import asyncio
import copy
import random
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image

from ..llm.prompt import build_reply_prompt
from ..utils.schemas import MessageData, XmlLlmResult
from .media_captioner import MediaCaptioner
from .tool_executor import ToolExecutor


class ReplyPipeline:
    def __init__(self, plugin):
        self.plugin = plugin
        self.media_captioner = MediaCaptioner(plugin)
        self.tool_executor = ToolExecutor(plugin)

    async def dispatch_llm_reply_loop(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        nickname: str,
        group_or_user_id: str,
        current_message: MessageData | None = None,
        remind_message: str | None = None,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        relevant_memories: list[str] | None = None,
        tool_results: list[dict[str, str]] | None = None,
        other_data: list[str] | None = None,
        times: int = 0,
    ):
        """
        集成用户提示词构建、LLM调用、生成表情包与工具执行，支持递归的工具执行循环。
        """
        bot_conf = self.plugin.bot_map[bot_name]
        iso_string = datetime.now().isoformat()
        max_loop = self.plugin.tools_config.get("max_loop", 10)

        if times >= max_loop:
            logger.warning(
                f"{bot_name} 达到最大工具调用次数 ({max_loop})，强制退出循环"
            )
            # 记录系统指令提示到数据库
            await self.plugin.data_cache.add_message(
                bot_name,
                group_or_user_id,
                MessageData(
                    nickname=nickname,
                    user_id=event.get_self_id(),
                    group_or_user_id=group_or_user_id,
                    time=iso_string,
                    message_id="",
                    content=f"系统提示：当前已经达到最大工具调用次数 {max_loop}，请立即停止调用工具，并以现有信息作为最终结果进行回复。",
                    is_recalled=False,
                    media_id_list=[],
                    role="operation_log",
                ),
            )

        # 1. 获取近期消息上下文并处理延迟转述
        recent_messages = await self.plugin.data_cache.get_recent_message(
            bot_name, group_or_user_id, self.plugin.msg_number
        )

        caption_config = bot_conf.get("caption_config", {})
        media_captions = await self.media_captioner.transcribe_media_if_deferred(
            bot_name=bot_name,
            recent_messages=recent_messages,
            caption_config=caption_config,
        )

        # 2. 读取画像、关系与状态数据
        bot_status = await self.plugin.data_cache.get_bot_status(
            bot_name=bot_name,
            group_id=group_or_user_id,
        )
        user_profile = await self.plugin.data_cache.get_user_profile(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            user_id=event.get_sender_id(),
        )
        group_profile = await self.plugin.data_cache.get_group_profile(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
        )
        user_relation = await self.plugin.data_cache.get_user_relation(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            user_id=event.get_sender_id(),
        )

        # 读取长期记忆
        long_memories = []
        if self.plugin.embedding_conf.get("enabled", False):
            long_memories = await self.plugin.data_cache.get_memories(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                limit=self.plugin.embedding_conf.get("inject_limit", 20),
            )

        # 获取表情包池并抽取随机样本
        bot_sticker_cache = await self.plugin.emoji_manager.get_random_stickers(bot_name)

        # 3. 构造回复提示词 Prompt
        user_prompt = build_reply_prompt(
            recent_messages=recent_messages,
            media_captions=media_captions,
            current_message=current_message,
            remind_message=remind_message,
            group_data=str(
                await event.get_group(event.get_group_id())
                if event.get_group_id()
                else ""
            ),
            user_id=event.get_sender_id(),
            nickname=nickname,
            bot_status=bot_status,
            tool_results=tool_results,
            long_memories=long_memories,
            relevant_memories=relevant_memories,
            user_profile=user_profile,
            group_profile=group_profile,
            other_data=other_data,
            user_relation=user_relation,
            bot_sticker=bot_sticker_cache,
        )
        logger.debug(f"[Giftia] 触发大模型回复,构造回复提示词：{user_prompt}")

        llm_reply_conf = bot_conf.get("llm_reply_conf", {})
        provider_ids = llm_reply_conf.get("provider_ids")
        if not provider_ids:
            old_provider_id = llm_reply_conf.get("provider_id")
            if old_provider_id:
                provider_ids = [old_provider_id] + llm_reply_conf.get(
                    "fallback_provider_ids", []
                )
            else:
                logger.error(f"{bot_name} 未配置回复模型ID")
                return
        provider_ids = [p for p in provider_ids if p]
        if not provider_ids:
            logger.error(f"{bot_name} 未配置回复模型ID")
            return

        provider_selection_mode = llm_reply_conf.get("provider_selection_mode", "fallback")
        if provider_selection_mode == "random":
            random.shuffle(provider_ids)

        # 4. 调用 LLM 进行回复
        llm_result = await self.plugin.call_llm.call_llm_reply(
            event=event,
            group_or_user_id=group_or_user_id,
            provider_ids=provider_ids,
            system_prompt=llm_reply_conf.get("llm_reply_prompt"),
            user_prompt=user_prompt,
            use_source_tools=self.plugin.tools_config.get("use_source_tools", False),
            force_xml_tools=self.plugin.tools_config.get("force_xml_tools", False),
            enabled_features=self.plugin.tools_config.get("enabled_interactive_features"),
            image_urls=image_urls,
            audio_urls=audio_urls,
            timeout=self.plugin.tools_config.get("timeout", 120),
        )

        if not llm_result:
            logger.error(f"{bot_name} LLM回复失败")
            return

        # 5. 空回复拦截处理
        if not remind_message and times == 0 and not llm_result.msg_chains:
            await self.plugin.db.update_message_reply_decision(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                message_id=current_message.message_id if current_message else "",
                reply_decision=0,
            )

        # 6. 更新机器人状态
        if llm_result.status:
            await self.plugin.data_cache.set_bot_status(
                bot_name=bot_name,
                group_id=group_or_user_id,
                status=llm_result.status,
            )

        # 7. 后台分析与添加表情包
        if llm_result.add_stickers:
            asyncio.create_task(
                self.media_captioner.analyze_and_add_stickers(
                    event=event,
                    bot_name=bot_name,
                    nickname=nickname,
                    group_or_user_id=group_or_user_id,
                    llm_result=llm_result,
                )
            )

        # 8. 产生 LLM 回复结果供 Dispatcher 处理
        yield llm_result

        # 初始化参数用于潜在的下一次递归
        if other_data is None:
            other_data = []
        if relevant_memories is None:
            relevant_memories = []
        if tool_results is None:
            tool_results = []

        # 9. 调用 ToolExecutor 执行工具和搜索查询
        image_base64 = await self.tool_executor.execute_tools_and_queries(
            event=event,
            bot_name=bot_name,
            nickname=nickname,
            group_or_user_id=group_or_user_id,
            llm_result=llm_result,
            recent_messages=recent_messages,
            relevant_memories=relevant_memories,
            other_data=other_data,
            tool_results=tool_results,
            times=times,
        )

        # 如果工具调用产生了图片，立即发送
        if image_base64:
            yield await event.send(
                MessageChain([Image.fromBase64(b64) for b64 in image_base64])
            )
            logger.info(
                f"{bot_name} 从MCP工具收到 {len(image_base64)} 张图片，直接发出去了"
            )

        # 10. 判断是否需要继续循环迭代
        if (
            len(llm_result.tools_to_call) > 0
            or len(llm_result.search_memories) > 0
            or len(llm_result.all_tasks) > 0
            or len(llm_result.search_histories) > 0
            or len(llm_result.get_message_contexts) > 0
        ):
            logger.debug(f"{bot_name} llm step {times + 1} ...")
            async for chunk in self.dispatch_llm_reply_loop(
                event=event,
                bot_name=bot_name,
                nickname=nickname,
                group_or_user_id=group_or_user_id,
                current_message=current_message,
                relevant_memories=relevant_memories,
                tool_results=tool_results,
                remind_message=remind_message,
                image_urls=image_base64,
                times=times + 1,
                other_data=other_data,
            ):
                yield chunk
