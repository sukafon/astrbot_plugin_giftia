import re
import json
import asyncio
from datetime import datetime
from astrbot.api import logger
from ..llm.prompt import parse_caption_to_str

def format_time_to_seconds(db_value: str) -> str:
    if not db_value:
        return ""
    try:
        dt = datetime.fromisoformat(db_value)
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return db_value[:19] if len(db_value) >= 19 else db_value

class PassiveMemoryManager:
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
        await self.plugin.db.upsert_kv_data(
            f"passive_memory:silent_count:{fmt_key}", 0
        )

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
                        f"passive_memory:last_summarized_id:{fmt_key}", last_summarized_id
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

    async def _run_background_summarize(
        self,
        bot_name: str,
        group_or_user_id: str,
        self_id: str,
        start_id: int,
        end_id: int,
    ):
        """后台异步总结历史消息段，提炼记忆与状态"""
        try:
            db_messages = await self.plugin.db.get_messages_by_id_range(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                start_id=start_id,
                end_id=end_id,
            )
            if not db_messages:
                return

            bot_participated = any(str(msg.user_id) == str(self_id) for msg in db_messages)
            if not bot_participated:
                logger.debug(
                    f"[Giftia Passive Memory] {bot_name}:{group_or_user_id} 消息范围 {start_id}-{end_id} 内机器人没有直接参与，跳过 LLM 总结。"
                )
                return

            # 获取活跃的参与者
            active_users_in_range = {
                msg.user_id for msg in db_messages 
                if msg.user_id and str(msg.user_id) != str(self_id)
            }

            # 建立两套映射：
            # - nickname → user_id：用于把 LLM 输出里的昵称解析回 user_id
            # - user_id → nickname：用于在 user_prompt 里按 user_id 取到对应昵称
            nickname_to_user_id = {}
            user_id_to_nickname = {}
            for msg in db_messages:
                if msg.user_id and msg.nickname:
                    nickname_to_user_id[msg.nickname] = msg.user_id
                    user_id_to_nickname[msg.user_id] = msg.nickname

            # 读取现有画像与好感度/关系
            user_profiles_str = []
            user_relations_str = []
            for uid in active_users_in_range:
                profile = await self.plugin.data_cache.get_user_profile(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    user_id=uid,
                )
                if profile:
                    user_profiles_str.append(f"用户 {uid} ({user_id_to_nickname.get(uid, '')}) 现有画像:\n{profile}")

                relation_score, relation_title = await self.plugin.data_cache.get_user_relation(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    user_id=uid,
                )
                user_relations_str.append(
                    f"用户 {uid} ({user_id_to_nickname.get(uid, '')}) 的好感度得分: {relation_score}, 头衔: {relation_title or '无'}"
                )

            group_profile = await self.plugin.data_cache.get_group_profile(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
            )

            # 1. 搜集并分类媒体转述
            # 1. 搜集媒体转述并调用共享的辅助函数处理
            all_media_ids = []
            for msg in db_messages:
                if msg.media_id_list:
                    all_media_ids.extend(msg.media_id_list)
            unique_media_ids = list(dict.fromkeys(all_media_ids))

            media_captions = []
            for media_id in unique_media_ids:
                media_caption = await self.plugin.data_cache.get_caption_by_hash(media_id)
                if media_caption:
                    media_captions.append(media_caption)

            from ..llm.prompt import process_media_captions_for_prompt
            processed_messages, remaining_captions = process_media_captions_for_prompt(
                messages=db_messages,
                media_captions=media_captions,
                threshold=100,
            )

            # 2. 格式化聊天记录
            chat_history_lines = []
            for msg in processed_messages:
                chat_history_lines.append(
                    f"[{format_time_to_seconds(msg.time)}] {msg.nickname}({msg.user_id}): {msg.content or ''}"
                )
            chat_history_text = "\n".join(chat_history_lines)

            # 3. 构建 User Prompt，将 <media_content> 与 <chat_history> 并列
            user_prompt_parts = [
                f"<session_id>{group_or_user_id}</session_id>",
                f"<current_user_profiles>\n{'\n---\n'.join(user_profiles_str) if user_profiles_str else '无'}\n</current_user_profiles>",
                f"<current_relations>\n{'\n'.join(user_relations_str) if user_relations_str else '无'}\n</current_relations>",
                f"<current_group_profile>\n{group_profile or '无'}\n</current_group_profile>",
            ]
            if remaining_captions:
                media_captions_block = "\n".join(parse_caption_to_str(c) for c in remaining_captions)
                user_prompt_parts.append(f"<media_content>\n{media_captions_block}\n</media_content>")
            user_prompt_parts.append(f"<chat_history>\n{chat_history_text}\n</chat_history>")
            user_prompt = "\n\n".join(user_prompt_parts)

            bot_conf = self.plugin.bot_map.get(bot_name, {})
            nickname = bot_conf.get("nickname", bot_name)

            sys_prompt = self.plugin.passive_memory_summary_prompt.format(
                nickname=nickname, self_id=self_id
            )

            provider_ids = self.plugin.passive_memory_provider_ids
            if not provider_ids:
                logger.warning("[Giftia Passive Memory] 未配置被动总结提供商(passive_memory_provider_ids)，跳过后台总结。")
                return

            completion_text = None
            for provider_id in provider_ids:
                for attempt in range(2):
                    try:
                        logger.info(
                            f"[Giftia Passive Memory] 尝试使用提供商 {provider_id} (第 {attempt+1} 次) 进行后台总结"
                        )
                        logger.debug(
                            f"[Giftia Passive Memory] 开始总结记忆的 system_prompt:\n{sys_prompt}"
                        )
                        logger.debug(
                            f"[Giftia Passive Memory] 开始总结记忆的 user_prompt:\n{user_prompt}"
                        )
                        llm_resp = await self.plugin.context.llm_generate(
                            chat_provider_id=provider_id,
                            system_prompt=sys_prompt,
                            prompt=user_prompt,
                        )
                        if llm_resp and llm_resp.completion_text:
                            completion_text = llm_resp.completion_text
                            break
                    except Exception as e:
                        logger.error(f"[Giftia Passive Memory] 提供商 {provider_id} 调用报错: {e}")
                if completion_text:
                    break

            if not completion_text:
                logger.error("[Giftia Passive Memory] 所有配置的总结提供商均调用失败，本次总结任务终止。")
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:last_summarized_id:{bot_name}:{group_or_user_id}", start_id - 1
                )
                return

            logger.info(f"[Giftia Passive Memory] 大模型总结返回内容:\n{completion_text}")

            # 解析 XML 并写入数据库/缓存
            memory_matches = re.finditer(
                r'<memory(?:\s+users=["\']([^"\']*)["\'])?>(.*?)</memory>',
                completion_text,
                re.DOTALL,
            )
            for match in memory_matches:
                users_attr = match.group(1) or ""
                text = match.group(2).strip()

                if not text or text == "无":
                    continue

                associated_ids = []
                if users_attr:
                    for u in re.split(r'[,，]', users_attr):
                        u = u.strip()
                        resolved_uid = nickname_to_user_id.get(u, u)
                        if resolved_uid:
                            associated_ids.append(resolved_uid)

                primary_user = associated_ids[0] if associated_ids else self_id
                
                await self.plugin.data_cache.add_memory(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    text=text,
                    user_id=primary_user,
                    associated_user_ids=associated_ids,
                )
                logger.info(f"[Giftia Passive Memory] 已成功记录长期记忆: {text} (关联用户: {associated_ids})")

            user_profile_matches = re.finditer(
                r'<summary_user_profile\s+user_id=["\']([^"\']*)["\']>(.*?)</summary_user_profile>',
                completion_text,
                re.DOTALL,
            )
            for match in user_profile_matches:
                target_user = match.group(1).strip()
                profile_content = match.group(2).strip()
                resolved_user_id = nickname_to_user_id.get(target_user, target_user)
                if resolved_user_id and profile_content:
                    await self.plugin.data_cache.set_user_profile(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        user_id=resolved_user_id,
                        profile=profile_content,
                    )
                    logger.info(f"[Giftia Passive Memory] 已更新用户 {resolved_user_id} 画像")

            group_profile_matches = re.finditer(
                r'<summary_group_profile>(.*?)</summary_group_profile>',
                completion_text,
                re.DOTALL,
            )
            for match in group_profile_matches:
                group_profile_content = match.group(1).strip()
                if group_profile_content:
                    await self.plugin.data_cache.set_group_profile(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        profile=group_profile_content,
                    )
                    logger.info(f"[Giftia Passive Memory] 已更新群画像")

            relation_matches = re.finditer(
                r'<update_relation\s+([^>]*)>(.*?)</update_relation>',
                completion_text,
                re.DOTALL,
            )
            for match in relation_matches:
                attr_str = match.group(1)
                reason = match.group(2).strip()
                attrs = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', attr_str))
                target_user = attrs.get("user_id", "").strip()
                score_change_str = attrs.get("score_change", "0").strip()

                resolved_user_id = nickname_to_user_id.get(target_user, target_user)
                try:
                    score_change = int(score_change_str)
                except ValueError:
                    score_change = 0

                if resolved_user_id and score_change != 0:
                    await self.plugin.data_cache.update_relation(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        user_id=resolved_user_id,
                        relation=score_change,
                    )
                    logger.info(
                        f"[Giftia Passive Memory] 用户 {resolved_user_id} 好感度变动 {score_change}，原因: {reason}"
                    )

            title_matches = re.finditer(
                r'<set_relation_title\s+([^>]*)>(.*?)</set_relation_title>',
                completion_text,
                re.DOTALL,
            )
            for match in title_matches:
                attr_str = match.group(1)
                title = match.group(2).strip()
                attrs = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', attr_str))
                target_user = attrs.get("user_id", "").strip()

                resolved_user_id = nickname_to_user_id.get(target_user, target_user)
                if resolved_user_id and title:
                    await self.plugin.data_cache.set_relation_title(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        user_id=resolved_user_id,
                        title=title,
                    )
                    logger.info(f"[Giftia Passive Memory] 用户 {resolved_user_id} 关系头衔已设置为: {title}")

        except Exception as e:
            logger.error(f"[Giftia Passive Memory] 后台总结执行异常: {e}", exc_info=True)
            await self.plugin.db.upsert_kv_data(
                f"passive_memory:last_summarized_id:{bot_name}:{group_or_user_id}", start_id - 1
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
            bot_participated = any(str(msg.user_id) == str(self_id) for msg in db_messages)
            if not bot_participated:
                # 依然推进边界，跳过该区间
                await self.plugin.db.upsert_kv_data(f"passive_memory:last_summarized_id:{fmt_key}", end_id)
                await self.plugin.db.upsert_kv_data(f"passive_memory:silent_count:{fmt_key}", 0)
                await self.plugin.db.upsert_kv_data(f"passive_memory:silence_armed:{fmt_key}", 0)
                return f"该区间内机器人未参与发言（消息范围: id {start_id} 到 {end_id}），跳过提炼。"

            # 推进状态边界，避免重入
            await self.plugin.db.upsert_kv_data(f"passive_memory:silent_count:{fmt_key}", 0)
            await self.plugin.db.upsert_kv_data(f"passive_memory:silence_armed:{fmt_key}", 0)
            await self.plugin.db.upsert_kv_data(f"passive_memory:last_summarized_id:{fmt_key}", end_id)

            try:
                # 同步调用 _run_background_summarize 以同步获取反馈
                await self._run_background_summarize(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    self_id=self_id,
                    start_id=start_id,
                    end_id=end_id,
                )
                return f"成功提炼了 {len(db_messages)} 条消息的记忆（消息范围: id {start_id} 到 {end_id}）。"
            except Exception as e:
                # 失败时回滚边界
                await self.plugin.db.upsert_kv_data(f"passive_memory:last_summarized_id:{fmt_key}", last_summarized_id)
                logger.error(f"强制提炼记忆执行失败: {e}", exc_info=True)
                return f"提炼记忆失败: {e}"
