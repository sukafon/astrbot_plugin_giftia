import asyncio
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from xxhash import xxh3_64_hexdigest

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import (
    At,
    File,
    Forward,
    Image,
    Json,
    Node,
    Nodes,
    Plain,
    Poke,
    Record,
    Reply,
    Video,
)
from astrbot.core.message.components import BaseMessageComponent

from ..database.data_cache import DataCache, is_temp_or_local_path
from ..llm.call_llm import CallLLM
from .http_manager import HttpManager
from .schemas import MediaCaption, MessageData

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

MAX_FORWARD_FETCH = 5
MAX_FORWARD_NODE_DEPTH = 4
MAX_FORWARD_NODE_COUNT = 80


@dataclass
class ChainParseResult:
    content: str = ""
    media_id_list: list[str] = field(default_factory=list)
    forward_messages: list[dict] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    audio_urls: list[str] = field(default_factory=list)

    def merge(self, other: "ChainParseResult", include_content: bool = False) -> None:
        if include_content and other.content:
            self.content = f"{self.content} {other.content}".strip()
        self.media_id_list.extend(other.media_id_list)
        self.forward_messages.extend(other.forward_messages)
        self.image_urls.extend(other.image_urls)
        self.audio_urls.extend(other.audio_urls)


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

    async def _resolve_user_name(
        self,
        event: AstrMessageEvent,
        user_id: str,
        current_name: str | None,
        force_lookup: bool = False,
    ) -> str:
        """Resolve a readable nickname for notice events that only carry user_id."""
        user_id = str(user_id or "").strip()
        current_name = str(current_name or "").strip()
        if current_name and current_name != user_id and not force_lookup:
            return current_name
        if not user_id:
            return current_name

        group_id = str(event.get_group_id() or "").strip()
        bot = getattr(event, "bot", None)
        routing_params = {}
        self_id = str(event.get_self_id() or "").strip()
        if self_id:
            routing_params["self_id"] = self_id

        if bot and hasattr(bot, "call_action"):
            try:
                query_user_id = int(user_id) if user_id.isdigit() else user_id
                if group_id:
                    query_group_id = int(group_id) if group_id.isdigit() else group_id
                    info = await bot.call_action(
                        action="get_group_member_info",
                        group_id=query_group_id,
                        user_id=query_user_id,
                        no_cache=False,
                        **routing_params,
                    )
                    nickname = (
                        info.get("card")
                        or info.get("nickname")
                        or info.get("nick")
                    )
                    if nickname:
                        return str(nickname)
                info = await bot.call_action(
                    action="get_stranger_info",
                    user_id=query_user_id,
                    no_cache=False,
                    **routing_params,
                )
                nickname = info.get("nick") or info.get("nickname")
                if nickname:
                    return str(nickname)
            except Exception as e:
                logger.debug(f"[Giftia] 解析用户昵称失败: {e}")

        if group_id:
            try:
                group = await event.get_group(group_id)
                for member in getattr(group, "members", []) or []:
                    if str(getattr(member, "user_id", "")) == user_id:
                        nickname = getattr(member, "nickname", "")
                        if nickname:
                            return str(nickname)
            except Exception as e:
                logger.debug(f"[Giftia] 从群成员列表解析昵称失败: {e}")

        return current_name or user_id

    @staticmethod
    def _get_poke_target_id(comp: Poke) -> str:
        target_id = (
            comp.target_id()
            if hasattr(comp, "target_id")
            else getattr(comp, "id", None) or getattr(comp, "qq", None)
        )
        return str(target_id or "").strip()

    @staticmethod
    def _format_user_ref(name: str, user_id: str) -> str:
        name = str(name or "").strip()
        user_id = str(user_id or "").strip()
        if name and user_id and name != user_id:
            return f"{name}({user_id})"
        return user_id or name or "未知用户"

    async def _format_poke_message(
        self,
        event: AstrMessageEvent,
        chain: list[BaseMessageComponent],
    ) -> str:
        parts = []
        for comp in chain:
            if not isinstance(comp, Poke):
                continue
            target_id = self._get_poke_target_id(comp)
            target_name = await self._resolve_user_name(
                event=event,
                user_id=target_id,
                current_name=target_id,
                force_lookup=True,
            )
            target_ref = self._format_user_ref(target_name, target_id)
            parts.append(f"[戳一戳:{target_ref}]")
        return " ".join(parts)

    async def parse_user_message(
        self, event: AstrMessageEvent, bot_name: str, defer_caption: bool = False
    ) -> tuple[MessageData, list[str], list[str]]:
        """解析用户发送的消息"""
        # 获取时间
        iso_string = datetime.fromtimestamp(event.message_obj.timestamp).isoformat()
        # 获取消息内容
        parsed = await self.chain_to_result(
            event.get_messages(), defer_caption, event=event
        )
        msg = parsed.content
        media_id_list = parsed.media_id_list
        group_or_user_id = event.get_group_id() or event.get_sender_id()
        sender_id = event.get_sender_id()
        has_poke = any(isinstance(comp, Poke) for comp in event.get_messages())
        sender_name = await self._resolve_user_name(
            event=event,
            user_id=sender_id,
            current_name=event.get_sender_name(),
            force_lookup=has_poke,
        )
        if has_poke:
            poke_msg = await self._format_poke_message(
                event=event,
                chain=event.get_messages(),
            )
            if poke_msg:
                msg = poke_msg
        msg_data = MessageData(
            nickname=sender_name,
            user_id=sender_id,
            group_or_user_id=group_or_user_id,
            time=iso_string,
            message_id=event.message_obj.message_id,
            content=msg,
            is_recalled=0,
            media_id_list=media_id_list,
            forward_messages=parsed.forward_messages,
        )
        # 将消息写入缓存
        await self.data_cache.add_message(bot_name, group_or_user_id, msg_data)
        return msg_data, parsed.image_urls, parsed.audio_urls

    async def chain_to_str(
        self, chain: list[BaseMessageComponent], defer_caption: bool = False
    ) -> tuple[str, list[str]]:
        """将消息链转换为字符串，用于接收用户消息时转换使用"""
        parsed = await self.chain_to_result(chain, defer_caption)
        return parsed.content, parsed.media_id_list

    async def chain_to_result(
        self,
        chain: list[BaseMessageComponent],
        defer_caption: bool = False,
        event: AstrMessageEvent | None = None,
        _forward_ctx: dict | None = None,
        _depth: int = 0,
    ) -> ChainParseResult:
        """将消息链转换为可入库的正文、媒体脚注和转发结构。"""
        if _forward_ctx is None:
            _forward_ctx = {
                "remote_refs": {},
                "fetch_count": 0,
                "fetching": set(),
            }

        msg_parts = []
        result = ChainParseResult()
        index = 0
        while index < len(chain):
            comp = chain[index]
            index += 1
            if isinstance(comp, Plain):
                msg_parts.append(comp.text)
            elif isinstance(comp, Reply):
                # 引用消息文本
                quote_text = ""
                if comp.chain:
                    quote_result = await self.chain_to_result(
                        comp.chain,
                        defer_caption=defer_caption,
                        event=event,
                        _forward_ctx=_forward_ctx,
                        _depth=_depth + 1,
                    )
                    result.merge(quote_result)
                    quote_text = quote_result.content
                msg_parts.append(
                    f"<quote message_id={comp.id} sender_id={comp.sender_id} sender_name={comp.sender_nickname}>{quote_text}</quote>"
                )
            elif isinstance(comp, At):
                msg_parts.append(f"<@{comp.name}({comp.qq})>")
            elif isinstance(comp, Image):
                custom_desc = getattr(comp, "meme_desc", None)
                part, media_result = await self._format_image_ref(
                    comp.url or "",
                    comp.file,
                    defer_caption,
                    custom_desc=custom_desc,
                )
                result.merge(media_result)
                msg_parts.append(part)
            # 语音消息
            elif isinstance(comp, Record):
                part, media_result = await self._format_audio_ref(
                    comp.url or "", comp.file, defer_caption
                )
                result.merge(media_result)
                msg_parts.append(part)
            elif isinstance(comp, Video):
                # 暂不支持视频转述，考虑用工具异步支持
                msg_parts.append("[视频]")
            elif isinstance(comp, Json):
                forward_result = await self._json_to_forward_result(
                    comp.data,
                    defer_caption=defer_caption,
                    event=event,
                    forward_ctx=_forward_ctx,
                    depth=_depth,
                )
                if forward_result:
                    result.merge(forward_result)
                    msg_parts.append(forward_result.content)
                else:
                    msg_parts.append("[合并转发消息]")
            elif isinstance(comp, File):
                # 暂不支持文件转述
                msg_parts.append(f"[文件:{comp.name}]")
            elif isinstance(comp, Poke):
                target_id = self._get_poke_target_id(comp)
                msg_parts.append(
                    f"[戳一戳:{target_id}]" if target_id else "[戳一戳]"
                )
            elif isinstance(comp, Node):
                nodes = [comp]
                while index < len(chain) and isinstance(chain[index], Node):
                    nodes.append(chain[index])
                    index += 1
                forward_result = await self._component_nodes_to_forward_result(
                    nodes,
                    defer_caption=defer_caption,
                    event=event,
                    forward_ctx=_forward_ctx,
                    depth=_depth,
                )
                result.merge(forward_result)
                msg_parts.append(forward_result.content)
            elif isinstance(comp, Nodes):
                forward_result = await self._component_nodes_to_forward_result(
                    comp.nodes or [],
                    defer_caption=defer_caption,
                    event=event,
                    forward_ctx=_forward_ctx,
                    depth=_depth,
                )
                result.merge(forward_result)
                msg_parts.append(forward_result.content)
            elif isinstance(comp, Forward):
                forward_result = await self._forward_id_to_result(
                    getattr(comp, "id", ""),
                    defer_caption=defer_caption,
                    event=event,
                    forward_ctx=_forward_ctx,
                    depth=_depth,
                )
                result.merge(forward_result)
                msg_parts.append(forward_result.content)
        result.content = " ".join(part for part in msg_parts if part).strip()
        return result

    @staticmethod
    def _make_forward_id(payload: dict) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return f"fwd_{xxh3_64_hexdigest(raw.encode())[:12]}"

    @staticmethod
    def _unwrap_action_response(payload) -> dict:
        if not isinstance(payload, dict):
            return {}
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload

    @staticmethod
    def _first_media_url(url: str | None, file_name: str | None) -> str:
        for candidate in (url, file_name):
            if not isinstance(candidate, str):
                continue
            candidate = candidate.strip()
            if candidate.startswith(("http://", "https://", "file://")):
                return candidate
        return ""

    async def _format_image_ref(
        self,
        url: str,
        file_name: str | None,
        defer_caption: bool,
        custom_desc: str | None = None,
    ) -> tuple[str, ChainParseResult]:
        result = ChainParseResult()
        decision_url = self._first_media_url(url, file_name)
        if decision_url:
            result.image_urls.append(decision_url)

        media_caption = None
        hash_val = None
        if file_name and not is_temp_or_local_path(file_name):
            hash_val, media_caption = await self.data_cache.get_caption_by_filename(
                file_name
            )

        should_try_caption = bool(
            url
            or (
                file_name
                and (
                    file_name.startswith("http")
                    or file_name.startswith("file://")
                    or file_name.startswith("base64://")
                )
            )
        )
        if not media_caption and should_try_caption:
            hash_val, media_caption = await self._get_image_caption(
                url or "", file_name, defer_caption, custom_desc=custom_desc
            )
        if hash_val and media_caption:
            result.media_id_list.append(hash_val)
            await self.data_cache.set_caption(media_caption)
            return f"[图片:{hash_val}]", result
        return "[图片]", result

    async def _format_audio_ref(
        self, url: str, file_name: str | None, defer_caption: bool
    ) -> tuple[str, ChainParseResult]:
        result = ChainParseResult()
        decision_url = self._first_media_url(url, file_name)
        if decision_url:
            result.audio_urls.append(decision_url)

        media_caption = None
        hash_val = None
        if (
            file_name
            and self.audio_caption_enabled
            and not is_temp_or_local_path(file_name)
        ):
            hash_val, media_caption = await self.data_cache.get_caption_by_filename(
                file_name
            )

        should_try_caption = bool(
            self.audio_caption_enabled
            and (
                url
                or (
                    file_name
                    and (
                        file_name.startswith("http")
                        or file_name.startswith("file://")
                    )
                )
            )
        )
        if not media_caption and should_try_caption:
            hash_val, media_caption = await self._get_audio_caption(
                url or "", file_name, defer_caption
            )
        if hash_val and media_caption:
            result.media_id_list.append(hash_val)
            await self.data_cache.set_caption(media_caption)
            return f"[语音:{hash_val}]", result
        return "[语音]", result

    async def _component_nodes_to_forward_result(
        self,
        nodes: list[Node],
        defer_caption: bool,
        event: AstrMessageEvent | None,
        forward_ctx: dict,
        depth: int,
    ) -> ChainParseResult:
        result = ChainParseResult()
        if depth > MAX_FORWARD_NODE_DEPTH:
            return result

        node_items = []
        for index, node in enumerate((nodes or [])[:MAX_FORWARD_NODE_COUNT], start=1):
            node_result = await self.chain_to_result(
                node.content or [],
                defer_caption=True,
                event=event,
                _forward_ctx=forward_ctx,
                _depth=depth + 1,
            )
            result.forward_messages.extend(node_result.forward_messages)
            node_items.append(
                {
                    "index": index,
                    "sender_name": node.name or "bot",
                    "sender_id": str(node.uin or ""),
                    "time": str(node.time or ""),
                    "content": node_result.content,
                }
            )

        block = {
            "source": "component",
            "nodes": node_items,
            "truncated": len(nodes or []) > MAX_FORWARD_NODE_COUNT,
        }
        block["id"] = self._make_forward_id(block)
        result.forward_messages.insert(0, block)
        result.content = f"[合并转发:{block['id']}]"
        return result

    async def _call_forward_msg(
        self, event: AstrMessageEvent | None, forward_id: str
    ) -> dict | None:
        if not event or not forward_id:
            return None
        bot = getattr(event, "bot", None)
        callers = []
        api = getattr(bot, "api", None)
        if callable(getattr(api, "call_action", None)):
            callers.append(api.call_action)
        if callable(getattr(bot, "call_action", None)):
            callers.append(bot.call_action)
        if not callers:
            return None

        forward_id = str(forward_id).strip()
        params_list = [{"message_id": forward_id}, {"id": forward_id}]
        if forward_id.isdigit():
            int_id = int(forward_id)
            params_list.extend([{"message_id": int_id}, {"id": int_id}])

        routing_params = {}
        try:
            self_id = str(event.get_self_id() or "").strip()
        except Exception:
            self_id = ""
        if self_id:
            routing_params["self_id"] = self_id

        last_error = None
        for caller in callers:
            for params in params_list:
                call_params = dict(params)
                call_params.update(routing_params)
                for keyword_action in (True, False):
                    try:
                        if keyword_action:
                            payload = await caller(
                                action="get_forward_msg", **call_params
                            )
                        else:
                            payload = await caller("get_forward_msg", **call_params)
                        if isinstance(payload, dict):
                            return payload
                    except TypeError as e:
                        last_error = e
                        continue
                    except Exception as e:
                        last_error = e
                        continue
        if last_error:
            logger.debug(f"[Giftia] 获取合并转发消息失败: {last_error}")
        return None

    async def _forward_id_to_result(
        self,
        forward_id,
        defer_caption: bool,
        event: AstrMessageEvent | None,
        forward_ctx: dict,
        depth: int,
    ) -> ChainParseResult:
        forward_id = str(forward_id or "").strip()
        if not forward_id:
            result = ChainParseResult()
            result.content = "[合并转发消息]"
            return result

        if forward_id in forward_ctx["remote_refs"]:
            block_id = forward_ctx["remote_refs"][forward_id]
            result = ChainParseResult()
            result.content = f"[合并转发:{block_id}]"
            return result

        if (
            depth > MAX_FORWARD_NODE_DEPTH
            or forward_ctx["fetch_count"] >= MAX_FORWARD_FETCH
            or forward_id in forward_ctx["fetching"]
        ):
            block = {
                "source": "remote",
                "source_id": forward_id,
                "nodes": [],
                "truncated": True,
            }
            block["id"] = self._make_forward_id(block)
            forward_ctx["remote_refs"][forward_id] = block["id"]
            result = ChainParseResult(
                content=f"[合并转发:{block['id']}]",
                forward_messages=[block],
            )
            return result

        forward_ctx["fetching"].add(forward_id)
        forward_ctx["fetch_count"] += 1
        payload = await self._call_forward_msg(event, forward_id)
        forward_ctx["fetching"].discard(forward_id)
        if not payload:
            block = {
                "source": "remote",
                "source_id": forward_id,
                "nodes": [],
                "unresolved": True,
            }
            block["id"] = self._make_forward_id(block)
            forward_ctx["remote_refs"][forward_id] = block["id"]
            return ChainParseResult(
                content=f"[合并转发:{block['id']}]",
                forward_messages=[block],
            )

        parsed = await self._onebot_forward_payload_to_result(
            payload,
            source_id=forward_id,
            defer_caption=defer_caption,
            event=event,
            forward_ctx=forward_ctx,
            depth=depth,
        )
        if parsed.forward_messages:
            forward_ctx["remote_refs"][forward_id] = parsed.forward_messages[0]["id"]
        return parsed

    async def _onebot_forward_payload_to_result(
        self,
        payload: dict,
        source_id: str,
        defer_caption: bool,
        event: AstrMessageEvent | None,
        forward_ctx: dict,
        depth: int,
    ) -> ChainParseResult:
        data = self._unwrap_action_response(payload)
        nodes = (
            data.get("messages")
            or data.get("message")
            or data.get("nodes")
            or data.get("nodeList")
        )
        if not isinstance(nodes, list):
            block = {
                "source": "remote",
                "source_id": source_id,
                "nodes": [],
                "unresolved": True,
            }
            block["id"] = self._make_forward_id(block)
            return ChainParseResult(
                content=f"[合并转发:{block['id']}]",
                forward_messages=[block],
            )
        return await self._onebot_nodes_to_forward_result(
            nodes,
            source_id=source_id,
            defer_caption=defer_caption,
            event=event,
            forward_ctx=forward_ctx,
            depth=depth,
        )

    async def _onebot_nodes_to_forward_result(
        self,
        nodes: list,
        source_id: str,
        defer_caption: bool,
        event: AstrMessageEvent | None,
        forward_ctx: dict,
        depth: int,
    ) -> ChainParseResult:
        result = ChainParseResult()
        if depth > MAX_FORWARD_NODE_DEPTH:
            return result

        node_items = []
        for index, node in enumerate(nodes[:MAX_FORWARD_NODE_COUNT], start=1):
            if not isinstance(node, dict):
                continue
            node_data = node.get("data") if isinstance(node.get("data"), dict) else {}
            sender = node.get("sender")
            if not isinstance(sender, dict):
                sender = (
                    node_data.get("sender")
                    if isinstance(node_data.get("sender"), dict)
                    else {}
                )
            sender_id = (
                sender.get("user_id")
                or sender.get("id")
                or node_data.get("user_id")
                or node_data.get("uin")
                or node.get("user_id")
                or ""
            )
            sender_name = (
                sender.get("nickname")
                or sender.get("card")
                or node_data.get("nickname")
                or node_data.get("name")
                or node.get("nickname")
                or node.get("name")
                or sender_id
                or "未知用户"
            )
            raw_content = (
                node.get("message")
                or node.get("content")
                or node_data.get("content")
                or node_data.get("message")
                or []
            )
            node_result = await self._onebot_content_to_result(
                raw_content,
                defer_caption=True,
                event=event,
                forward_ctx=forward_ctx,
                depth=depth + 1,
            )
            result.forward_messages.extend(node_result.forward_messages)
            node_items.append(
                {
                    "index": index,
                    "sender_name": str(sender_name),
                    "sender_id": str(sender_id or ""),
                    "time": str(node.get("time") or node_data.get("time") or ""),
                    "content": node_result.content,
                }
            )

        block = {
            "source": "remote" if source_id else "onebot",
            "source_id": source_id,
            "nodes": node_items,
            "truncated": len(nodes) > MAX_FORWARD_NODE_COUNT,
        }
        block["id"] = self._make_forward_id(block)
        result.forward_messages.insert(0, block)
        result.content = f"[合并转发:{block['id']}]"
        return result

    async def _onebot_content_to_result(
        self,
        raw_content,
        defer_caption: bool,
        event: AstrMessageEvent | None,
        forward_ctx: dict,
        depth: int,
    ) -> ChainParseResult:
        if isinstance(raw_content, list):
            if raw_content and all(
                isinstance(item, BaseMessageComponent) for item in raw_content
            ):
                return await self.chain_to_result(
                    raw_content,
                    defer_caption=defer_caption,
                    event=event,
                    _forward_ctx=forward_ctx,
                    _depth=depth,
                )
            return await self._onebot_segments_to_result(
                raw_content,
                defer_caption=defer_caption,
                event=event,
                forward_ctx=forward_ctx,
                depth=depth,
            )
        if isinstance(raw_content, dict):
            return await self._onebot_segments_to_result(
                [raw_content],
                defer_caption=defer_caption,
                event=event,
                forward_ctx=forward_ctx,
                depth=depth,
            )
        if isinstance(raw_content, str):
            text = raw_content.strip()
            if not text:
                return ChainParseResult()
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return await self._onebot_segments_to_result(
                    parsed,
                    defer_caption=defer_caption,
                    event=event,
                    forward_ctx=forward_ctx,
                    depth=depth,
                )
            if isinstance(parsed, dict):
                return await self._onebot_segments_to_result(
                    [parsed],
                    defer_caption=defer_caption,
                    event=event,
                    forward_ctx=forward_ctx,
                    depth=depth,
                )
            return ChainParseResult(content=text)
        return ChainParseResult()

    async def _onebot_segments_to_result(
        self,
        segments: list,
        defer_caption: bool,
        event: AstrMessageEvent | None,
        forward_ctx: dict,
        depth: int,
    ) -> ChainParseResult:
        result = ChainParseResult()
        parts = []
        index = 0
        while index < len(segments):
            seg = segments[index]
            index += 1
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type") or "").lower()
            seg_data = seg.get("data") if isinstance(seg.get("data"), dict) else {}

            if seg_type in ("text", "plain"):
                text = seg_data.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif seg_type == "at":
                qq = seg_data.get("qq") or seg_data.get("user_id") or ""
                name = seg_data.get("name") or ""
                parts.append(f"<@{name}({qq})>" if name else f"<@{qq}>")
            elif seg_type == "image":
                url = seg_data.get("url") or ""
                file_name = seg_data.get("file") or seg_data.get("path") or ""
                part, media_result = await self._format_image_ref(
                    str(url or ""), str(file_name or ""), defer_caption
                )
                result.merge(media_result)
                parts.append(part)
            elif seg_type in ("record", "voice", "audio"):
                url = seg_data.get("url") or ""
                file_name = seg_data.get("file") or seg_data.get("path") or ""
                part, media_result = await self._format_audio_ref(
                    str(url or ""), str(file_name or ""), defer_caption
                )
                result.merge(media_result)
                parts.append(part)
            elif seg_type == "video":
                parts.append("[视频]")
            elif seg_type == "file":
                name = (
                    seg_data.get("name")
                    or seg_data.get("file_name")
                    or seg_data.get("file")
                    or "file"
                )
                parts.append(f"[文件:{name}]")
            elif seg_type in ("forward", "forward_msg"):
                fid = seg_data.get("id") or seg_data.get("message_id")
                if fid:
                    forward_result = await self._forward_id_to_result(
                        fid,
                        defer_caption=defer_caption,
                        event=event,
                        forward_ctx=forward_ctx,
                        depth=depth,
                    )
                    result.merge(forward_result)
                    parts.append(forward_result.content)
                else:
                    nested = seg_data.get("content") or seg_data.get("nodes")
                    nested_result = await self._onebot_content_to_result(
                        nested,
                        defer_caption=defer_caption,
                        event=event,
                        forward_ctx=forward_ctx,
                        depth=depth + 1,
                    )
                    result.merge(nested_result)
                    if nested_result.content:
                        parts.append(nested_result.content)
                    else:
                        parts.append("[合并转发消息]")
            elif seg_type == "node":
                nodes = [seg]
                while index < len(segments):
                    next_seg = segments[index]
                    if not isinstance(next_seg, dict):
                        break
                    if str(next_seg.get("type") or "").lower() != "node":
                        break
                    nodes.append(next_seg)
                    index += 1
                forward_result = await self._onebot_nodes_to_forward_result(
                    nodes,
                    source_id="",
                    defer_caption=defer_caption,
                    event=event,
                    forward_ctx=forward_ctx,
                    depth=depth,
                )
                result.merge(forward_result)
                parts.append(forward_result.content)
            elif seg_type == "nodes":
                nodes = (
                    seg_data.get("nodes")
                    or seg_data.get("messages")
                    or seg_data.get("message")
                    or []
                )
                if isinstance(nodes, list):
                    forward_result = await self._onebot_nodes_to_forward_result(
                        nodes,
                        source_id="",
                        defer_caption=defer_caption,
                        event=event,
                        forward_ctx=forward_ctx,
                        depth=depth,
                    )
                    result.merge(forward_result)
                    parts.append(forward_result.content)
            elif seg_type == "json":
                raw_json = seg_data.get("data") or seg_data
                forward_result = await self._json_to_forward_result(
                    raw_json,
                    defer_caption=defer_caption,
                    event=event,
                    forward_ctx=forward_ctx,
                    depth=depth,
                )
                if forward_result:
                    result.merge(forward_result)
                    parts.append(forward_result.content)
                else:
                    parts.append("[合并转发消息]")
        result.content = " ".join(part for part in parts if part).strip()
        return result

    def _extract_json_forward_source_id(self, data: dict) -> str:
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        detail = meta.get("detail") if isinstance(meta.get("detail"), dict) else {}
        candidates = [detail, meta, data]
        for item in candidates:
            for key in (
                "resid",
                "m_resid",
                "forward_id",
                "message_id",
                "id",
                "msg_resid",
                "uniseq",
            ):
                value = item.get(key) if isinstance(item, dict) else None
                if isinstance(value, (str, int)) and str(value).strip():
                    return str(value).strip()
        return ""

    def _extract_json_forward_preview_nodes(self, data: dict) -> list[dict]:
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        detail = meta.get("detail") if isinstance(meta.get("detail"), dict) else {}
        news_items = detail.get("news")
        if not isinstance(news_items, list):
            return []

        nodes = []
        for item in news_items:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            sender_name = item.get("name") or item.get("title") or ""
            nodes.append(
                {
                    "sender": {"nickname": sender_name},
                    "message": [{"type": "text", "data": {"text": text.strip()}}],
                }
            )
        return nodes

    async def _json_to_forward_result(
        self,
        data,
        defer_caption: bool,
        event: AstrMessageEvent | None,
        forward_ctx: dict,
        depth: int,
    ) -> ChainParseResult | None:
        if isinstance(data, str):
            raw = data.strip().replace("&#44;", ",")
            try:
                data = json.loads(raw)
            except Exception:
                return None
        elif isinstance(data, dict) and "data" in data:
            inner = data.get("data")
            if isinstance(inner, str):
                try:
                    parsed_inner = json.loads(inner.strip().replace("&#44;", ","))
                    if isinstance(parsed_inner, dict):
                        data = parsed_inner
                except Exception:
                    pass
            elif isinstance(inner, dict):
                data = inner
        if not isinstance(data, dict):
            return None

        is_multimsg = data.get("app") == "com.tencent.multimsg" or data.get("view") in (
            "contact",
            "Forward",
        )
        if not is_multimsg:
            return None

        source_id = self._extract_json_forward_source_id(data)
        preview_nodes = self._extract_json_forward_preview_nodes(data)
        if source_id:
            fetched = await self._forward_id_to_result(
                source_id,
                defer_caption=defer_caption,
                event=event,
                forward_ctx=forward_ctx,
                depth=depth,
            )
            first_block = fetched.forward_messages[0] if fetched.forward_messages else {}
            if fetched.forward_messages and not first_block.get("unresolved"):
                return fetched
            if preview_nodes:
                preview_result = await self._onebot_nodes_to_forward_result(
                    preview_nodes,
                    source_id=source_id,
                    defer_caption=defer_caption,
                    event=event,
                    forward_ctx=forward_ctx,
                    depth=depth,
                )
                if preview_result.forward_messages:
                    forward_ctx["remote_refs"][source_id] = preview_result.forward_messages[
                        0
                    ]["id"]
                return preview_result
            return fetched
        if preview_nodes:
            return await self._onebot_nodes_to_forward_result(
                preview_nodes,
                source_id="",
                defer_caption=defer_caption,
                event=event,
                forward_ctx=forward_ctx,
                depth=depth,
            )
        return None

    async def _get_image_caption(
        self, url: str, file_name: str | None = None, defer_caption: bool = False, custom_desc: str | None = None
    ) -> tuple[str | None, MediaCaption | None]:
        """获取图片描述"""
        if not url and file_name:
            url = file_name
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
            image_bytes = None
            if file_name and file_name.startswith("file://"):
                import urllib.parse
                from pathlib import Path

                clean_path = urllib.parse.unquote(file_name[7:])
                local_path = Path(clean_path)
                if local_path.is_file():
                    try:
                        image_bytes = local_path.read_bytes()
                    except Exception as e:
                        file_name_disp = (
                            (file_name[:100] + "...")
                            if file_name and len(file_name) > 100
                            else file_name
                        )
                        logger.error(f"[Giftia] 读取本地图片失败 {file_name_disp}: {e}")
            elif file_name and file_name.startswith("base64://"):
                import base64 as b64_module

                try:
                    b64_data = file_name[9:]
                    if "," in b64_data:
                        b64_data = b64_data.split(",", 1)[1]
                    image_bytes = b64_module.b64decode(b64_data)
                except Exception as e:
                    logger.error(f"[Giftia] 解码 base64 图片失败: {e}")

            if not image_bytes:
                image_bytes = await self.http_manager.download_media(url)

            if not image_bytes:
                return None, None
            # 生成hash
            hash_val = stable_hash or xxh3_64_hexdigest(image_bytes)

            # If the URL/file_name is a base64 string, replace it with a clean placeholder to avoid bloated DB columns
            db_url = url
            if db_url and db_url.startswith("base64://"):
                db_url = f"base64://{hash_val}"

            db_file_name = file_name
            if db_file_name and db_file_name.startswith("base64://"):
                db_file_name = f"base64://{hash_val}"

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
            if custom_desc:
                media_caption = MediaCaption(
                    hash_val=hash_val,
                    url=db_url,
                    media_type="image",
                    caption=custom_desc,
                    genre="表情包",
                    is_captioned=True,
                )
                if file_name:
                    media_caption.file_name = db_file_name
                await self.data_cache.set_caption(media_caption)
                return hash_val, media_caption

            # 检查缓存
            media_caption = await self.data_cache.get_caption_by_hash(hash_val)
            if media_caption:
                media_caption.url = db_url
                if file_name:
                    media_caption.file_name = db_file_name
                if getattr(media_caption, "is_captioned", True) or defer_caption:
                    await self.data_cache.set_caption(media_caption)
                    return hash_val, media_caption

            if not self.image_caption_enabled:
                media_caption = MediaCaption(
                    hash_val=hash_val,
                    url=db_url,
                    media_type="image",
                    is_captioned=True,
                )
                if file_name:
                    media_caption.file_name = db_file_name
                await self.data_cache.set_caption(media_caption)
                return hash_val, media_caption

            # 如果开启了延迟，直接返回一个仅包含url和hash的基础对象
            if defer_caption:
                media_caption = MediaCaption(
                    hash_val=hash_val,
                    url=db_url,
                    media_type="image",
                    is_captioned=False,
                )
                if file_name:
                    media_caption.file_name = db_file_name
                await self.data_cache.set_caption(media_caption)
                return hash_val, media_caption

            # 处理图片
            base64s, is_animated = await asyncio.to_thread(
                self.http_manager.handle_image, image_bytes
            )
            if not base64s:
                media_caption = MediaCaption(
                    hash_val=hash_val,
                    url=db_url,
                    media_type="image",
                    is_captioned=True,
                )
                if file_name:
                    media_caption.file_name = db_file_name
                await self.data_cache.set_caption(media_caption)
                return hash_val, media_caption
            # Log key identifiers so we can detect if two different URLs produce the
            # same image content (which would indicate a stale-temp-file read).
            url_disp = (url[:100] + "...") if url and len(url) > 100 else url
            logger.info(
                f"[Giftia] 调用LLM转述图片: hash={hash_val} "
                f"size={len(image_bytes)}B "
                f"head={image_bytes[:8].hex()} "
                f"url={url_disp!r}"
            )
            # 调用LLM生成图片描述
            media_caption = await self.call_llm.call_llm_image_caption(base64s)
            if not media_caption:
                media_caption = MediaCaption(
                    hash_val=hash_val,
                    url=db_url,
                    media_type="image",
                    is_captioned=True,
                )
                if file_name:
                    media_caption.file_name = db_file_name
                await self.data_cache.set_caption(media_caption)
                return hash_val, media_caption
            media_caption.hash_val = hash_val
            media_caption.url = db_url
            if file_name:
                media_caption.file_name = db_file_name
            media_caption.media_type = "image"
            media_caption.is_captioned = True
            # 缓存
            await self.data_cache.set_caption(media_caption)
            return hash_val, media_caption

    async def _get_audio_caption(
        self, url: str, file_name: str | None = None, defer_caption: bool = False
    ) -> tuple[str | None, MediaCaption | None]:
        """获取语音描述"""
        if not url and file_name:
            url = file_name
        async with self.url_locks[url]:
            if file_name and not is_temp_or_local_path(file_name):
                # 检查缓存
                hash_val, media_caption = await self.data_cache.get_caption_by_filename(
                    file_name
                )
                if hash_val and media_caption:
                    if getattr(media_caption, "is_captioned", True) or defer_caption:
                        return hash_val, media_caption

            # 语音的hash_val用url生成，如果是本地文件，使用本地内容生成hash
            audio_bytes = None
            if file_name and file_name.startswith("file://"):
                import urllib.parse
                from pathlib import Path

                clean_path = urllib.parse.unquote(file_name[7:])
                local_path = Path(clean_path)
                if local_path.is_file():
                    try:
                        audio_bytes = local_path.read_bytes()
                    except Exception as e:
                        file_name_disp = (
                            (file_name[:100] + "...")
                            if file_name and len(file_name) > 100
                            else file_name
                        )
                        logger.error(f"[Giftia] 读取本地音频失败 {file_name_disp}: {e}")

            if audio_bytes:
                hash_val = xxh3_64_hexdigest(audio_bytes)
            else:
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
            if not audio_bytes:
                try:
                    audio_bytes = await self.http_manager.download_media(url)
                except Exception as e:
                    url_disp = (url[:100] + "...") if url and len(url) > 100 else url
                    logger.error(f"[Giftia] 下载音频失败 {url_disp}: {e}")

            if audio_bytes:
                try:
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
