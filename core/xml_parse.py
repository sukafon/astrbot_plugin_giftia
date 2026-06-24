import ast
import json
import re
from xml.sax.saxutils import escape

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from astrbot.api import logger
from astrbot.api.message_components import At, Image, Plain, Reply
from astrbot.core.message.components import BaseMessageComponent

from .data_cache import DataCache
from .emoji_manager import EmojiManager
from .schemas import Decision, MediaCaption, XmlLlmResult


class XmlParse:
    def __init__(
        self,
        data_cache: DataCache,
        emoji_manager: EmojiManager,
        sticker_summaries: list[str] | None = None,
    ):
        self.data_cache = data_cache
        self.emoji_manager = emoji_manager
        import random

        self.random = random
        self.sticker_summaries = sticker_summaries or ["图片"]

    @staticmethod
    def str_to_int_bool(val):
        """将XML属性转为决策整数"""
        if val is None:
            return 2  # 未决策
        v = str(val).lower().strip()
        if v in ["true", "1", "yes", "t"]:
            return 1
        if v in ["false", "0", "no", "f"]:
            return 0
        return 2  # 无法解析的情况

    def decode_decision_xml(self, xml_str: str) -> Decision | None:
        """解码决策XML字符串"""
        if not xml_str:
            return None
        try:
            result = Decision()
            safe_xml = self.preprocess_xml(xml_str)
            soup = BeautifulSoup(safe_xml, "xml")
            decision_node = soup.find("decision")
            if decision_node:
                result.reply_decision = self.str_to_int_bool(decision_node.get("reply"))
                result.use_rag = self.str_to_int_bool(decision_node.get("use_rag"))
                result.rag_query = str(
                    decision_node.get("rag_query", "")
                ) or decision_node.get_text(strip=True)
            if result.reply_decision == 2 or result.use_rag == 2:
                logger.error(f"决策数据无效: {result}, xml_str: {xml_str[:1000]}")
                return None
            return result
        except Exception as e:
            logger.error(f"解析决策XML失败: {e}, xml_str: {xml_str[:1000]}")
            return None

    async def decode_llm_xml(
        self, xml_str: str, group_or_user_id: str
    ) -> XmlLlmResult | None:
        """解码LLM返回的XML字符串"""
        if not xml_str:
            return None
        try:
            result = XmlLlmResult()
            safe_xml = self.preprocess_xml(xml_str)
            # BeautifulSoup 容错解析
            soup = BeautifulSoup(safe_xml, "xml")
            root = soup.find("root")
            if not root:
                return None

            for child in root.find_all(recursive=False):
                tag_name = child.name

                if tag_name == "status":
                    raw_text = child.get_text(strip=True)
                    parsed_data = dict(re.findall(r"(\w+)[:：]\s*([^\n]+)", raw_text))
                    result.status.mood = (
                        parsed_data.get("心情", "").strip().strip("\"'")
                    )
                    result.status.state = (
                        parsed_data.get("状态", "").strip().strip("\"'")
                    )
                    result.status.action = (
                        parsed_data.get("动作", "").strip().strip("\"'")
                    )
                    result.status.energy = (
                        parsed_data.get("能量", "100").strip().strip("\"'")
                    )
                    result.status.memory = (
                        (parsed_data.get("思考") or parsed_data.get("记忆", ""))
                        .strip()
                        .strip("\"'")
                    )

                elif tag_name == "message":
                    sub_chain: list[BaseMessageComponent] = []
                    sub_text: str = ""
                    sub_log: str = ""

                    if child.get("quote"):
                        sub_chain.append(Reply(id=child.get("quote")))

                    for content in child.contents:
                        # 如果是纯文本内容
                        if isinstance(content, NavigableString):
                            text_content = str(content).strip()
                            if text_content:
                                sub_chain.append(Plain(text=text_content))
                                sub_text += text_content
                                sub_log += text_content

                        # 如果是子标签 (<at>, <sticker>等)
                        elif isinstance(content, Tag):
                            if content.name == "at":
                                if content.get("user_id"):
                                    sub_chain.append(At(qq=content.get("user_id")))
                                    sub_log += f" <@{content.get('user_id')}>"
                                else:
                                    logger.error(
                                        f"At组件缺少user_id属性: {content.attrs}, xml_str: {xml_str[:1000]}"
                                    )

                            elif content.name == "sticker":
                                sticker_id = self._attr_str(content, "sticker_id", "")
                                if sticker_id:
                                    local_path = self.emoji_manager.get_sticker_path(
                                        sticker_id
                                    )
                                    if local_path:
                                        img = Image.fromFileSystem(str(local_path))
                                        sub_chain.append(img)
                                    else:
                                        media_caption = (
                                            await self.data_cache.get_caption_by_hash(
                                                sticker_id
                                            )
                                        )
                                        if media_caption and media_caption.url:
                                            img = Image.fromURL(media_caption.url)
                                            sub_chain.append(img)
                                        else:
                                            logger.error(
                                                f"未找到图片: {sticker_id}, xml_str: {xml_str[:1000]}"
                                            )
                                    result.send_stickers.append(sticker_id)
                                    sub_log += f" [图片:{sticker_id}]"
                                else:
                                    logger.error(
                                        f"Sticker组件缺少sticker_id属性: {content.attrs}, xml_str: {xml_str[:1000]}"
                                    )

                    result.msg_chains.append(sub_chain)
                    result.msg_texts.append(sub_text)
                    result.msg_logs.append(sub_log)

                elif tag_name == "at":
                    if self._attr_str(child, "user_id", ""):
                        result.msg_chains.append(
                            [At(qq=self._attr_str(child, "user_id", ""))]
                        )
                        result.msg_logs.append(
                            f"<@{self._attr_str(child, 'user_id', '')}>"
                        )

                elif tag_name == "sticker":
                    sticker_id = self._attr_str(child, "sticker_id", "")
                    if sticker_id:
                        local_path = self.emoji_manager.get_sticker_path(sticker_id)
                        if local_path:
                            img = Image.fromFileSystem(str(local_path))
                            result.msg_chains.append([img])
                        else:
                            media_caption = await self.data_cache.get_caption_by_hash(
                                sticker_id
                            )
                            if media_caption and media_caption.url:
                                img = Image.fromURL(media_caption.url)
                                result.msg_chains.append([img])
                            else:
                                logger.error(
                                    f"未找到图片: {sticker_id}, xml_str: {xml_str[:1000]}"
                                )
                        result.send_stickers.append(sticker_id)
                        result.msg_logs.append(f"[图片:{sticker_id}]")
                elif tag_name == "emoji_like":
                    if self._attr_str(child, "message_id", "") and self._attr_str(
                        child, "emoji_id", ""
                    ):
                        result.emoji_ids.append(
                            (
                                self._attr_str(child, "message_id", ""),
                                self._attr_str(child, "emoji_id", ""),
                            )
                        )
                    else:
                        logger.error(
                            f"贴表情数据不完整: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )

                elif tag_name == "delete":
                    msg_id = self._attr_str(child, "message_id", "") or child.get_text(
                        strip=True
                    )
                    if msg_id:
                        result.delete_message_ids.append(msg_id)

                elif tag_name == "like":
                    if self._attr_str(child, "user_id", "") and self._attr_str(
                        child, "count", ""
                    ):
                        result.likes.append(
                            (
                                self._attr_str(child, "user_id", ""),
                                self._attr_str(child, "count", ""),
                            )
                        )
                    else:
                        logger.error(
                            f"点赞标签数据不完整: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )

                elif tag_name == "poke":
                    if self._attr_str(child, "user_id", ""):
                        result.poke.append(
                            (
                                self._attr_str(child, "group_id", "")
                                or group_or_user_id,
                                self._attr_str(child, "user_id", ""),
                            )
                        )
                    else:
                        logger.error(
                            f"戳一戳标签数据不完整: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )

                elif tag_name == "ban":
                    if self._attr_str(child, "user_id", ""):
                        result.ban.append(
                            (
                                self._attr_str(child, "group_id", "")
                                or group_or_user_id,
                                self._attr_str(child, "user_id", ""),
                                self._attr_str(child, "duration", ""),
                            )
                        )
                    else:
                        logger.error(
                            f"禁言标签数据不完整: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )

                elif tag_name == "kick":
                    if self._attr_str(child, "user_id", ""):
                        result.kick.append(
                            (
                                self._attr_str(child, "group_id", "")
                                or group_or_user_id,
                                self._attr_str(child, "user_id", ""),
                            )
                        )
                    else:
                        logger.error(
                            f"踢人标签数据不完整: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )

                elif tag_name == "leave":
                    result.leave.append(group_or_user_id)

                elif tag_name == "summary_user_profile":
                    text = child.get_text(strip=True)
                    user_id = self._attr_str(child, "user_id", "")
                    if text and user_id:
                        result.summary_user_profiles.append(
                            (
                                group_or_user_id,
                                user_id,
                                text,
                            )
                        )

                elif tag_name == "summary_group_profile":
                    text = child.get_text(strip=True)
                    if text:
                        result.summary_group_profiles.append((group_or_user_id, text))

                elif tag_name == "save_memory":
                    text = child.get_text(strip=True)
                    if text:
                        result.save_memories.append((group_or_user_id, text))

                elif tag_name == "search_memory":
                    text = child.get_text(strip=True)
                    if text:
                        result.search_memories.append((group_or_user_id, text))

                elif tag_name == "search_chat_history":
                    keyword = self._attr_str(child, "keyword", "")
                    user_id = self._attr_str(child, "user_id", "")
                    start_time = self._attr_str(child, "start_time", "")
                    end_time = self._attr_str(child, "end_time", "")
                    sort_order = self._attr_str(child, "sort_order", "desc")
                    limit_str = self._attr_str(child, "limit", "30")
                    try:
                        limit = int(limit_str)
                    except ValueError:
                        limit = 30
                    result.search_histories.append(
                        {
                            "group_or_user_id": group_or_user_id,
                            "keyword": keyword,
                            "user_id": user_id,
                            "start_time": start_time,
                            "end_time": end_time,
                            "sort_order": sort_order,
                            "limit": limit,
                        }
                    )

                elif tag_name == "get_message_context":
                    message_id = self._attr_str(child, "message_id", "")
                    limit_str = self._attr_str(child, "limit", "30")
                    try:
                        limit = int(limit_str)
                    except ValueError:
                        limit = 30
                    if message_id:
                        result.get_message_contexts.append(
                            {
                                "group_or_user_id": group_or_user_id,
                                "message_id": message_id,
                                "limit": limit,
                            }
                        )

                elif tag_name == "delete_memory":
                    memory_id = self._attr_str(child, "id", "")
                    if memory_id:
                        result.delete_memories.append(memory_id)
                    else:
                        logger.error(
                            f"Delete memory数据不完整: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )

                elif tag_name == "update_memory":
                    memory_id = self._attr_str(child, "id", "")
                    text = child.get_text(strip=True)
                    if memory_id and text:
                        result.update_memories.append((memory_id, text))
                    else:
                        logger.error(
                            f"Update memory数据不完整: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )

                elif tag_name == "update_relation":
                    user_id = self._attr_str(child, "user_id", "")
                    delta = self._attr_str(child, "delta", "")
                    if user_id and delta:
                        # delta应该是数字类型，尝试转换
                        try:
                            # 如果有+号，去掉它再转换
                            if delta.startswith("+"):
                                delta = delta[1:]
                            delta_int = int(delta)
                            # 如果delta绝对值大于+-5，截断到+-5，防止误操作导致关系崩盘
                            if delta_int > 5:
                                delta_int = 5
                            elif delta_int < -5:
                                delta_int = -5
                            result.update_relations.append((user_id, delta_int))
                        except ValueError:
                            logger.error(
                                f"Update relation delta值无效: {delta}, xml_str: {xml_str[:1000]}"
                            )
                    else:
                        logger.error(
                            f"Update relation数据不完整: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )

                elif tag_name == "set_relation_title":
                    user_id = self._attr_str(child, "user_id", "")
                    title = child.get_text(strip=True)
                    if user_id and title:
                        result.set_relation_titles.append((user_id, title))
                    else:
                        logger.error(
                            f"Set relation title数据不完整: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )

                elif tag_name == "tool_call":
                    tool_name = self._attr_str(child, "name", "")
                    text = self._attr_str(child, "arguments", "") or child.get_text(
                        strip=True
                    )
                    arg_dict = None
                    if tool_name and text:
                        arg_dict = self.parse_str_json(text)

                    if tool_name and arg_dict is not None:
                        result.tools_to_call.append((tool_name, arg_dict))
                    else:
                        logger.error(
                            f"Tool call数据不完整或解析失败: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )
                        raise ValueError(f"Malformed tool call in XML: {text}")

                elif tag_name == "schedule_task":
                    task_time = self._attr_str(child, "time", "")
                    text = child.get_text(strip=True)
                    if task_time and text:
                        result.schedule_tasks.append(
                            (
                                group_or_user_id,
                                task_time,
                                text,
                            )
                        )
                    else:
                        logger.error(
                            f"Schedule task数据不完整: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )

                elif tag_name == "delete_task":
                    task_id = self._attr_str(child, "task_id", "")
                    if task_id:
                        result.delete_schedule_tasks.append(task_id)
                    else:
                        logger.error(
                            f"Delete task数据不完整: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )

                elif tag_name == "all_task":
                    group_id = self._attr_str(child, "group_id", group_or_user_id)
                    result.all_tasks.append(group_id)

                elif tag_name == "add_sticker":
                    media_id = self._attr_str(child, "media_id", "")
                    if media_id:
                        result.add_stickers.append(media_id)
                    else:
                        logger.error(
                            f"Add sticker数据不完整: {child.attrs}, xml_str: {xml_str[:1000]}"
                        )

            return result
        except Exception as e:
            logger.error(f"解析LLM XML失败: {e}, xml_str: {xml_str[:1000]}")
            return None

    def decode_media_caption_xml(self, xml_str: str) -> MediaCaption | None:
        """解码媒体图片描述XML字符串"""
        result = MediaCaption()
        result.media_type = "image"

        try:
            safe_xml = self.preprocess_xml(xml_str)
            soup = BeautifulSoup(safe_xml, "xml")
            root = soup.find("root")
            if root:
                for child in root.find_all(recursive=False):
                    if child.name == "caption":
                        result.genre = self._attr_str(child, "genre", "")
                        result.character = self._attr_str(child, "character", "")
                        result.source = self._attr_str(child, "source", "")
                        result.text = self._attr_str(child, "text", "")
                        result.caption = child.get_text(strip=True)

            if not result.caption:
                logger.warning(
                    f"媒体图片描述数据无效: {result}, xml_str: {xml_str[:1000]}"
                )
                return None
            return result
        except Exception as e:
            logger.error(f"解析媒体图片描述XML失败: {e}, xml_str: {xml_str[:1000]}")
            return None

    def decode_media_audio_xml(self, xml_str: str) -> MediaCaption | None:
        """解码媒体语音描述XML字符串"""
        result = MediaCaption()
        result.media_type = "audio"

        try:
            safe_xml = self.preprocess_xml(xml_str)
            soup = BeautifulSoup(safe_xml, "xml")
            root = soup.find("root")
            if root:
                for child in root.find_all(recursive=False):
                    if child.name == "caption":
                        result.genre = self._attr_str(child, "genre", "")
                        result.character = self._attr_str(child, "character", "")
                        result.source = self._attr_str(child, "source", "")
                        result.text = self._attr_str(child, "text", "")
                        result.caption = child.get_text(strip=True)

            if not result.caption:
                logger.warning(
                    f"媒体语音描述数据无效: {result}, xml_str: {xml_str[:1000]}"
                )
                return None
            return result
        except Exception as e:
            logger.error(f"解析媒体语音描述XML失败: {e}, xml_str: {xml_str[:1000]}")
            return None

    def parse_str_json(self, response_text: str) -> dict | None:
        """把AI回复的json字符串解析成字典"""
        clean_text = response_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()

        match = re.search(r"\{.*\}", clean_text, re.DOTALL)
        if match:
            clean_text = match.group(0)
        else:
            logger.error(f"未找到JSON对象，原始文本: {response_text[:1000]}")
            return None

        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(clean_text)
            except Exception as e:
                logger.error(f"解析JSON失败: {e}, clean_text: {clean_text[:1000]}")
                return None

    def close_xml_tags(self, xml_str: str) -> str:
        """Automatically close unclosed sibling XML tags.

        Args:
            xml_str: The raw XML string from the LLM.

        Returns:
            The processed XML string with properly closed tags.
        """
        flat_tags = [
            "status",
            "message",
            "delete",
            "like",
            "poke",
            "ban",
            "kick",
            "leave",
            "summary_user_profile",
            "summary_group_profile",
            "save_memory",
            "search_memory",
            "search_chat_history",
            "get_message_context",
            "delete_memory",
            "update_memory",
            "update_relation",
            "set_relation_title",
            "tool_call",
            "schedule_task",
            "delete_task",
            "all_task",
            "add_sticker",
            "decision",
            "caption",
        ]
        pattern = r"<\s*(/?)\s*(" + "|".join(flat_tags) + r")\b([^>]*?)(/?)\s*>"

        result = []
        open_tag = None
        last_end = 0

        for match in re.finditer(pattern, xml_str):
            is_close = bool(match.group(1))
            tag_name = match.group(2)
            is_self_closing = bool(match.group(4))
            start, end = match.span()

            result.append(xml_str[last_end:start])

            if is_close:
                if open_tag == tag_name:
                    result.append(match.group(0))
                    open_tag = None
                else:
                    result.append(match.group(0))
            elif is_self_closing:
                if open_tag is not None:
                    result.append(f"</{open_tag}>")
                    open_tag = None
                result.append(match.group(0))
            else:
                if open_tag is not None:
                    result.append(f"</{open_tag}>")
                result.append(match.group(0))
                open_tag = tag_name

            last_end = end

        result.append(xml_str[last_end:])
        if open_tag is not None:
            result.append(f"</{open_tag}>")

        return "".join(result)

    def preprocess_xml(self, xml_raw: str) -> str:
        """全能型 XML 预处理：融合了 escape 的终极安全版本"""
        if not xml_raw:
            return ""

        # 移除首尾的 ```xml 等代码块标记
        clean_str = re.sub(
            r"```[a-zA-Z]*\s*|\s*```", "", xml_raw, flags=re.IGNORECASE
        ).strip()

        # 自动闭合未闭合的同级标签
        clean_str = self.close_xml_tags(clean_str)

        # 处理 <think>...</think> 标签内部的xml，进行转义
        pattern_think = re.compile(
            r"(<\s*think\s*>)(.*?)(<\s*/\s*think\s*>)", re.DOTALL | re.IGNORECASE
        )

        def escape_think(match):
            # 使用 escape 自动转义 <, >, &
            return f"{match.group(1)}{escape(match.group(2))}{match.group(3)}"

        clean_str = pattern_think.sub(escape_think, clean_str)

        # # 模型用 Markdown 列表打草稿（比如以 * 或 1. 开头）
        # lines = clean_str.split("\n")
        # processed_lines = []
        # for line in lines:
        #     # 正则匹配：以 *、- 或数字加点开头（前面允许有空格）
        #     if re.match(r"^\s*([\*\-]|\d+\.)\s+", line):
        #         # 用 escape 把整行草稿变为安全的纯文本
        #         line = escape(line)
        #     processed_lines.append(line)

        # clean_str = "\n".join(processed_lines)

        # 包裹根节点
        return f"<root>{clean_str}</root>"

    @staticmethod
    def _attr_str(tag: Tag, attr_name: str, default: str = "") -> str:
        """安全获取 BeautifulSoup 标签的字符串属性"""
        val = tag.get(attr_name, default)
        # 如果类型检查器认为可能是列表，取第一个值或转为字符串
        if isinstance(val, list):
            return val[0] if val else default
        return val if val is not None else default
