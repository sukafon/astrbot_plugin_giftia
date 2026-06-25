import asyncio
import random
import re
import uuid
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from ..utils.schemas import MessageData, XmlLlmResult


class ActionDispatcher:
    def __init__(self, plugin):
        self.plugin = plugin

    async def dispatch_actions(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        nickname: str,
        group_or_user_id: str,
        llm_result: XmlLlmResult,
    ):
        """
        根据 LLM 结果派发具体写操作（OneBot API 或是通用消息发送）。
        """
        if not isinstance(llm_result, XmlLlmResult):
            logger.warning(
                f"派发动作遇到非 XmlLlmResult 类型的结果，类型为: {type(llm_result)}，跳过动作派发"
            )
            return

        # 区分 aiocqhttp 平台与其它通用平台
        if event.get_platform_name() == "aiocqhttp" and isinstance(
            event, AiocqhttpMessageEvent
        ):
            success_logs = []
            iso_string = datetime.now().isoformat()

            # 1. 删除长期记忆
            if llm_result.delete_memories and self.plugin.embedding_conf.get("enabled", False):
                for memory_id in llm_result.delete_memories:
                    result = await self.plugin.data_cache.delete_memory(memory_id=memory_id)
                    if result:
                        success_logs.append(
                            f"<delete_memory memory_id={memory_id} result='success'/>"
                        )
                    else:
                        success_logs.append(
                            f"<delete_memory memory_id={memory_id} result='failed'/>"
                        )

            # 2. 撤回消息
            if llm_result.delete_message_ids:
                try:
                    ids = [int(msg_id) for msg_id in llm_result.delete_message_ids]
                    err_msg = await self.plugin.aiocqhttp.delete_messages(
                        event=event, message_ids=ids
                    )
                    await self.plugin.data_cache.set_message_recalled(
                        bot_name, group_or_user_id, llm_result.delete_message_ids
                    )
                    success_logs.append(
                        f"<recall message_ids={llm_result.delete_message_ids} result={err_msg or 'success'}/>"
                    )
                except ValueError:
                    logger.error(
                        f"{bot_name} 撤回消息数据格式错误: {llm_result.delete_message_ids}"
                    )

            # 3. 消息贴表情点赞
            if llm_result.emoji_ids:
                for message_id, emoji_id in llm_result.emoji_ids:
                    try:
                        message_id_int = int(message_id)
                        emoji_id_int = int(emoji_id)
                        err_msg = await self.plugin.aiocqhttp.msg_emoji_like(
                            event=event,
                            message_id=message_id_int,
                            emoji_id=emoji_id_int,
                        )
                        success_logs.append(
                            f"<emoji_like message_id={message_id} emoji_id={emoji_id} result={err_msg or 'success'}/>"
                        )
                    except ValueError:
                        logger.error(
                            f"{bot_name} 贴表情数据格式错误: {message_id}, {emoji_id}"
                        )

            # 4. 点赞
            if llm_result.likes:
                for user_id, count in llm_result.likes:
                    try:
                        user_id_int = int(user_id)
                        count_int = int(count)
                        err_msg = await self.plugin.aiocqhttp.like(
                            event=event,
                            user_id=user_id_int,
                            count=count_int,
                        )
                        success_logs.append(
                            f"<like user_id={user_id} result={err_msg or 'success'}/>"
                        )
                    except ValueError:
                        logger.error(f"{bot_name} 点赞数据格式错误: {user_id}, {count}")

            # 5. 戳一戳
            if llm_result.poke:
                for group_id, user_id in llm_result.poke:
                    try:
                        group_id_int = int(group_id)
                        user_id_int = int(user_id)
                        err_msg = await self.plugin.aiocqhttp.group_poke(
                            event=event,
                            group_id=group_id_int,
                            user_id=user_id_int,
                        )
                        success_logs.append(
                            f"<poke user_id={user_id} result={err_msg or 'success'}/>"
                        )
                    except ValueError:
                        logger.error(
                            f"{bot_name} 戳一戳数据格式错误: {group_id}, {user_id}"
                        )

            # 6. 禁言
            if llm_result.ban:
                for group_id, user_id, duration in llm_result.ban:
                    try:
                        group_id_int = int(group_id)
                        user_id_int = int(user_id)
                        duration_int = int(duration)
                        err_msg = await self.plugin.aiocqhttp.group_ban(
                            event=event,
                            group_id=group_id_int,
                            user_id=user_id_int,
                            duration=duration_int,
                        )
                        success_logs.append(
                            f"<ban user_id={user_id} duration={duration} result={err_msg or 'success'}/>"
                        )
                    except ValueError:
                        logger.error(
                            f"{bot_name} 禁言数据格式错误: {group_id}, {user_id}, {duration}"
                        )

            # 7. 记录工具分发状态
            if llm_result.tools_to_call:
                for tool_name, tool_args in llm_result.tools_to_call:
                    success_logs.append(
                        f"<tool_call name={tool_name} args={tool_args} status='dispatched' info='The system has received the call and is processing it.'/>"
                    )

            # 8. 添加定时任务
            if llm_result.schedule_tasks:
                for group_id, time_expr, remind_content in llm_result.schedule_tasks:
                    task_id = f"{bot_name}_{group_or_user_id}_{uuid.uuid4().hex[:6]}"
                    kwargs = {
                        "unified_msg_origin": event.unified_msg_origin,
                        "adapter_id": event.platform_meta.id,
                        "bot_name": bot_name,
                        "nickname": nickname,
                        "self_id": event.get_self_id(),
                        "platform_name": event.get_platform_name(),
                        "user_id": event.get_sender_id(),
                        "user_name": event.get_sender_name(),
                        "group_id": event.get_group_id(),
                        "group_or_user_id": group_or_user_id,
                        "remind_message": remind_content,
                    }
                    err_msg = self.plugin.task_manager.add_job(
                        task_id,
                        "remind",
                        time_expr,
                        kwargs=kwargs,
                    )
                    success_logs.append(
                        f"<schedule_task task_id={task_id} time_expr={time_expr} result={err_msg or 'success'}/>"
                    )

            # 9. 删除定时任务
            if llm_result.delete_schedule_tasks:
                for task_id in llm_result.delete_schedule_tasks:
                    err_msg = self.plugin.task_manager.remove_job(task_id)
                    success_logs.append(
                        f"<delete_task task_id={task_id} result={err_msg or 'success'}/>"
                    )

            # 10. 添加表情包日志
            if llm_result.add_stickers:
                for sticker_id in llm_result.add_stickers:
                    success_logs.append(
                        f"<add_sticker media_id={sticker_id} result='success'/>"
                    )

            # 11. 发送消息链
            for index, msg_chain in enumerate(llm_result.msg_chains):
                if not msg_chain:
                    continue
                # 随机延迟发送
                if index > 0:
                    interval = random.randint(
                        self.plugin.min_reply_interval, self.plugin.max_reply_interval
                    )
                    await asyncio.sleep(interval)
                success, message_id = await self.plugin.aiocqhttp.send_message(
                    event,
                    msg_chain,
                )
                if success and message_id:
                    iso_string = datetime.now().isoformat()
                    msg_str = (
                        llm_result.msg_logs[index]
                        if llm_result.msg_logs and index < len(llm_result.msg_logs)
                        else ""
                    )
                    media_id_list = re.findall(r"\[图片:(.*?)\]", msg_str)
                    msg_data = MessageData(
                        nickname=nickname,
                        user_id=event.get_self_id(),
                        group_or_user_id=group_or_user_id,
                        time=iso_string,
                        message_id=str(message_id),
                        content=msg_str,
                        is_recalled=False,
                        media_id_list=media_id_list,
                    )
                    await self.plugin.data_cache.add_message(
                        bot_name, group_or_user_id, msg_data
                    )

            # 12. 踢人
            if llm_result.kick:
                for group_id, user_id in llm_result.kick:
                    try:
                        group_id_int = int(group_id)
                        user_id_int = int(user_id)
                        err_msg = await self.plugin.aiocqhttp.group_kick(
                            event=event,
                            group_id=group_id_int,
                            user_id=user_id_int,
                        )
                        success_logs.append(
                            f"<kick user_id={user_id} result={err_msg or 'success'}/>"
                        )
                    except ValueError:
                        logger.error(
                            f"{bot_name} 踢人数据格式错误: {group_id}, {user_id}"
                        )

            # 13. 退群
            if llm_result.leave:
                for group_id in llm_result.leave:
                    try:
                        group_id_int = int(group_id)
                        err_msg = await self.plugin.aiocqhttp.group_leave(
                            event=event,
                            group_id=group_id_int,
                        )
                        success_logs.append(
                            f"<leave user_id={event.get_self_id()} result={err_msg or 'success'}/>"
                        )
                    except ValueError:
                        logger.error(f"{bot_name} 退群数据格式错误: {group_id}")

            # 14. 记录总体操作日志
            if len(success_logs) > 0:
                await self.plugin.data_cache.add_message(
                    bot_name,
                    group_or_user_id,
                    MessageData(
                        nickname=nickname,
                        user_id=event.get_self_id(),
                        group_or_user_id=group_or_user_id,
                        time=iso_string,
                        message_id="",
                        content="\n".join(success_logs),
                        is_recalled=False,
                        media_id_list=[],
                        role="operation_log",
                    ),
                )
            return

        # 其它平台普通消息发送
        if llm_result.msg_chains:
            for index, msg_chain in enumerate(llm_result.msg_chains):
                if index > 0:
                    interval = random.randint(
                        self.plugin.min_reply_interval, self.plugin.max_reply_interval
                    )
                    await asyncio.sleep(interval)
                try:
                    await event.send(MessageChain(msg_chain))
                    iso_string = datetime.now().isoformat()
                    msg_str, media_id_list = await self.plugin.message_parser.chain_to_str(
                        msg_chain
                    )
                    msg_data = MessageData(
                        nickname=nickname,
                        user_id=event.get_self_id(),
                        group_or_user_id=group_or_user_id,
                        time=iso_string,
                        message_id="",
                        content=msg_str,
                        is_recalled=False,
                        media_id_list=media_id_list,
                    )
                    await self.plugin.data_cache.add_message(
                        bot_name, group_or_user_id, msg_data
                    )
                except Exception as e:
                    logger.error(f"{bot_name} 通用平台发送消息失败: {e}")
