import asyncio
import json

from astrbot.api import logger

from .passive_tasks import PassiveSummaryTaskMixin


class PassiveMemoryManager(PassiveSummaryTaskMixin):
    def __init__(self, plugin):
        self.plugin = plugin
        self.initialized_keys = set()

    async def mark_silence_summary_armed(
        self, bot_name: str, group_or_user_id: str, trigger_msg_id: str = None
    ) -> None:
        """bot 发言后重新武装一次静默总结"""
        if not self.plugin.passive_memory_enabled:
            return
        fmt_key = f"{bot_name}:{group_or_user_id}"
        await self.plugin.db.upsert_kv_data(
            f"passive_memory:silence_armed:{fmt_key}", 1
        )
        await self.plugin.db.upsert_kv_data(f"passive_memory:silent_count:{fmt_key}", 0)

        # 如果提供了触发消息的 ID（说明机器人在此前处于不活跃状态被唤醒），
        # 推进 last_summarized_id 到该触发消息的前一位，跳过这期间从未见过的群友对话。
        if trigger_msg_id:
            db_msg_id = await self.plugin.db.get_database_id_by_message_id(
                message_id=trigger_msg_id,
                group_or_user_id=group_or_user_id,
                bot_name=bot_name,
            )
            if db_msg_id:
                last_summarized_id = await self.plugin.db.get_kv_data(
                    f"passive_memory:last_summarized_id:{fmt_key}", 0
                )
                if db_msg_id - 1 > last_summarized_id:
                    logger.info(
                        f"[Giftia Passive Memory] 机器人从不活跃中被唤醒。将 last_summarized_id 从 {last_summarized_id} 推进到 {db_msg_id - 1}，跳过未见过的消息。"
                    )
                    await self.plugin.db.upsert_kv_data(
                        f"passive_memory:last_summarized_id:{fmt_key}", db_msg_id - 1
                    )

    async def search_and_filter_memories(
        self,
        bot_name: str,
        group_or_user_id: str,
        query: str,
        recent_messages: list = None,
        limit: int = 5,
        threshold: float = 0.7,
    ) -> list[dict]:
        """语义搜索并根据当前上下文窗口的活跃用户过滤记忆"""
        embedding_memories = await self.plugin.ltm.search_memory(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            query=query,
            limit=limit,
            threshold=threshold,
        )
        if not embedding_memories:
            return []

        if recent_messages is None:
            recent_messages = await self.plugin.data_cache.get_recent_message(
                bot_name=bot_name,
                group_id=group_or_user_id,
                limit=self.plugin.msg_number,
            )

        active_users = {str(msg.user_id) for msg in recent_messages if msg.user_id}

        filtered_memories = []
        for memory in embedding_memories:
            metadata_str = memory.get("metadata", "{}")
            try:
                meta = json.loads(metadata_str) if metadata_str else {}
            except Exception:
                meta = {}

            associated_ids = meta.get("associated_user_ids", [])
            if not associated_ids:
                filtered_memories.append(memory)
                continue

            associated_ids_str = {str(uid) for uid in associated_ids}
            if associated_ids_str & active_users:
                filtered_memories.append(memory)

        return filtered_memories

    async def check_and_trigger_passive_memory(
        self,
        bot_name: str,
        group_or_user_id: str,
        self_id: str,
    ):
        """检查并触发被动记忆/状态更新总结"""
        if not self.plugin.passive_memory_enabled:
            return

        fmt_key = f"{bot_name}:{group_or_user_id}"

        # 如果机器人既不处于活跃计数窗口中，也未武装静默总结，说明处于闲置状态，直接返回。
        # 此时无需执行任何数据库查询和计算，唤醒时 B 逻辑会自动推进边界并跳过闲置期。
        active_counter = self.plugin.active_reply_counters.get(fmt_key, 0)
        silence_armed = await self.plugin.db.get_kv_data(
            f"passive_memory:silence_armed:{fmt_key}", 0
        )
        if active_counter == 0 and not silence_armed:
            return

        if not hasattr(self, "passive_memory_locks"):
            self.passive_memory_locks = {}
        if fmt_key not in self.passive_memory_locks:
            self.passive_memory_locks[fmt_key] = asyncio.Lock()

        async with self.passive_memory_locks[fmt_key]:
            max_id = await self.plugin.db.get_max_message_id(bot_name, group_or_user_id)
            if max_id == 0:
                return

            last_summarized_id = await self.plugin.db.get_kv_data(
                f"passive_memory:last_summarized_id:{fmt_key}", 0
            )

            if last_summarized_id > max_id:
                logger.info(
                    f"[Giftia Passive Memory] 检测到 max_id ({max_id}) 小于 last_summarized_id ({last_summarized_id})，将 last_summarized_id 重置为 {max_id}。"
                )
                last_summarized_id = max_id
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:last_summarized_id:{fmt_key}", last_summarized_id
                )

            if last_summarized_id == 0:
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:last_summarized_id:{fmt_key}", max_id
                )
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:silent_count:{fmt_key}", 0
                )
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:silence_armed:{fmt_key}", 0
                )
                self.initialized_keys.add(fmt_key)
                return

            boundary_id = await self.plugin.db.get_boundary_message_id(
                bot_name, group_or_user_id, self.plugin.msg_number
            )

            # 首次检查：如果未总结的消息范围太旧（已经超出了当前上下文窗口），直接跳过（这通常是离线期间的消息）
            if fmt_key not in self.initialized_keys:
                if boundary_id > last_summarized_id:
                    logger.info(
                        f"[Giftia Passive Memory] 检测到离线期间未见过的消息。将 last_summarized_id 从 {last_summarized_id} 推进到 {boundary_id}，跳过离线消息。"
                    )
                    last_summarized_id = boundary_id
                    await self.plugin.db.upsert_kv_data(
                        f"passive_memory:last_summarized_id:{fmt_key}",
                        last_summarized_id,
                    )
                    # 重置静默武装状态，防止旧状态被残留唤醒
                    await self.plugin.db.upsert_kv_data(
                        f"passive_memory:silence_armed:{fmt_key}", 0
                    )
                    await self.plugin.db.upsert_kv_data(
                        f"passive_memory:silent_count:{fmt_key}", 0
                    )
                self.initialized_keys.add(fmt_key)

            if max_id <= last_summarized_id:
                return

            active_counter = self.plugin.active_reply_counters.get(fmt_key, 0)

            trigger_type = None
            start_id = last_summarized_id + 1
            end_id = max_id
            silence_armed = await self.plugin.db.get_kv_data(
                f"passive_memory:silence_armed:{fmt_key}", 0
            )

            if boundary_id > last_summarized_id:
                overflow_count = await self.plugin.db.get_message_count_by_id_range(
                    bot_name, group_or_user_id, last_summarized_id + 1, boundary_id
                )
                if overflow_count >= self.plugin.passive_memory_overflow_threshold:
                    trigger_type = "overflow"
                    end_id = boundary_id

            if trigger_type is None and active_counter == 0 and silence_armed:
                silent_count = await self.plugin.db.get_kv_data(
                    f"passive_memory:silent_count:{fmt_key}", 0
                )
                silent_count += 1
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:silent_count:{fmt_key}", silent_count
                )

                if silent_count >= self.plugin.passive_memory_silence_threshold:
                    trigger_type = "silence"
                    end_id = max_id
            elif active_counter > 0:
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:silent_count:{fmt_key}", 0
                )

            if trigger_type:
                logger.info(
                    f"[Giftia Passive Memory] 触发被动总结 ({trigger_type}). "
                    f"范围: {start_id} 到 {end_id}"
                )
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:silent_count:{fmt_key}", 0
                )
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:last_summarized_id:{fmt_key}", end_id
                )
                if trigger_type == "silence":
                    await self.plugin.db.upsert_kv_data(
                        f"passive_memory:silence_armed:{fmt_key}", 0
                    )

                asyncio.create_task(
                    self._run_background_summarize(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        self_id=self_id,
                        start_id=start_id,
                        end_id=end_id,
                    )
                )

    async def force_trigger_passive_memory(
        self,
        bot_name: str,
        group_or_user_id: str,
        self_id: str,
    ) -> str:
        """手动强制总结，并返回处理结果状态"""
        if not self.plugin.passive_memory_enabled:
            return "被动记忆功能未启用"

        fmt_key = f"{bot_name}:{group_or_user_id}"

        if not hasattr(self, "passive_memory_locks"):
            self.passive_memory_locks = {}
        if fmt_key not in self.passive_memory_locks:
            self.passive_memory_locks[fmt_key] = asyncio.Lock()

        if self.passive_memory_locks[fmt_key].locked():
            return "当前会话正在进行总结，请稍后再试..."

        async with self.passive_memory_locks[fmt_key]:
            max_id = await self.plugin.db.get_max_message_id(bot_name, group_or_user_id)
            last_summarized_id = await self.plugin.db.get_kv_data(
                f"passive_memory:last_summarized_id:{fmt_key}", 0
            )

            if last_summarized_id > max_id:
                logger.info(
                    f"[Giftia Passive Memory] 检测到 max_id ({max_id}) 小于 last_summarized_id ({last_summarized_id})，将 last_summarized_id 重置为 {max_id}。"
                )
                last_summarized_id = max_id
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:last_summarized_id:{fmt_key}", last_summarized_id
                )

            if max_id <= last_summarized_id or max_id == 0:
                return "当前会话暂无未总结的消息！"

            start_id = last_summarized_id + 1
            end_id = max_id

            db_messages = await self.plugin.db.get_messages_by_id_range(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                start_id=start_id,
                end_id=end_id,
            )
            if not db_messages:
                return "无有效消息内容。"

            # 遵循发言检测
            bot_participated = any(
                str(msg.user_id) == str(self_id) for msg in db_messages
            )
            if not bot_participated:
                logger.debug(
                    f"[Giftia Passive Memory] 强制总结区间 {start_id}-{end_id} 内机器人未参与，仅检查用户画像候选。"
                )

            # 推进状态边界，避免重入
            await self.plugin.db.upsert_kv_data(
                f"passive_memory:silent_count:{fmt_key}", 0
            )
            await self.plugin.db.upsert_kv_data(
                f"passive_memory:silence_armed:{fmt_key}", 0
            )
            await self.plugin.db.upsert_kv_data(
                f"passive_memory:last_summarized_id:{fmt_key}", end_id
            )

            try:
                # 同步调用 _run_background_summarize 以同步获取反馈
                await self._run_background_summarize(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    self_id=self_id,
                    start_id=start_id,
                    end_id=end_id,
                )
                if not bot_participated:
                    return (
                        f"该区间内机器人未参与发言，已检查用户画像候选"
                        f"（消息范围: id {start_id} 到 {end_id}）。"
                    )
                return f"成功处理了 {len(db_messages)} 条消息的被动总结（消息范围: id {start_id} 到 {end_id}）。"
            except Exception as e:
                # 失败时回滚边界
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:last_summarized_id:{fmt_key}", last_summarized_id
                )
                logger.error(f"强制提炼记忆执行失败: {e}", exc_info=True)
                return f"提炼记忆失败: {e}"
