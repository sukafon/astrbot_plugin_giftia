import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from aiocqhttp import CQHttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import At
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
    AiocqhttpAdapter,
)

from ..utils.schemas import XmlLlmResult
from .action_dispatcher import ActionDispatcher
from .decision_engine import DecisionEngine
from .reply_pipeline import ReplyPipeline


class ChatManager:
    def __init__(self, plugin):
        self.plugin = plugin
        self.decision_engine = DecisionEngine(plugin)
        self.reply_pipeline = ReplyPipeline(plugin)
        self.action_dispatcher = ActionDispatcher(plugin)

    async def handle_message(self, event: AstrMessageEvent):
        """接收并处理消息"""
        # 1. 检查白名单拦截
        if not self.decision_engine.check_whitelists(event):
            return

        # 2. 处理撤回消息通知
        if hasattr(event.message_obj, "raw_message") and event.message_obj.raw_message:
            raw_message = event.message_obj.raw_message
            message_name = getattr(raw_message, "name", "")
            if message_name in ["notice.group_recall", "notice.friend_recall"]:
                recalled_message_id = str(getattr(raw_message, "message_id", ""))
                bot_name = self.plugin.adapter_id_map.get(event.platform_meta.id)
                if bot_name and recalled_message_id:
                    group_or_user_id = event.get_group_id() or event.get_sender_id()
                    try:
                        await self.plugin.data_cache.set_message_recalled(
                            bot_name, group_or_user_id, [recalled_message_id]
                        )
                        logger.debug(
                            f"{bot_name} 收到撤回消息事件，已标注消息 {recalled_message_id} 为撤回"
                        )
                    except Exception as e:
                        logger.error(f"处理撤回消息失败: {e}")
                return

        # 3. 跳过机器人自己的发言
        if event.get_sender_id() == event.get_self_id():
            logger.debug(f"{event.platform_meta.id} 消息为机器人自己的消息，跳过处理")
            return

        # 4. 创建后台回复任务
        task = asyncio.create_task(self.job(event))
        task_id = str(id(task))
        self.plugin.running_tasks[task_id] = task
        try:
            await task

            # 被动记忆后台触发检查
            if self.plugin.passive_memory_enabled and self.plugin.embedding_conf.get("enabled", False):
                bot_name = self.plugin.adapter_id_map.get(event.platform_meta.id)
                group_or_user_id = event.get_group_id() or event.get_sender_id()
                if bot_name:
                    asyncio.create_task(
                        self.plugin.passive_memory_manager.check_and_trigger_passive_memory(
                            bot_name=bot_name,
                            group_or_user_id=group_or_user_id,
                            self_id=event.get_self_id(),
                        )
                    )
        except asyncio.CancelledError:
            logger.info(f"{task_id} 任务被取消")
        except Exception as e:
            logger.error(f"{task_id} 任务执行失败: {e}", exc_info=True)
        finally:
            self.plugin.running_tasks.pop(task_id, None)

    async def job(self, event: AstrMessageEvent):
        # 获取基础信息
        bot_name = self.plugin.adapter_id_map[event.platform_meta.id]
        bot_conf = self.plugin.bot_map[bot_name]
        nickname = bot_conf.get("nickname", bot_name)
        group_or_user_id = event.get_group_id() or event.get_sender_id()

        # 检查是否开启延迟多媒体转述 (仅在没有 @ 且不在发言窗口时延迟)
        caption_config = bot_conf.get("caption_config", {})
        defer_enabled = caption_config.get("defer_caption_enabled", True)

        should_defer = False
        if defer_enabled:
            is_just_at = any(
                isinstance(c, At) and str(c.qq) == event.get_self_id()
                for c in event.get_messages()
            )
            is_private = not event.get_group_id()
            if is_private and self.plugin.private_chat_bypass:
                is_just_at = True

            fmt_key = f"{bot_name}:{group_or_user_id}"
            active_counter = self.plugin.active_reply_counters.get(fmt_key, 0)
            is_active_window = active_counter > 0

            if not is_just_at and not is_active_window:
                should_defer = True

        # 解析用户消息并缓存多媒体
        async with self.plugin.parse_locks[f"{bot_name}:{group_or_user_id}"]:
            (
                current_message,
                image_urls,
                audio_urls,
            ) = await self.plugin.message_parser.parse_user_message(
                event, bot_name, defer_caption=should_defer
            )

        # 5. 调用决策引擎进行发言判断
        should_reply, relevant_memories, is_just_at = await self.decision_engine.evaluate_decision(
            event=event,
            bot_name=bot_name,
            nickname=nickname,
            group_or_user_id=group_or_user_id,
            current_message=current_message,
            image_urls=image_urls,
            audio_urls=audio_urls,
        )

        if not should_reply:
            return

        # 6. 进入 LLM 回复流水线
        reply_key = f"{bot_name}:{group_or_user_id}"
        self.plugin.replying_status[reply_key] = self.plugin.replying_status.get(reply_key, 0) + 1

        try:
            has_sent_reply = False
            async for chunk in self.reply_pipeline.dispatch_llm_reply_loop(
                event=event,
                bot_name=bot_name,
                nickname=nickname,
                group_or_user_id=group_or_user_id,
                current_message=current_message,
                image_urls=image_urls,
                audio_urls=audio_urls,
                relevant_memories=relevant_memories,
            ):
                if chunk:
                    if isinstance(chunk, XmlLlmResult):
                        # 派发具体写操作和消息发送
                        await self.action_dispatcher.dispatch_actions(
                            event=event,
                            bot_name=bot_name,
                            nickname=nickname,
                            group_or_user_id=group_or_user_id,
                            llm_result=chunk,
                        )
                        if chunk.msg_chains:
                            has_sent_reply = True
                else:
                    logger.error(f"{bot_name} 生成消息失败，收到空消息块")

            if has_sent_reply:
                fmt_key = f"{bot_name}:{group_or_user_id}"
                decision_conf = bot_conf.get("decision_conf", {})
                window_size = decision_conf.get("reply_active_window", 10)
                self.plugin.active_reply_counters[fmt_key] = window_size
                logger.info(
                    f"{bot_name} 机器人发言，重置接话分析窗口计数为 {window_size}"
                )
        finally:
            self.plugin.replying_status[reply_key] = max(
                0, self.plugin.replying_status.get(reply_key, 0) - 1
            )

    def get_platform_adapter(
        self, adapter_id: str
    ) -> tuple[CQHttp, PlatformMetadata] | None:
        """获取平台适配器实例，目前仅支持aiocqhttp"""
        platforms = self.plugin.context.platform_manager.get_insts()
        for p in platforms:
            if isinstance(p, AiocqhttpAdapter) and p.metadata.id == adapter_id:
                return p.bot, p.metadata
        return None

    async def remind_task(
        self,
        unified_msg_origin: str,
        adapter_id: str,
        bot_name: str,
        nickname: str,
        self_id: str,
        platform_name: str,
        user_id: str,
        user_name: str,
        group_id: str,
        group_or_user_id: str,
        remind_message: str,
    ):
        """处理定时任务调度提醒"""
        mock_event = self.fake_event(
            self_id=self_id,
            sender_id=user_id,
            sender_name=user_name,
            group_id=group_id,
            unified_msg_origin=unified_msg_origin,
            adapter_id=adapter_id,
        )
        has_sent_reply = False
        async for chunk in self.reply_pipeline.dispatch_llm_reply_loop(
            event=mock_event,
            bot_name=bot_name,
            nickname=nickname,
            group_or_user_id=group_or_user_id,
            remind_message=f"[定时任务唤醒] {user_name}({user_id}): {remind_message}",
        ):
            if chunk:
                if isinstance(chunk, XmlLlmResult):
                    if platform_name == "aiocqhttp":
                        if mock_event:
                            await self.action_dispatcher.dispatch_actions(
                                event=mock_event,
                                bot_name=bot_name,
                                nickname=nickname,
                                group_or_user_id=group_or_user_id,
                                llm_result=chunk,
                            )
                            if chunk.msg_chains:
                                has_sent_reply = True
                            continue
                    # 降级到普通消息发送
                    if not chunk.msg_chains:
                        continue
                    for msg_chain in chunk.msg_chains:
                        await self.plugin.context.send_message(
                            unified_msg_origin, MessageChain(msg_chain)
                        )
                        has_sent_reply = True
            else:
                logger.error(f"{bot_name} 定时任务调度失败，未获取到回复内容")

        if has_sent_reply:
            fmt_key = f"{bot_name}:{group_or_user_id}"
            bot_conf = self.plugin.bot_map.get(bot_name, {})
            decision_conf = bot_conf.get("decision_conf", {})
            window_size = decision_conf.get("reply_active_window", 10)
            self.plugin.active_reply_counters[fmt_key] = window_size
            logger.info(
                f"{bot_name} 定时任务发言，重置接话分析窗口计数为 {window_size}"
            )

    def fake_event(
        self,
        self_id: str,
        sender_id: str,
        sender_name: str,
        group_id: str,
        unified_msg_origin: str,
        adapter_id: str,
    ) -> AstrMessageEvent:
        """伪造一个aiocqhttp的event，用于主动消息复用被动消息函数"""
        mock_event = MagicMock(spec=AiocqhttpMessageEvent)
        adapter = self.get_platform_adapter(adapter_id)
        if adapter:
            bot, metadata = adapter
            mock_event.bot = bot
            mock_event.platform_meta = metadata
        mock_event.get_platform_name = MagicMock(return_value="aiocqhttp")
        mock_event.get_group = AsyncMock(return_value="")
        mock_event.get_self_id = MagicMock(return_value=self_id)
        mock_event.get_group_id = MagicMock(return_value=group_id)
        mock_event.get_sender_id = MagicMock(return_value=sender_id)
        mock_event.get_sender_name = MagicMock(return_value=sender_name)
        mock_event.unified_msg_origin = unified_msg_origin
        return mock_event
