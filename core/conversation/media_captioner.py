import os
import base64
from pathlib import Path
import asyncio
import copy
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Reply
from astrbot.core.star.star_tools import StarTools

from ..utils.schemas import MediaCaption, XmlLlmResult, extract_media_ids
from ..utils.video_utils import check_ffmpeg_available, clip_video_ffmpeg, format_duration, format_file_size


class MediaCaptioner:
    def __init__(self, plugin):
        self.plugin = plugin

    @staticmethod
    def _caption_enabled(media_caption: MediaCaption, caption_config: dict) -> bool:
        media_type = str(getattr(media_caption, "media_type", "") or "").lower()
        if media_type == "audio":
            return bool(caption_config.get("audio_caption_enabled", True))
        elif media_type == "video":
            # 视频不触发全局被动/延迟自动转述，仅在 Bot 主动调用 inspect_video 工具时按需切片转述
            return False
        return bool(caption_config.get("image_caption_enabled", True))

    async def transcribe_media_if_deferred(
        self, bot_name: str, recent_messages: list, caption_config: dict, group_or_user_id: str = ""
    ) -> list[MediaCaption]:
        """
        根据近期消息中的媒体ID，如果未转述，进行懒加载转述，并缓存。
        """
        # 先取所有消息的media_id，按从新到旧的顺序去重获取，确保越新的媒体越优先转述
        hash_vals = []
        seen_media = set()
        for msg in reversed(recent_messages):
            content_media_ids = extract_media_ids(getattr(msg, "content", "") or "")
            for media_id in reversed(content_media_ids):
                if media_id not in seen_media:
                    seen_media.add(media_id)
                    hash_vals.append(media_id)

        try:
            max_deferred = int(caption_config.get("max_deferred_captions", 5))
        except (TypeError, ValueError):
            max_deferred = 5
        max_deferred = max(0, max_deferred)
        deferred_count = 0

        media_captions: list[MediaCaption] = []
        for hash_val in hash_vals:
            media_caption = await self.plugin.data_cache.get_caption_by_hash(hash_val)
            if media_caption:
                if not self._caption_enabled(media_caption, caption_config):
                    continue
                # If the media caption has not been transcribed yet, transcribe it now
                if not getattr(media_caption, "is_captioned", True):
                    if deferred_count < max_deferred:
                        deferred_count += 1
                        logger.info(
                            f"[Giftia] 延迟转述触发: hash={hash_val}, type={media_caption.media_type}"
                        )
                        try:
                            cache_file = (
                                StarTools.get_data_dir("astrbot_plugin_giftia")
                                / "media_cache"
                                / hash_val
                            )
                            if media_caption.media_type == "audio":
                                audio_urls = (
                                    [str(cache_file)]
                                    if cache_file.exists()
                                    else [media_caption.url]
                                )
                                if audio_urls and audio_urls[0]:
                                    transcribed = await self.plugin.call_llm.call_llm_audio_caption(
                                        audio_urls,
                                        bot_name=bot_name,
                                        group_or_user_id=group_or_user_id,
                                    )
                                    if transcribed:
                                        media_caption.genre = transcribed.genre
                                        media_caption.character = transcribed.character
                                        media_caption.source = transcribed.source
                                        media_caption.text = transcribed.text
                                        media_caption.caption = transcribed.caption
                                        media_caption.is_captioned = True
                                        await self.plugin.data_cache.update_caption(
                                            media_caption
                                        )
                            else:  # image media
                                image_bytes = None
                                if cache_file.exists():
                                    try:
                                        image_bytes = cache_file.read_bytes()
                                    except Exception as e:
                                        logger.error(f"[Giftia] 读取图片缓存失败: {e}")
                                if not image_bytes and media_caption.url:
                                    image_bytes = (
                                        await self.plugin.http_manager.download_media(
                                            media_caption.url
                                        )
                                    )
                                if image_bytes:
                                    base64s, is_animated = await asyncio.to_thread(
                                        self.plugin.http_manager.handle_image,
                                        image_bytes,
                                    )
                                    if base64s:
                                        transcribed = await self.plugin.call_llm.call_llm_image_caption(
                                            base64s,
                                            bot_name=bot_name,
                                            group_or_user_id=group_or_user_id,
                                        )
                                        if transcribed:
                                            media_caption.genre = transcribed.genre
                                            media_caption.character = (
                                                transcribed.character
                                            )
                                            media_caption.source = transcribed.source
                                            media_caption.text = transcribed.text
                                            media_caption.caption = transcribed.caption
                                            media_caption.is_captioned = True
                                            await self.plugin.data_cache.update_caption(
                                                media_caption
                                            )
                        except Exception as e:
                            logger.error(
                                f"[Giftia] 延迟转述处理失败: {e}", exc_info=True
                            )

                if await self.plugin.emoji_manager.has_sticker(bot_name, hash_val):
                    media_caption = copy.copy(media_caption)
                    media_caption.caption += " (你已收藏此表情包)"
                media_captions.append(media_caption)

        return media_captions

    async def analyze_and_add_stickers(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        nickname: str,
        group_or_user_id: str,
        llm_result: XmlLlmResult,
    ):
        """
        分析并后台添加表情包
        """
        if not llm_result.add_stickers:
            return

        categories = await self.plugin.db.get_sticker_categories()
        for sticker_id in llm_result.add_stickers:
            async with self.plugin.sticker_locks[sticker_id]:
                # 先检查有没有添加过，如果全局有过，就直接关联而无需再次消耗Token分析
                if sticker_id in self.plugin.emoji_manager.stickers:
                    await self.plugin.emoji_manager.add_sticker(
                        bot_name=bot_name, media_id=sticker_id
                    )
                    continue

                caption = await self.plugin.data_cache.get_caption_by_hash(sticker_id)
                is_useful, sticker = False, None

                target_url = None
                for comp in event.get_messages():
                    if isinstance(comp, Reply) and comp.chain:
                        for quote in comp.chain:
                            if isinstance(quote, Image) and quote.url:
                                if quote.file and sticker_id in quote.file.lower():
                                    target_url = quote.url
                                    break
                                elif quote.file:
                                    (
                                        quote_hash,
                                        _,
                                    ) = await self.plugin.data_cache.get_caption_by_filename(
                                        quote.file
                                    )
                                    if quote_hash == sticker_id:
                                        target_url = quote.url
                                        break
                        if target_url:
                            break
                if not target_url and caption and caption.url:
                    target_url = caption.url

                if target_url:
                    # 先将图片下载并转为 base64，防止大模型无法访问本地/内网 URL
                    image_bytes = await self.plugin.http_manager.download_media(
                        target_url
                    )
                    if image_bytes:
                        base64s, _ = await asyncio.to_thread(
                            self.plugin.http_manager.handle_image, image_bytes
                        )
                        if base64s:
                            (
                                is_useful,
                                sticker,
                            ) = await self.plugin.call_llm.call_llm_sticker_analysis(
                                image_urls=base64s,
                                categories=categories,
                                media_id=sticker_id,
                                bot_name=bot_name,
                                group_or_user_id=group_or_user_id,
                            )
                        # 如果判定为有用，则下载保存到本地
                        if is_useful and sticker:
                            local_path = (
                                await self.plugin.emoji_manager.save_sticker_image(
                                    image_bytes, sticker_id
                                )
                            )
                            sticker.filename = local_path.name

                if is_useful and sticker:
                    await self.plugin.emoji_manager.add_sticker(
                        bot_name=bot_name, media_id=sticker_id, sticker=sticker
                    )

    async def retranscribe_media_with_question(
        self, bot_name: str, hash_val: str, question: str, group_or_user_id: str = ""
    ) -> MediaCaption | None:
        """
        强制针对给定的 media_id (hash_val) 和额外关注的问题，进行重新转述，并更新缓存与数据库。
        """
        media_caption = await self.plugin.data_cache.get_caption_by_hash(hash_val)
        if not media_caption:
            logger.warning(f"[Giftia] 重新转述失败：未找到对应的媒体缓存 hash={hash_val}")
            return None

        logger.info(
            f"[Giftia] 重新转述处理 (bot_name={bot_name}): hash={hash_val}, type={media_caption.media_type}, question={question}"
        )
        try:
            cache_file = (
                StarTools.get_data_dir("astrbot_plugin_giftia")
                / "media_cache"
                / hash_val
            )
            if media_caption.media_type == "audio":
                audio_urls = (
                    [str(cache_file)]
                    if cache_file.exists()
                    else [media_caption.url]
                )
                if audio_urls and audio_urls[0]:
                    transcribed = await self.plugin.call_llm.call_llm_audio_caption(
                        audio_urls, question=question, bot_name=bot_name, group_or_user_id=group_or_user_id
                    )
                    if transcribed:
                        media_caption.genre = transcribed.genre
                        media_caption.character = transcribed.character
                        media_caption.source = transcribed.source
                        media_caption.text = transcribed.text
                        media_caption.caption = transcribed.caption
                        media_caption.is_captioned = True
                        await self.plugin.data_cache.update_caption(media_caption)
                        return media_caption
            elif media_caption.media_type == "video":
                caption_text = await self.transcribe_video_media(
                    media_caption,
                    start_time=0,
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                )
                media_caption.caption = caption_text
                return media_caption
            elif media_caption.media_type == "image":  # image media
                image_bytes = None
                if cache_file.exists():
                    try:
                        image_bytes = cache_file.read_bytes()
                    except Exception as e:
                        logger.error(f"[Giftia] 读取图片缓存失败: {e}")
                if not image_bytes and media_caption.url:
                    image_bytes = (
                        await self.plugin.http_manager.download_media(
                            media_caption.url
                        )
                    )
                if image_bytes:
                    base64s, is_animated = await asyncio.to_thread(
                        self.plugin.http_manager.handle_image,
                        image_bytes,
                    )
                    if base64s:
                        transcribed = await self.plugin.call_llm.call_llm_image_caption(
                            base64s, question=question, bot_name=bot_name, group_or_user_id=group_or_user_id
                        )
                        if transcribed:
                            media_caption.genre = transcribed.genre
                            media_caption.character = transcribed.character
                            media_caption.source = transcribed.source
                            media_caption.text = transcribed.text
                            media_caption.caption = transcribed.caption
                            media_caption.is_captioned = True
                            await self.plugin.data_cache.update_caption(media_caption)
                            return media_caption
        except Exception as e:
            logger.error(f"[Giftia] 重新转述处理失败: {e}", exc_info=True)
            raise e
        return None

    async def transcribe_video_media(
        self,
        media_caption: MediaCaption,
        start_time: int = 0,
        question: str = "",
        bot_name: str = "",
        group_or_user_id: str = "",
    ) -> str:
        """对视频进行缓存、切片并调用 LLM 进行转述理解"""
        caption_config = getattr(self.plugin, "conf", {}).get("caption_config", {})
        threshold = int(caption_config.get("video_clip_threshold_seconds", 30))

        url = media_caption.url or media_caption.file_name
        if not url:
            return "[视频文件路径或URL无效]"

        cache_dir = StarTools.get_data_dir("astrbot_plugin_giftia") / "media_cache"
        os.makedirs(cache_dir, exist_ok=True)
        local_video_path = cache_dir / f"{media_caption.hash_val}.mp4"

        if not local_video_path.exists():
            if url.startswith("file://"):
                local_video_path = Path(url.replace("file://", ""))
            elif url.startswith("http://") or url.startswith("https://"):
                logger.info(f"[Giftia] 下载视频文件进行分析: hash={media_caption.hash_val}")
                video_bytes = await self.plugin.http_manager.download_media(url)
                if not video_bytes:
                    return "[视频下载失败]"
                local_video_path.write_bytes(video_bytes)
            elif os.path.exists(url):
                local_video_path = Path(url)

        if not local_video_path.exists():
            return "[本地视频文件不存在]"

        duration = media_caption.duration or 0.0
        target_video_file = str(local_video_path)
        clip_info_str = ""

        if duration > threshold:
            clip_output = cache_dir / f"{media_caption.hash_val}_clip_{start_time}_{threshold}.mp4"
            if not clip_output.exists():
                success = await clip_video_ffmpeg(
                    str(local_video_path),
                    start_time=start_time,
                    duration=threshold,
                    output_path=str(clip_output)
                )
                if success:
                    target_video_file = str(clip_output)
                    clip_info_str = f" (切片区间: {start_time}s ~ {start_time + threshold}s)"
            else:
                target_video_file = str(clip_output)
                clip_info_str = f" (切片区间: {start_time}s ~ {start_time + threshold}s)"

        # 拦截保护：检查目标视频/切片文件大小，防止超大视频爆内存
        max_size_mb = int(caption_config.get("video_max_file_size_mb", 50))
        max_size_bytes = max_size_mb * 1024 * 1024
        if os.path.exists(target_video_file):
            actual_size = os.path.getsize(target_video_file)
            if actual_size > max_size_bytes:
                logger.warning(
                    f"[Giftia] 视频文件 ({format_file_size(actual_size)}) 超出转述限制 ({max_size_mb}MB)，取消读取"
                )
                return f"[视频文件体积 ({format_file_size(actual_size)}) 超过系统转述限制 ({max_size_mb}MB)，无法读取转述]"

        # 强抓原生视频全量字节包转 Base64 (data:video/mp4;base64,...)
        try:
            video_raw_bytes = Path(target_video_file).read_bytes()
            video_b64_str = base64.b64encode(video_raw_bytes).decode("utf-8")
            video_data_url = f"data:video/mp4;base64,{video_b64_str}"
        except Exception as e:
            logger.error(f"[Giftia] 视频文件读取或编码 Base64 失败: {e}")
            return f"[视频编码失败: {e}]"

        try:
            transcribed = await self.plugin.call_llm.call_llm_video_caption(
                video_url=video_data_url,
                question=question,
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
            )
            if transcribed:
                caption_text = f"{transcribed.caption}{clip_info_str}"
                media_caption.genre = transcribed.genre
                media_caption.character = transcribed.character
                media_caption.source = transcribed.source
                media_caption.text = transcribed.text
                media_caption.caption = caption_text
                media_caption.is_captioned = True
                await self.plugin.data_cache.update_caption(media_caption)
                return caption_text
        except Exception as e:
            logger.error(f"[Giftia] 原生视频转述 LLM 调用失败: {e}", exc_info=True)
            return f"[视频转述失败: {e}]"

        return "[视频解析未生成有效结论]"
