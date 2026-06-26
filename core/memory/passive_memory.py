import re
import json
import asyncio
from datetime import datetime
from astrbot.api import logger

class PassiveMemoryManager:
    def __init__(self, plugin):
        self.plugin = plugin

    async def _format_message_content_for_summary(self, msg) -> str:
        """清洗消息中的媒体占位符，并补充适合总结的媒体转述文本"""
        content = msg.content or ""
        if not msg.media_id_list:
            return content

        media_lines = []
        for media_id in msg.media_id_list:
            media_caption = await self.plugin.data_cache.get_caption_by_hash(media_id)
            if not media_caption:
                content = content.replace(f"[图片:{media_id}]", "[图片]")
                content = content.replace(f"[语音:{media_id}]", "[语音]")
                continue

            media_type = (media_caption.media_type or "").lower()
            caption_text = (media_caption.caption or "").strip()
            transcript_text = (media_caption.text or "").strip()

            if media_type == "audio":
                content = content.replace(
                    f"[语音:{media_id}]",
                    "" if (transcript_text or caption_text) else "[语音]",
                )
                if transcript_text:
                    media_lines.append(f"[语音转写:{transcript_text}]")
                if caption_text:
                    media_lines.append(f"[语音总结:{caption_text}]")
                elif not transcript_text:
                    media_lines.append("[语音]")
                continue

            if media_type == "image":
                content = content.replace(
                    f"[图片:{media_id}]",
                    "" if caption_text else "[图片]",
                )
                if caption_text:
                    media_lines.append(f"[图片转述:{caption_text}]")
                else:
                    media_lines.append("[图片]")
                continue

            content = content.replace(f"[图片:{media_id}]", "")
            content = content.replace(f"[语音:{media_id}]", "")
            if caption_text:
                media_lines.append(f"[媒体转述:{caption_text}]")
            elif transcript_text:
                media_lines.append(f"[媒体内容:{transcript_text}]")

        if media_lines:
            content = f"{content} {' '.join(media_lines)}".strip()

        return re.sub(r"\s{2,}", " ", content).strip()

    async def mark_silence_summary_armed(
        self, bot_name: str, group_or_user_id: str
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
                return

            if max_id <= last_summarized_id:
                return

            active_counter = self.plugin.active_reply_counters.get(fmt_key, 0)
            
            trigger_type = None
            start_id = last_summarized_id + 1
            end_id = max_id
            silence_armed = await self.plugin.db.get_kv_data(
                f"passive_memory:silence_armed:{fmt_key}", 0
            )
            
            boundary_id = await self.plugin.db.get_boundary_message_id(
                bot_name, group_or_user_id, self.plugin.msg_number
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
                # 收窄总结范围到上下文窗口内，bot 只记忆自己"见过"的消息
                if start_id < boundary_id:
                    logger.info(
                        f"[Giftia Passive Memory] start_id({start_id}) 超出上下文窗口边界({boundary_id})，"
                        f"收窄到上下文范围，跳过 bot 未见过的消息"
                    )
                    start_id = boundary_id

                if start_id > end_id:
                    # 上下文窗口内无需总结的消息
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
                    return

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

            # 建立昵称与 ID 映射，处理 LLM 可能会直接使用昵称的情况
            nickname_to_user_id = {}
            for msg in db_messages:
                if msg.user_id:
                    nickname_to_user_id[msg.user_id] = msg.user_id
                    if msg.nickname:
                        nickname_to_user_id[msg.nickname] = msg.user_id

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
                    user_profiles_str.append(f"用户 {uid} ({nickname_to_user_id.get(uid, '')}) 现有画像:\n{profile}")
                
                relation_score, relation_title = await self.plugin.data_cache.get_user_relation(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    user_id=uid,
                )
                user_relations_str.append(
                    f"用户 {uid} ({nickname_to_user_id.get(uid, '')}) 的好感度得分: {relation_score}, 头衔: {relation_title or '无'}"
                )

            group_profile = await self.plugin.data_cache.get_group_profile(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
            )

            # 格式化聊天记录
            chat_history_lines = []
            for msg in db_messages:
                content = await self._format_message_content_for_summary(msg)
                chat_history_lines.append(
                    f"[{msg.time}] {msg.nickname}({msg.user_id}): {content}"
                )
            chat_history_text = "\n".join(chat_history_lines)

            # 构建 User Prompt
            user_prompt = f"""以下是一段历史聊天记录，你需要根据这段记录，提取/更新长期记忆、用户画像、群聊画像和好感度/关系头衔。

【当前群聊ID/会话ID】: {group_or_user_id}

【现有状态信息】:
1. 用户现有画像:
{"\n---\n".join(user_profiles_str) if user_profiles_str else "无"}

2. 用户现有好感度/关系:
{"\n".join(user_relations_str) if user_relations_str else "无"}

3. 当前群聊的现有画像:
{group_profile or "无"}

【待分析的聊天记录】:
{chat_history_text}
"""

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
