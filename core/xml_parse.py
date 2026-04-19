import ast
import json
import re
import xml.etree.ElementTree as ET

from astrbot.api import logger
from astrbot.api.message_components import At, Image, Plain, Reply
from astrbot.core.message.components import BaseMessageComponent

from .data_cache import DataCache
from .schemas import Decision, MediaCaption, XmlLlmResult


class XmlParse:
    def __init__(self, data_cache: DataCache):
        self.data_cache = data_cache

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
        # 初始化
        result = Decision()

        # 开始解析
        try:
            clean_str = re.sub(
                r"```[a-zA-Z]*\s*|\s*```", "", xml_str, flags=re.IGNORECASE
            ).strip()
            wrapped_data = f"<root>{clean_str}</root>"
            root = ET.fromstring(wrapped_data)

            for child in root:
                # if child.tag == "think":
                #     # 打印思考看看
                #     logger.info(
                #         f"<think>{child.text.strip() if child.text else ''}</think>"
                #     )
                if child.tag == "decision":
                    result.reply_decision = self.str_to_int_bool(child.get("reply"))
                    result.use_rag = self.str_to_int_bool(child.get("use_rag"))
                    result.rag_query = (
                        child.get("rag_query", "") or child.text.strip()
                        if child.text
                        else ""
                    )
            # 检查数据是否有效
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
        # 初始化
        result = XmlLlmResult()
        try:
            # 开始解析
            clean_str = re.sub(
                r"```[a-zA-Z]*\s*|\s*```", "", xml_str, flags=re.IGNORECASE
            ).strip()
            wrapped_data = f"<root>{clean_str}</root>"
            safe_xml = self.mask_think_content(wrapped_data)
            root = ET.fromstring(safe_xml)

            for child in root:
                # if child.tag == "think":
                #     # 打印思考看看
                #     logger.info(
                #         f"<think>{child.text.strip() if child.text else ''}</think>"
                #     )
                if child.tag == "status":
                    result.status.mood = child.get("mood", "")
                    result.status.state = child.get("state", "")
                    result.status.action = child.get("action", "")
                    result.status.energy = child.get("energy", "")
                    result.status.memory = child.text.strip() if child.text else ""
                    # 打印状态看看
                    # logger.info(
                    #     f"<mood>{result.status.mood}</mood>"
                    #     f"<state>{result.status.state}</state>"
                    #     f"<memory>{result.status.memory}</memory>"
                    #     f"<action>{result.status.action}</action>"
                    #     f"<energy>{result.status.energy}</energy>"
                    # )
                # 仅将reply、at、image、plain合并为同一条消息，其他拆分成独立消息链，避免行为不自然
                elif child.tag == "message":
                    sub_chain: list[BaseMessageComponent] = []
                    # 收集纯文本消息，用于非aiocqhttp平台发送
                    sub_text: str = ""
                    if child.get("quote"):
                        if child.get("quote"):
                            sub_chain.append(Reply(id=child.get("quote")))
                        else:
                            logger.error(
                                f"Reply组件缺少quote属性: {child.attrib}, xml_str: {xml_str[:1000]}"
                            )
                    # 标签<message>内，子标签前的文本
                    if child.text and child.text.strip():
                        # 判断前面是不是AT，是AT加入\u200b字符
                        if sub_chain and isinstance(sub_chain[-1], At):
                            sub_chain.append(Plain(text="\u200b" + child.text.strip()))
                        else:
                            sub_chain.append(Plain(text=child.text.strip()))
                        sub_text = child.text.strip()
                    for element in child:
                        if element.tag == "at":
                            if element.get("user_id"):
                                sub_chain.append(At(qq=element.get("user_id")))
                            else:
                                logger.error(
                                    f"At组件缺少user_id属性: {element.attrib}, xml_str: {xml_str[:1000]}"
                                )
                        # sticker作为表情包的语义比image更强烈一些
                        elif element.tag == "sticker":
                            if element.get("media_id"):
                                media_caption = (
                                    await self.data_cache.get_caption_by_hash(
                                        element.get("media_id", "")
                                    )
                                )
                                if media_caption and media_caption.url:
                                    sub_chain.append(Image.fromURL(media_caption.url))
                                else:
                                    logger.error(
                                        f"未找到图片: {element.get('media_id')}, xml_str: {xml_str[:1000]}"
                                    )
                            else:
                                logger.error(
                                    f"Sticker组件缺少media_id属性: {element.attrib}, xml_str: {xml_str[:1000]}"
                                )
                        # 标签<message>内，子标签后的文本
                        if element.tail and element.tail.strip():
                            # 判断前面是不是AT，是AT加入\u200b字符
                            if sub_chain and isinstance(sub_chain[-1], At):
                                sub_chain.append(
                                    Plain(text="\u200b" + element.tail.strip())
                                )
                            else:
                                sub_chain.append(Plain(text=element.tail.strip()))
                            sub_text += element.tail.strip()
                    result.msg_chains.append(sub_chain)
                    result.msg_texts.append(sub_text)
                # 允许at也作为独立的消息，增强可靠性
                elif child.tag == "at":
                    if child.get("user_id"):
                        result.msg_chains.append([At(qq=child.get("user_id"))])
                # 图片和表情包
                elif child.tag == "sticker":
                    if child.get("media_id"):
                        # 暂时先这样，做好偷表情包再修这里
                        media_caption = await self.data_cache.get_caption_by_hash(
                            child.get("media_id", "")
                        )
                        if media_caption and media_caption.url:
                            result.msg_chains.append([Image.fromURL(media_caption.url)])
                        else:
                            logger.error(
                                f"未找到图片: {child.get('media_id')}, xml_str: {xml_str[:1000]}"
                            )
                # 贴表情
                elif child.tag == "emoji_like":
                    if child.get("message_id") and child.get("emoji_id"):
                        result.emoji_ids.append((
                            child.get("message_id", ""),
                            child.get("emoji_id", ""),
                        ))
                    else:
                        logger.error(
                            f"贴表情数据不完整: {child.attrib}, xml_str: {xml_str[:1000]}"
                        )
                # 撤回消息
                elif child.tag == "delete":
                    # 优先获取属性，如果属性为空，则尝试获取标签内部的文本
                    msg_id = child.get("message_id") or (
                        child.text.strip() if child.text else ""
                    )
                    if msg_id:
                        result.delete_message_ids.append(msg_id)
                # 点赞
                elif child.tag == "like":
                    if child.get("user_id") and child.get("count"):
                        result.likes.append((
                            child.get("user_id", ""),
                            child.get("count", ""),
                        ))
                    else:
                        logger.error(
                            f"点赞标签数据不完整: {child.attrib}, xml_str: {xml_str[:1000]}"
                        )
                # 戳一戳
                elif child.tag == "poke":
                    if child.get("user_id"):
                        result.poke.append((
                            child.get("group_id", "") or group_or_user_id,
                            child.get("user_id", ""),
                        ))
                    else:
                        logger.error(
                            f"戳一戳标签数据不完整: {child.attrib}, xml_str: {xml_str[:1000]}"
                        )
                # 禁言
                elif child.tag == "ban":
                    if child.get("user_id"):
                        result.ban.append((
                            child.get("group_id", "") or group_or_user_id,
                            child.get("user_id", ""),
                            child.get("duration", ""),
                        ))
                    else:
                        logger.error(
                            f"禁言标签数据不完整: {child.attrib}, xml_str: {xml_str[:1000]}"
                        )
                # 用户画像
                elif child.tag == "summary_user_profile":
                    if child.text:
                        user_id = child.get("user_id")
                        if user_id:
                            result.summary_user_profiles.append((
                                group_or_user_id,
                                user_id,
                                child.text.strip(),
                            ))
                # 群画像
                elif child.tag == "summary_group_profile":
                    if child.text:
                        result.summary_group_profiles.append((
                            group_or_user_id,
                            child.text.strip(),
                        ))
                # 记忆
                elif child.tag == "save_memory":
                    if child.text:
                        result.save_memories.append((
                            group_or_user_id,
                            child.text.strip(),
                        ))
                elif child.tag == "search_memory":
                    if child.text:
                        result.search_memories.append((
                            group_or_user_id,
                            child.text.strip(),
                        ))
                # 工具调用
                elif child.tag == "tool_call":
                    tool_name = child.get("name")
                    if tool_name and child.text:
                        arg_dict = self.parse_str_json(child.text)
                        if arg_dict:
                            result.tools_to_call.append((tool_name, arg_dict))
                    else:
                        logger.error(
                            f"Tool call数据不完整: {child.attrib}, xml_str: {xml_str[:1000]}"
                        )
                # 设置定时任务
                elif child.tag == "schedule_task":
                    task_time = child.get("time")
                    if task_time and child.text:
                        result.schedule_tasks.append((
                            group_or_user_id,
                            task_time,
                            child.text.strip(),
                        ))
                    else:
                        logger.error(
                            f"Schedule task数据不完整: {child.attrib}, xml_str: {xml_str[:1000]}"
                        )
                # 删除定时任务
                elif child.tag == "delete_task":
                    task_id = child.get("task_id")
                    if task_id:
                        result.delete_schedule_tasks.append(task_id)
                    else:
                        logger.error(
                            f"Delete task数据不完整: {child.attrib}, xml_str: {xml_str[:1000]}"
                        )

            return result
        except Exception as e:
            logger.error(f"解析LLM XML失败: {e}, xml_str: {xml_str[:1000]}")
            return None

    def decode_media_caption_xml(self, xml_str: str) -> MediaCaption | None:
        """解码媒体图片描述XML字符串"""
        # 初始化
        result = MediaCaption()
        result.media_type = "image"
        try:
            # 开始解析
            clean_str = re.sub(
                r"```[a-zA-Z]*\s*|\s*```", "", xml_str, flags=re.IGNORECASE
            ).strip()
            wrapped_data = f"<root>{clean_str}</root>"
            root = ET.fromstring(wrapped_data)

            for child in root:
                if child.tag == "caption":
                    result.genre = child.get("genre", "")
                    result.character = child.get("character", "")
                    result.source = child.get("source", "")
                    result.text = child.get("text", "")
                    result.caption = child.text.strip() if child.text else ""
            # 检查数据是否有效
            if result.caption == "":
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
        # 初始化
        result = MediaCaption()
        result.media_type = "audio"
        try:
            # 开始解析
            clean_str = re.sub(
                r"```[a-zA-Z]*\s*|\s*```", "", xml_str, flags=re.IGNORECASE
            ).strip()
            wrapped_data = f"<root>{clean_str}</root>"
            root = ET.fromstring(wrapped_data)

            for child in root:
                if child.tag == "caption":
                    result.genre = child.get("genre", "")
                    result.character = child.get("character", "")
                    result.source = child.get("source", "")
                    result.text = child.get("text", "")
                    result.caption = child.text.strip() if child.text else ""
            # 检查数据是否有效
            if result.caption == "":
                logger.warning(
                    f"媒体语音描述数据无效: {result}, xml_str: {xml_str[:1000]}"
                )
                return None
            return result
        except Exception as e:
            logger.error(f"解析媒体语音描述XML失败: {e}, xml_str: {xml_str[:1000]}")
            return None

    def parse_str_json(self, response_text: str) -> dict | None:
        """
        把AI回复的json字符串解析成字典
        """
        # 清理markdown代码块标记
        clean_text = response_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()

        # 正则取花括号内容
        match = re.search(r"\{.*\}", clean_text, re.DOTALL)
        if match:
            clean_text = match.group(0)
        else:
            logger.error(f"未找到JSON对象，原始文本: {response_text[:1000]}")
            return None

        # 尝试解析
        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            try:
                # 解决单引号问题
                return ast.literal_eval(clean_text)
            except Exception as e:
                logger.error(f"解析JSON失败: {e}, clean_text: {clean_text[:1000]}")
                return None

    def mask_think_content(self, xml_raw):
        # 找到 <think> 和 </think> 之间的所有内容
        def escape_match(match):
            content = match.group(1)
            # 仅对内部的尖括号进行转义
            safe_content = content.replace("<", "&lt;").replace(">", "&gt;")
            return f"<think>{safe_content}</think>"

        # 使用非贪婪匹配获取 think 块
        return re.sub(r"<think>(.*?)</think>", escape_match, xml_raw, flags=re.DOTALL)
