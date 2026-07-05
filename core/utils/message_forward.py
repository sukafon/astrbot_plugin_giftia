import json

from xxhash import xxh3_64_hexdigest

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Node
from astrbot.core.message.components import BaseMessageComponent

from .message_parse_types import ChainParseResult

MAX_FORWARD_FETCH = 5
MAX_FORWARD_NODE_DEPTH = 4
MAX_FORWARD_NODE_COUNT = 80


class MessageForwardParser:
    def __init__(self, chain_to_result, format_image_ref, format_audio_ref):
        self.chain_to_result = chain_to_result
        self.format_image_ref = format_image_ref
        self.format_audio_ref = format_audio_ref

    @staticmethod
    def make_forward_id(payload: dict) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return f"fwd_{xxh3_64_hexdigest(raw.encode())[:12]}"

    @staticmethod
    def unwrap_action_response(payload) -> dict:
        if not isinstance(payload, dict):
            return {}
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload

    async def component_nodes_to_forward_result(
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
        block["id"] = self.make_forward_id(block)
        result.forward_messages.insert(0, block)
        result.content = f"[合并转发:{block['id']}]"
        return result

    async def call_forward_msg(
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

    async def forward_id_to_result(
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
            block["id"] = self.make_forward_id(block)
            forward_ctx["remote_refs"][forward_id] = block["id"]
            result = ChainParseResult(
                content=f"[合并转发:{block['id']}]",
                forward_messages=[block],
            )
            return result

        forward_ctx["fetching"].add(forward_id)
        forward_ctx["fetch_count"] += 1
        payload = await self.call_forward_msg(event, forward_id)
        forward_ctx["fetching"].discard(forward_id)
        if not payload:
            block = {
                "source": "remote",
                "source_id": forward_id,
                "nodes": [],
                "unresolved": True,
            }
            block["id"] = self.make_forward_id(block)
            forward_ctx["remote_refs"][forward_id] = block["id"]
            return ChainParseResult(
                content=f"[合并转发:{block['id']}]",
                forward_messages=[block],
            )

        parsed = await self.onebot_forward_payload_to_result(
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

    async def onebot_forward_payload_to_result(
        self,
        payload: dict,
        source_id: str,
        defer_caption: bool,
        event: AstrMessageEvent | None,
        forward_ctx: dict,
        depth: int,
    ) -> ChainParseResult:
        data = self.unwrap_action_response(payload)
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
            block["id"] = self.make_forward_id(block)
            return ChainParseResult(
                content=f"[合并转发:{block['id']}]",
                forward_messages=[block],
            )
        return await self.onebot_nodes_to_forward_result(
            nodes,
            source_id=source_id,
            defer_caption=defer_caption,
            event=event,
            forward_ctx=forward_ctx,
            depth=depth,
        )

    async def onebot_nodes_to_forward_result(
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
            node_result = await self.onebot_content_to_result(
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
        block["id"] = self.make_forward_id(block)
        result.forward_messages.insert(0, block)
        result.content = f"[合并转发:{block['id']}]"
        return result

    async def onebot_content_to_result(
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
            return await self.onebot_segments_to_result(
                raw_content,
                defer_caption=defer_caption,
                event=event,
                forward_ctx=forward_ctx,
                depth=depth,
            )
        if isinstance(raw_content, dict):
            return await self.onebot_segments_to_result(
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
                return await self.onebot_segments_to_result(
                    parsed,
                    defer_caption=defer_caption,
                    event=event,
                    forward_ctx=forward_ctx,
                    depth=depth,
                )
            if isinstance(parsed, dict):
                return await self.onebot_segments_to_result(
                    [parsed],
                    defer_caption=defer_caption,
                    event=event,
                    forward_ctx=forward_ctx,
                    depth=depth,
                )
            return ChainParseResult(content=text)
        return ChainParseResult()

    async def onebot_segments_to_result(
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
                part, media_result = await self.format_image_ref(
                    str(url or ""), str(file_name or ""), defer_caption
                )
                result.merge(media_result)
                parts.append(part)
            elif seg_type in ("record", "voice", "audio"):
                url = seg_data.get("url") or ""
                file_name = seg_data.get("file") or seg_data.get("path") or ""
                part, media_result = await self.format_audio_ref(
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
                    forward_result = await self.forward_id_to_result(
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
                    nested_result = await self.onebot_content_to_result(
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
                forward_result = await self.onebot_nodes_to_forward_result(
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
                    forward_result = await self.onebot_nodes_to_forward_result(
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
                forward_result = await self.json_to_forward_result(
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

    def extract_json_forward_source_id(self, data: dict) -> str:
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

    def extract_json_forward_preview_nodes(self, data: dict) -> list[dict]:
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

    async def json_to_forward_result(
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

        source_id = self.extract_json_forward_source_id(data)
        preview_nodes = self.extract_json_forward_preview_nodes(data)
        if source_id:
            fetched = await self.forward_id_to_result(
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
                preview_result = await self.onebot_nodes_to_forward_result(
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
            return await self.onebot_nodes_to_forward_result(
                preview_nodes,
                source_id="",
                defer_caption=defer_caption,
                event=event,
                forward_ctx=forward_ctx,
                depth=depth,
            )
        return None
