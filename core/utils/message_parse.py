import asyncio
import re
from collections import defaultdict
from datetime import datetime

from xxhash import xxh3_64_hexdigest

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import (
    At,
    File,
    Image,
    Json,
    Plain,
    Record,
    Reply,
    Video,
)
from astrbot.core.message.components import BaseMessageComponent

from ..llm.call_llm import CallLLM
from ..database.data_cache import DataCache, is_temp_or_local_path
from .http_manager import HttpManager
from .schemas import MessageData
from ..llm.xml_parse import MediaCaption

# 支持的图片文件格式
SUPPORTED_FILE_FORMATS_WITH_DOT = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".mpo",
)


class MessageParser:
    def __init__(
        self,
        data_cache: DataCache,
        http_manager: HttpManager,
        image_caption_enabled: bool,
        audio_caption_enabled: bool,
        call_llm: CallLLM,
    ):
        self.data_cache = data_cache
        self.http_manager = http_manager
        self.image_caption_enabled = image_caption_enabled
        self.audio_caption_enabled = audio_caption_enabled
        self.call_llm = call_llm
        # 异步锁，防止多机器人场景重复解析媒体信息
        self.url_locks = defaultdict(asyncio.Lock)
        self.hash_locks = defaultdict(asyncio.Lock)

    async def parse_user_message(
        self, event: AstrMessageEvent, bot_name: str, defer_caption: bool = False
    ) -> tuple[MessageData, list[str], list[str]]:
        """解析用户发送的消息"""
        # 获取时间
        iso_string = datetime.fromtimestamp(event.message_obj.timestamp).isoformat()
        # 获取消息内容
        msg, media_id_list = await self.chain_to_str(
            event.get_messages(), defer_caption
        )
        group_or_user_id = event.get_group_id() or event.get_sender_id()
        # 提取消息中的图片url
        image_urls = []
        audio_urls = []
        for comp in event.get_messages():
            if isinstance(comp, Reply) and comp.chain:
                for quote in comp.chain:
                    if isinstance(quote, Image) and quote.url:
                        image_urls.append(quote.url)
                    elif isinstance(quote, Record) and quote.url:
                        audio_urls.append(quote.url)
                    # 图片文件（astr的大模型请求接口似乎没有pdf的类型，这里只支持图片）
                    elif (
                        isinstance(quote, File)
                        and quote.url
                        and quote.url.startswith("http")
                        and (
                            quote.url.lower().endswith(SUPPORTED_FILE_FORMATS_WITH_DOT)
                            or quote.name
                            and quote.name.lower().endswith(
                                SUPPORTED_FILE_FORMATS_WITH_DOT
                            )
                        )
                    ):
                        image_urls.append(quote.url)
            elif isinstance(comp, Image) and comp.url:
                image_urls.append(comp.url)
            elif isinstance(comp, Record) and comp.url:
                audio_urls.append(comp.url)
            # 图片文件（astr的大模型请求接口似乎没有pdf的类型，这里只支持图片）
            elif (
                isinstance(comp, File)
                and comp.url
                and comp.url.startswith("http")
                and (
                    comp.url.lower().endswith(SUPPORTED_FILE_FORMATS_WITH_DOT)
                    or comp.name
                    and comp.name.lower().endswith(SUPPORTED_FILE_FORMATS_WITH_DOT)
                )
            ):
                image_urls.append(comp.url)
        msg_data = MessageData(
            nickname=event.get_sender_name(),
            user_id=event.get_sender_id(),
            group_or_user_id=group_or_user_id,
            time=iso_string,
            message_id=event.message_obj.message_id,
            content=msg,
            is_recalled=0,
            media_id_list=media_id_list,
        )
        # 将消息写入缓存
        await self.data_cache.add_message(bot_name, group_or_user_id, msg_data)
        return msg_data, image_urls, audio_urls

    async def chain_to_str(
        self, chain: list[BaseMessageComponent], defer_caption: bool = False
    ) -> tuple[str, list[str]]:
        """将消息链转换为字符串，用于接收用户消息时转换使用"""
        msg_parts = []
        media_id_list = []
        for comp in chain:
            if isinstance(comp, Plain):
                msg_parts.append(comp.text)
            elif isinstance(comp, Reply):
                # 引用消息文本
                quote_text = ""
                if comp.chain:
                    quote_parts = []
                    for quote in comp.chain:
                        if isinstance(quote, Plain):
                            quote_parts.append(quote.text)
                        elif isinstance(quote, At):
                            quote_parts.append(f"<@{quote.name}({quote.qq})>")
                        elif isinstance(quote, Image):
                            file_name = quote.file
                            media_caption = None
                            hash_val = None

                            if file_name and not is_temp_or_local_path(file_name):
                                (
                                    hash_val,
                                    media_caption,
                                ) = await self.data_cache.get_caption_by_filename(
                                    file_name
                                )

                            if not media_caption and quote.url:
                                hash_val, media_caption = await self._get_image_caption(
                                    quote.url, file_name, defer_caption
                                )
                            if hash_val and media_caption:
                                quote_parts.append(f"[图片:{hash_val}]")
                                media_id_list.append(hash_val)
                                # 写入缓存
                                await self.data_cache.set_caption(media_caption)
                                continue
                            # 无法获取图片描述，使用默认值
                            quote_parts.append("[图片]")
                        elif isinstance(quote, Record):
                            file_name = quote.file
                            media_caption = None
                            hash_val = None

                            if (
                                file_name
                                and self.audio_caption_enabled
                                and not is_temp_or_local_path(file_name)
                            ):
                                (
                                    hash_val,
                                    media_caption,
                                ) = await self.data_cache.get_caption_by_filename(
                                    file_name
                                )

                            if (
                                not media_caption
                                and quote.url
                                and self.audio_caption_enabled
                            ):
                                hash_val, media_caption = await self._get_audio_caption(
                                    quote.url, file_name, defer_caption
                                )
                            if hash_val and media_caption:
                                quote_parts.append(f"[语音:{hash_val}]")
                                media_id_list.append(hash_val)
                                # 写入缓存
                                await self.data_cache.set_caption(media_caption)
                                continue
                            # 无法获取语音描述，使用默认值
                            quote_parts.append("[语音]")
                        elif isinstance(quote, Video):
                            quote_parts.append("[视频]")
                        elif isinstance(quote, Json):
                            quote_parts.append("[合并转发消息]")
                        elif isinstance(quote, File):
                            quote_parts.append(f"[文件:{quote.name}]")
                    quote_text = " ".join(quote_parts)
                msg_parts.append(
                    f"<quote message_id={comp.id} sender_id={comp.sender_id} sender_name={comp.sender_nickname}>{quote_text}</quote>"
                )
            elif isinstance(comp, At):
                msg_parts.append(f"<@{comp.name}({comp.qq})>")
            elif isinstance(comp, Image):
                file_name = comp.file
                media_caption = None
                hash_val = None

                if file_name and not is_temp_or_local_path(file_name):
                    (
                        hash_val,
                        media_caption,
                    ) = await self.data_cache.get_caption_by_filename(file_name)

                if not media_caption and comp.url:
                    hash_val, media_caption = await self._get_image_caption(
                        comp.url, file_name, defer_caption
                    )
                if hash_val and media_caption:
                    msg_parts.append(f"[图片:{hash_val}]")
                    media_id_list.append(hash_val)
                    # 写入缓存
                    await self.data_cache.set_caption(media_caption)
                    continue
                # 无法获取图片描述，使用默认值
                msg_parts.append("[图片]")
            # 语音消息
            elif isinstance(comp, Record):
                file_name = comp.file
                media_caption = None
                hash_val = None

                if (
                    file_name
                    and self.audio_caption_enabled
                    and not is_temp_or_local_path(file_name)
                ):
                    (
                        hash_val,
                        media_caption,
                    ) = await self.data_cache.get_caption_by_filename(file_name)

                if not media_caption and comp.url and self.audio_caption_enabled:
                    hash_val, media_caption = await self._get_audio_caption(
                        comp.url, file_name, defer_caption
                    )
                if hash_val and media_caption:
                    msg_parts.append(f"[语音:{hash_val}]")
                    media_id_list.append(hash_val)
                    # 写入缓存
                    await self.data_cache.set_caption(media_caption)
                    continue
                # 无法获取语音描述，使用默认值
                msg_parts.append("[语音]")
            elif isinstance(comp, Video):
                # 暂不支持视频转述，考虑用工具异步支持
                msg_parts.append("[视频]")
            elif isinstance(comp, Json):
                # 暂不支持合并转发消息转述，考虑用工具异步支持
                msg_parts.append("[合并转发消息]")
            elif isinstance(comp, File):
                # 暂不支持文件转述
                msg_parts.append(f"[文件:{comp.name}]")
        return " ".join(msg_parts), media_id_list

    async def _get_image_caption(
        self, url: str, file_name: str | None = None, defer_caption: bool = False
    ) -> tuple[str | None, MediaCaption | None]:
        """获取图片描述"""
        async with self.url_locks[url]:
            if file_name and not is_temp_or_local_path(file_name):
                # 检查缓存
                hash_val, media_caption = await self.data_cache.get_caption_by_filename(
                    file_name
                )
                if hash_val and media_caption:
                    if getattr(media_caption, "is_captioned", True) or defer_caption:
                        return hash_val, media_caption

            # Try to extract a stable MD5 hash from file_name as the stable identifier.
            # Only file_name is used — URLs are intentionally excluded because they often
            # contain shared parameters (e.g. rkey, session tokens) that look like 32-char
            # hex strings but are identical across different images, causing false cache hits.
            stable_hash = None

            if file_name and not is_temp_or_local_path(file_name):
                # Require the 32-char hex to be the entire stem of the filename (e.g.
                # "ABCDEF...1234.image" -> stem "ABCDEF...1234"), not a partial match.
                stem = re.sub(r"\.[^.]+$", "", file_name)  # strip extension
                if re.fullmatch(r"[a-fA-F0-9]{32}", stem):
                    stable_hash = stem.lower()

            if stable_hash:
                media_caption = await self.data_cache.get_caption_by_hash(stable_hash)
                if media_caption:
                    media_caption.url = url
                    if file_name:
                        media_caption.file_name = file_name
                    if getattr(media_caption, "is_captioned", True) or defer_caption:
                        await self.data_cache.set_caption(media_caption)
                        return stable_hash, media_caption

            # 下载图片
            image_bytes = await self.http_manager.download_media(url)
            if not image_bytes:
                return None, None
            # 生成hash
            hash_val = stable_hash or xxh3_64_hexdigest(image_bytes)

            # 保存到本地持久缓存目录，以便网页端可以永久预览
            try:
                from astrbot.core.star.star_tools import StarTools

                cache_dir = (
                    StarTools.get_data_dir("astrbot_plugin_giftia") / "media_cache"
                )
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_file = cache_dir / hash_val
                if not cache_file.exists():
                    cache_file.write_bytes(image_bytes)
            except Exception as e:
                logger.error(f"[Giftia] 保存媒体缓存失败: {e}")

        async with self.hash_locks[hash_val]:
            # 检查缓存
            media_caption = await self.data_cache.get_caption_by_hash(hash_val)
            if media_caption:
                media_caption.url = url
                if file_name:
                    media_caption.file_name = file_name
                if getattr(media_caption, "is_captioned", True) or defer_caption:
                    await self.data_cache.set_caption(media_caption)
                    return hash_val, media_caption

            # 如果开启了延迟，或者未开启转述，直接返回一个仅包含url和hash的基础对象
            if defer_caption:
                media_caption = MediaCaption(
                    hash_val=hash_val,
                    url=url,
                    media_type="image",
                    is_captioned=False,
                )
                if file_name:
                    media_caption.file_name = file_name
                await self.data_cache.set_caption(media_caption)
                return hash_val, media_caption

            if not self.image_caption_enabled:
                media_caption = MediaCaption(
                    hash_val=hash_val,
                    url=url,
                    media_type="image",
                    is_captioned=True,
                )
                if file_name:
                    media_caption.file_name = file_name
                await self.data_cache.set_caption(media_caption)
                return hash_val, media_caption

            # 处理图片
            base64s, is_animated = await asyncio.to_thread(
                self.http_manager.handle_image, image_bytes
            )
            if not base64s:
                media_caption = MediaCaption(
                    hash_val=hash_val,
                    url=url,
                    media_type="image",
                    is_captioned=True,
                )
                if file_name:
                    media_caption.file_name = file_name
                await self.data_cache.set_caption(media_caption)
                return hash_val, media_caption
            # Log key identifiers so we can detect if two different URLs produce the
            # same image content (which would indicate a stale-temp-file read).
            logger.info(
                f"[Giftia] 调用LLM转述图片: hash={hash_val} "
                f"size={len(image_bytes)}B "
                f"head={image_bytes[:8].hex()} "
                f"url={url!r}"
            )
            # 调用LLM生成图片描述
            media_caption = await self.call_llm.call_llm_image_caption(base64s)
            if not media_caption:
                media_caption = MediaCaption(
                    hash_val=hash_val,
                    url=url,
                    media_type="image",
                    is_captioned=True,
                )
                if file_name:
                    media_caption.file_name = file_name
                await self.data_cache.set_caption(media_caption)
                return hash_val, media_caption
            media_caption.hash_val = hash_val
            media_caption.url = url
            if file_name:
                media_caption.file_name = file_name
            media_caption.media_type = "image"
            media_caption.is_captioned = True
            # 缓存
            await self.data_cache.set_caption(media_caption)
            return hash_val, media_caption

    async def _get_audio_caption(
        self, url: str, file_name: str | None = None, defer_caption: bool = False
    ) -> tuple[str | None, MediaCaption | None]:
        """获取语音描述"""
        async with self.url_locks[url]:
            if file_name and not is_temp_or_local_path(file_name):
                # 检查缓存
                hash_val, media_caption = await self.data_cache.get_caption_by_filename(
                    file_name
                )
                if hash_val and media_caption:
                    if getattr(media_caption, "is_captioned", True) or defer_caption:
                        return hash_val, media_caption

            # 语音的hash_val用url生成
            hash_val = xxh3_64_hexdigest(url.encode())

            # 检查缓存
            media_caption = await self.data_cache.get_caption_by_hash(hash_val)
            if media_caption:
                media_caption.url = url
                if file_name:
                    media_caption.file_name = file_name
                if getattr(media_caption, "is_captioned", True) or defer_caption:
                    await self.data_cache.set_caption(media_caption)
                    return hash_val, media_caption

            # 下载并保存语音文件，以便永久播放
            audio_bytes = None
            try:
                audio_bytes = await self.http_manager.download_media(url)
                if audio_bytes:
                    from astrbot.core.star.star_tools import StarTools

                    cache_dir = (
                        StarTools.get_data_dir("astrbot_plugin_giftia") / "media_cache"
                    )
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    cache_file = cache_dir / hash_val
                    if not cache_file.exists():
                        cache_file.write_bytes(audio_bytes)
            except Exception as e:
                logger.error(f"[Giftia] 保存音频缓存失败: {e}")

            if defer_caption:
                media_caption = MediaCaption(
                    hash_val=hash_val,
                    url=url,
                    media_type="audio",
                    is_captioned=False,
                )
                if file_name:
                    media_caption.file_name = file_name
                await self.data_cache.set_caption(media_caption)
                return hash_val, media_caption

            # 调用LLM生成语音描述
            media_caption = await self.call_llm.call_llm_audio_caption([url])
            if not media_caption:
                # 即使LLM失败，也需要保存一个未转述的或者空的对象，但标记为 captioned=False 方便后续重试
                media_caption = MediaCaption(
                    hash_val=hash_val,
                    url=url,
                    media_type="audio",
                    is_captioned=False,
                )
                if file_name:
                    media_caption.file_name = file_name
                await self.data_cache.set_caption(media_caption)
                return hash_val, media_caption

            media_caption.hash_val = hash_val
            media_caption.url = url
            if file_name:
                media_caption.file_name = file_name
            media_caption.media_type = "audio"
            media_caption.is_captioned = True
            # 缓存
            await self.data_cache.set_caption(media_caption)
            return hash_val, media_caption
