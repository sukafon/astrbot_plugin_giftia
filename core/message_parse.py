import asyncio
from collections import defaultdict
from datetime import datetime

from xxhash import xxh3_64_hexdigest

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

from .call_llm import CallLLM
from .data_cache import DataCache
from .http_manager import HttpManager
from .schemas import MessageData
from .xml_parse import MediaCaption

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
        self.hash_val_locks = defaultdict(asyncio.Lock)

    async def parse_user_message(
        self, event: AstrMessageEvent, bot_name: str
    ) -> tuple[MessageData, list[str], list[str]]:
        """解析用户发送的消息"""
        # 获取时间
        iso_string = datetime.fromtimestamp(event.message_obj.timestamp).isoformat()
        # 获取消息内容
        msg, media_id_list = await self.chain_to_str(event.get_messages())
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
        self, chain: list[BaseMessageComponent]
    ) -> tuple[str, list[str]]:
        """将消息链转换为字符串"""
        msg_parts = []
        media_id_list = []
        for comp in chain:
            if isinstance(comp, Plain):
                msg_parts.append(comp.text)
            elif isinstance(comp, Reply):
                msg_parts.append(f" <quote:{comp.id}>")
            elif isinstance(comp, At):
                msg_parts.append(f" <@{comp.name}({comp.qq})>")
            elif isinstance(comp, Image):
                if comp.url and self.image_caption_enabled:
                    hash_val, media_caption = await self.data_cache.get_caption_by_url(
                        comp.url
                    )
                    if not hash_val:
                        hash_val, media_caption = await self._get_image_caption(
                            comp.url
                        )
                    if hash_val and media_caption:
                        msg_parts.append(f" [图片:{hash_val}]")
                        media_id_list.append(hash_val)
                        # 写入缓存
                        await self.data_cache.set_caption(media_caption)
                        continue
                # 无法获取图片描述，使用默认值
                msg_parts.append(" [图片]")
            # 语音消息
            elif isinstance(comp, Record):
                if comp.url and self.audio_caption_enabled:
                    # 考虑到统一性，语音也写入缓存
                    hash_val, media_caption = await self.data_cache.get_caption_by_url(
                        comp.url
                    )
                    if not hash_val:
                        hash_val, media_caption = await self._get_audio_caption(
                            comp.url
                        )
                    if hash_val and media_caption:
                        msg_parts.append(f" [语音:{hash_val}]")
                        media_id_list.append(hash_val)
                        # 写入缓存
                        await self.data_cache.set_caption(media_caption)
                        continue
                # 无法获取语音描述，使用默认值
                msg_parts.append(" [语音]")
            elif isinstance(comp, Video):
                # 暂不支持视频转述，考虑用工具异步支持
                msg_parts.append(" [视频]")
            elif isinstance(comp, Json):
                # 暂不支持合并转发消息转述，考虑用工具异步支持
                msg_parts.append(" [合并转发消息]")
            elif isinstance(comp, File):
                # 暂不支持文件转述
                msg_parts.append(f" [文件:{comp.name}]")
        return "".join(msg_parts), media_id_list

    async def _get_image_caption(
        self, url: str
    ) -> tuple[str | None, MediaCaption | None]:
        """获取图片描述"""
        async with self.hash_val_locks[url]:
            # 检查缓存
            hash_val, media_caption = await self.data_cache.get_caption_by_url(url)
            if hash_val and media_caption:
                return hash_val, media_caption
            # 下载图片
            image_bytes = await self.http_manager.download_media(url)
            if not image_bytes:
                return None, None
            # 生成hash
            hash_val = xxh3_64_hexdigest(image_bytes)
            # 检查缓存
            media_caption = await self.data_cache.get_caption_by_hash(hash_val)
            if media_caption:
                media_caption.url = url
                await self.data_cache.set_caption(media_caption)
                return hash_val, media_caption
            # 处理图片
            base64s, is_animated = await asyncio.to_thread(
                self.http_manager.handle_image, image_bytes
            )
            if not base64s:
                return None, None
            # 调用LLM生成图片描述
            media_caption = await self.call_llm.call_llm_image_caption(base64s)
            if not media_caption:
                return None, None
            media_caption.hash_val = hash_val
            media_caption.url = url
            # 缓存
            await self.data_cache.set_caption(media_caption)
            return hash_val, media_caption

    async def _get_audio_caption(
        self, url: str
    ) -> tuple[str | None, MediaCaption | None]:
        """获取语音描述"""
        async with self.hash_val_locks[url]:
            # 检查缓存
            hash_val, media_caption = await self.data_cache.get_caption_by_url(url)
            if hash_val and media_caption:
                return hash_val, media_caption
            # 调用LLM生成语音描述
            media_caption = await self.call_llm.call_llm_audio_caption([url])
            if not media_caption:
                return None, None
            # 语音的hash_val直接用url生成吧
            hash_val = xxh3_64_hexdigest(url.encode())
            media_caption.hash_val = hash_val
            media_caption.url = url
            # 缓存
            await self.data_cache.set_caption(media_caption)
            return hash_val, media_caption
