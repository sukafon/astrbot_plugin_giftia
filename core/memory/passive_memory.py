import asyncio
import json
import re
from datetime import datetime

from astrbot.api import logger

from ..llm.prompt import (
    USER_PROFILE_FIELDS,
    normalize_profile_text,
    normalize_profile_value,
    parse_caption_to_str,
)


DEFAULT_PASSIVE_MEMORY_SUMMARY_PROMPT = """# 角色与目标
你是一个长期记忆提炼器。你需要分析以下群聊片段，只总结与机器人自身（昵称：{nickname}，ID：{self_id}）直接相关、未来值得召回的事件记忆。

# 提炼规则
- 只记录机器人参与、被提及、有互动的有价值事件，例如约定、承诺、共同经历、明确偏好或重要结论。
- 每条记忆必须使用第一人称，从机器人的角度描述。
- 每条记忆控制在 50 字以内，避免流水账和情绪泛化。
- 必须使用 `users` 属性指出该记忆直接关联的群友 user_id，多用户用逗号分隔。
- 与特定人无关但对机器人有意义时，可以省略 `users` 属性。

# 输出格式
请只输出 `<memory>` 标签：
`<memory users="12345">小明约我周末一起打游戏，我答应提醒他。</memory>`

如果没有值得记录的长期记忆，请只输出：
`<memory>无</memory>`"""


DEFAULT_PASSIVE_PROFILE_SUMMARY_PROMPT = """# 角色与目标
你是一个用户画像和群画像维护器。你需要分析以下群聊片段，结合已有画像，维护结构化用户画像、群画像、好感度和关系头衔。

# 提供的现有状态
- <current_user_profiles>：当前活跃成员的结构化画像、旧画像参考、好感度和关系头衔。
- <current_group_profile>：当前群聊的现有画像。

# 用户画像更新
如果发现某位用户的新特征、新喜好、称呼关系或互动状态，请结合现有画像，输出该用户需要更新的字段。

用户画像字段说明：
- call_name：你对该成员的称呼。
- aliases：其他群友对该成员的称呼或外号。
- personality：性格特征与说话风格。
- interests：兴趣爱好与关注事物。
- attitude：该成员对你的态度。
- agreements：与你达成的承诺或共同回忆。
- extra：无法归入以上字段、但长期有助于理解用户的信息；不要重复已有字段；最多 3 条，每条 30 字以内。

只输出需要更新的字段，不要为无变化字段输出标签。每个字段精炼在一句话、30 字以内。好感度使用最新绝对分数，不要输出增量。关系头衔如果没有变化可以省略 `title` 属性。

格式：
`<summary_user_profile user_id="12345" relation="12" title="挚友">
<call_name>小草莓</call_name>
<aliases>草莓酱</aliases>
<personality>傲娇但友好</personality>
<interests>喜欢动漫和游戏</interests>
<attitude>经常调侃我</attitude>
<agreements>周末一起打游戏</agreements>
<extra>习惯深夜活跃</extra>
</summary_user_profile>`

# 群画像更新
如果发现群聊的新特征，请结合现有群画像，输出最新完整群画像：
- 群聊主题：<群聊定位与核心主题>
- 氛围特征：<群内氛围与活跃特征>
- 成员关系：<核心成员互动关系，50 字以内>
- 核心规则与忌讳：<群规、敏感点或忌讳>

格式：
`<summary_group_profile>
- 群聊主题：游戏讨论与日常吹水
- 氛围特征：气氛轻松，经常开玩笑
- 成员关系：流萤与爱丽丝关系亲密
- 核心规则与忌讳：禁止刷屏和恶意复读
</summary_group_profile>`

# 输出要求
只输出需要更新的 XML 标签。如果没有任何画像或关系需要更新，请只输出：
`<profile>无</profile>`"""


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

    def _looks_like_legacy_combined_prompt(self, prompt: str) -> bool:
        legacy_markers = (
            "summary_user_profile",
            "summary_group_profile",
            "update_relation",
            "set_relation_title",
            "current_relations",
        )
        return any(marker in prompt for marker in legacy_markers)

    def _format_prompt_template(
        self, prompt_template: str, nickname: str, self_id: str
    ) -> str:
        try:
            return prompt_template.format(nickname=nickname, self_id=self_id)
        except Exception as e:
            logger.warning(
                f"[Giftia Passive Memory] 提示词变量格式化失败，将使用原文: {e}"
            )
            return prompt_template

    def _get_memory_summary_prompt(self) -> str:
        prompt = getattr(self.plugin, "passive_memory_summary_prompt", "") or ""
        if not prompt or self._looks_like_legacy_combined_prompt(prompt):
            return DEFAULT_PASSIVE_MEMORY_SUMMARY_PROMPT
        return prompt

    def _get_profile_summary_prompt(self) -> str:
        prompt = getattr(self.plugin, "passive_profile_summary_prompt", "") or ""
        return prompt or DEFAULT_PASSIVE_PROFILE_SUMMARY_PROMPT

    def _format_user_profile_record_for_summary(self, record: dict | None) -> str:
        if not record:
            return "无"

        parts = []
        structured_lines = []
        for field, label in USER_PROFILE_FIELDS:
            value = normalize_profile_value(record.get(field))
            if value:
                structured_lines.append(f"- {label}：{value}")
        if structured_lines:
            parts.append("结构化画像:\n" + "\n".join(structured_lines))

        legacy_profile = normalize_profile_text(record.get("profile"))
        if legacy_profile:
            parts.append("历史画像参考:\n" + legacy_profile)

        relation_parts = []
        relation = record.get("relation")
        title = record.get("title")
        if relation not in (None, "", 0):
            relation_parts.append(f"好感度: {relation}")
        if title:
            relation_parts.append(f"头衔: {title}")
        if relation_parts:
            parts.append("关系状态: " + "，".join(relation_parts))

        return "\n".join(parts) if parts else "无"

    def _parse_user_profile_fields(self, profile_content: str) -> dict[str, str]:
        parsed = {}
        for field, _ in USER_PROFILE_FIELDS:
            match = re.search(
                rf"<{field}>(.*?)</{field}>",
                profile_content,
                re.DOTALL,
            )
            if not match:
                continue
            value = normalize_profile_value(match.group(1))
            if value:
                parsed[field] = value
        return parsed

    async def _build_summary_context(
        self,
        bot_name: str,
        group_or_user_id: str,
        self_id: str,
        db_messages: list,
    ) -> dict:
        active_users_in_range = {
            msg.user_id
            for msg in db_messages
            if msg.user_id and str(msg.user_id) != str(self_id)
        }

        nickname_to_user_id = {}
        user_id_to_nickname = {}
        for msg in db_messages:
            if msg.user_id and msg.nickname:
                nickname_to_user_id[msg.nickname] = msg.user_id
                user_id_to_nickname[msg.user_id] = msg.nickname

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

        chat_history_lines = []
        for msg in processed_messages:
            chat_history_lines.append(
                f"[{format_time_to_seconds(msg.time)}] {msg.nickname}({msg.user_id}): {msg.content or ''}"
            )
        chat_history_text = "\n".join(chat_history_lines)

        active_user_lines = []
        user_profile_blocks = []
        for uid in sorted(active_users_in_range):
            nickname = user_id_to_nickname.get(uid, "")
            profile_record = await self.plugin.data_cache.get_user_profile_record(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                user_id=uid,
            )

            # 获取 Bot 对用户的自定义称呼并注入到 active_users_text 中
            call_name = profile_record.get("call_name") if profile_record else None
            parts = [f"群内昵称: {nickname}" if nickname else None]
            if call_name:
                parts.append(f"你对他的称呼: {call_name}")
            info_str = "，".join(p for p in parts if p)
            active_user_lines.append(f"- {uid} ({info_str})" if info_str else f"- {uid}")

            block_lines = [f"用户 {uid} ({nickname})" if nickname else f"用户 {uid}"]
            block_lines.append(
                "现有画像:\n" + self._format_user_profile_record_for_summary(
                    profile_record
                )
            )
            user_profile_blocks.append("\n".join(block_lines))

        group_profile = await self.plugin.data_cache.get_group_profile(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
        )

        media_captions_block = ""
        if remaining_captions:
            media_captions_block = "\n".join(
                parse_caption_to_str(c) for c in remaining_captions
            )

        return {
            "nickname_to_user_id": nickname_to_user_id,
            "user_id_to_nickname": user_id_to_nickname,
            "active_users_text": "\n".join(active_user_lines) or "无",
            "user_profiles_text": "\n---\n".join(user_profile_blocks) or "无",
            "group_profile": group_profile or "无",
            "media_captions_block": media_captions_block,
            "chat_history_text": chat_history_text,
        }

    def _build_memory_user_prompt(self, group_or_user_id: str, context: dict) -> str:
        user_prompt_parts = [
            f"<session_id>{group_or_user_id}</session_id>",
            f"<active_users>\n{context['active_users_text']}\n</active_users>",
        ]
        if context["media_captions_block"]:
            user_prompt_parts.append(
                f"<media_content>\n{context['media_captions_block']}\n</media_content>"
            )
        user_prompt_parts.append(
            f"<chat_history>\n{context['chat_history_text']}\n</chat_history>"
        )
        return "\n\n".join(user_prompt_parts)

    def _build_profile_user_prompt(self, group_or_user_id: str, context: dict) -> str:
        user_prompt_parts = [
            f"<session_id>{group_or_user_id}</session_id>",
            f"<current_user_profiles>\n{context['user_profiles_text']}\n</current_user_profiles>",
            f"<current_group_profile>\n{context['group_profile']}\n</current_group_profile>",
        ]
        if context["media_captions_block"]:
            user_prompt_parts.append(
                f"<media_content>\n{context['media_captions_block']}\n</media_content>"
            )
        user_prompt_parts.append(
            f"<chat_history>\n{context['chat_history_text']}\n</chat_history>"
        )
        return "\n\n".join(user_prompt_parts)

    async def _call_summary_llm(
        self,
        task_name: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str | None:
        provider_ids = self.plugin.passive_memory_provider_ids
        if not provider_ids:
            logger.warning(
                "[Giftia Passive Memory] 未配置被动总结提供商(passive_memory_provider_ids)，跳过后台总结。"
            )
            return None

        for provider_id in provider_ids:
            for attempt in range(2):
                try:
                    logger.info(
                        f"[Giftia Passive Memory] 尝试使用提供商 {provider_id} (第 {attempt + 1} 次) 进行{task_name}"
                    )
                    logger.debug(
                        f"[Giftia Passive Memory] {task_name} system_prompt:\n{system_prompt}"
                    )
                    logger.debug(
                        f"[Giftia Passive Memory] {task_name} user_prompt:\n{user_prompt}"
                    )
                    llm_resp = await self.plugin.context.llm_generate(
                        chat_provider_id=provider_id,
                        system_prompt=system_prompt,
                        prompt=user_prompt,
                    )
                    if llm_resp and llm_resp.completion_text:
                        return llm_resp.completion_text
                except Exception as e:
                    logger.error(
                        f"[Giftia Passive Memory] 提供商 {provider_id} 调用{task_name}报错: {e}"
                    )

        logger.error(
            f"[Giftia Passive Memory] 所有配置的总结提供商均调用失败，{task_name}终止。"
        )
        return None

    async def _run_memory_summary_task(
        self,
        bot_name: str,
        group_or_user_id: str,
        self_id: str,
        nickname: str,
        context: dict,
    ) -> bool | None:
        if not self.plugin.embedding_conf.get("enabled", False):
            logger.debug(
                "[Giftia Passive Memory] 嵌入模型未启用，跳过长期记忆提炼，仅维护画像。"
            )
            return None

        sys_prompt = self._format_prompt_template(
            self._get_memory_summary_prompt(), nickname=nickname, self_id=self_id
        )
        user_prompt = self._build_memory_user_prompt(group_or_user_id, context)
        completion_text = await self._call_summary_llm(
            "长期记忆提炼", sys_prompt, user_prompt
        )
        if not completion_text:
            return False

        logger.info(
            f"[Giftia Passive Memory] 长期记忆提炼返回内容:\n{completion_text}"
        )

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
                for u in re.split(r"[,，]", users_attr):
                    u = u.strip()
                    resolved_uid = context["nickname_to_user_id"].get(u, u)
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
            logger.info(
                f"[Giftia Passive Memory] 已成功记录长期记忆: {text} (关联用户: {associated_ids})"
            )
        return True

    async def _run_profile_summary_task(
        self,
        bot_name: str,
        group_or_user_id: str,
        nickname: str,
        self_id: str,
        context: dict,
    ) -> bool:
        sys_prompt = self._format_prompt_template(
            self._get_profile_summary_prompt(), nickname=nickname, self_id=self_id
        )
        user_prompt = self._build_profile_user_prompt(group_or_user_id, context)
        completion_text = await self._call_summary_llm(
            "画像维护", sys_prompt, user_prompt
        )
        if not completion_text:
            return False

        logger.info(f"[Giftia Passive Memory] 画像维护返回内容:\n{completion_text}")

        user_profile_matches = re.finditer(
            r"<summary_user_profile\s+([^>]*)>(.*?)</summary_user_profile>",
            completion_text,
            re.DOTALL,
        )
        for match in user_profile_matches:
            attr_str = match.group(1)
            attrs = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', attr_str))
            target_user = attrs.get("user_id", "").strip()
            profile_content = match.group(2).strip()
            resolved_user_id = context["nickname_to_user_id"].get(
                target_user, target_user
            )

            relation = None
            for relation_key in ("relation", "score", "favorability", "affinity"):
                if relation_key in attrs:
                    try:
                        relation = int(attrs[relation_key].strip())
                    except ValueError:
                        relation = None
                    break

            title = attrs.get("title")
            if title is not None:
                title = title.strip()

            has_profile_content = bool(profile_content and profile_content != "无")
            if not resolved_user_id or (
                not has_profile_content and relation is None and title is None
            ):
                continue

            profile_fields = {}
            legacy_profile = None
            if has_profile_content:
                profile_fields = self._parse_user_profile_fields(profile_content)
                if not profile_fields:
                    legacy_profile = profile_content

            await self.plugin.data_cache.set_user_profile(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                user_id=resolved_user_id,
                profile=legacy_profile,
                relation=relation,
                title=title,
                profile_fields=profile_fields,
                clamp_relation=True,
            )
            logger.info(
                f"[Giftia Passive Memory] 已更新用户 {resolved_user_id} 画像"
            )

        group_profile_matches = re.finditer(
            r"<summary_group_profile>(.*?)</summary_group_profile>",
            completion_text,
            re.DOTALL,
        )
        for match in group_profile_matches:
            group_profile_content = match.group(1).strip()
            if group_profile_content and group_profile_content != "无":
                await self.plugin.data_cache.set_group_profile(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    profile=group_profile_content,
                )
                logger.info("[Giftia Passive Memory] 已更新群画像")

        relation_matches = re.finditer(
            r"<update_relation\s+([^>]*)>(.*?)</update_relation>",
            completion_text,
            re.DOTALL,
        )
        for match in relation_matches:
            attr_str = match.group(1)
            reason = match.group(2).strip()
            attrs = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', attr_str))
            target_user = attrs.get("user_id", "").strip()
            score_change_str = (
                attrs.get("score_change") or attrs.get("delta") or "0"
            ).strip()

            resolved_user_id = context["nickname_to_user_id"].get(
                target_user, target_user
            )
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
            r"<set_relation_title\s+([^>]*)>(.*?)</set_relation_title>",
            completion_text,
            re.DOTALL,
        )
        for match in title_matches:
            attr_str = match.group(1)
            title = match.group(2).strip()
            attrs = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', attr_str))
            target_user = attrs.get("user_id", "").strip()

            resolved_user_id = context["nickname_to_user_id"].get(
                target_user, target_user
            )
            if resolved_user_id and title:
                await self.plugin.data_cache.set_relation_title(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    user_id=resolved_user_id,
                    title=title,
                )
                logger.info(
                    f"[Giftia Passive Memory] 用户 {resolved_user_id} 关系头衔已设置为: {title}"
                )
        return True

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

            bot_participated = any(
                str(msg.user_id) == str(self_id) for msg in db_messages
            )
            if not bot_participated:
                logger.debug(
                    f"[Giftia Passive Memory] {bot_name}:{group_or_user_id} 消息范围 {start_id}-{end_id} 内机器人没有直接参与，跳过 LLM 总结。"
                )
                return

            bot_conf = self.plugin.bot_map.get(bot_name, {})
            nickname = bot_conf.get("nickname", bot_name)
            summary_context = await self._build_summary_context(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                self_id=self_id,
                db_messages=db_messages,
            )

            memory_ok = await self._run_memory_summary_task(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                self_id=self_id,
                nickname=nickname,
                context=summary_context,
            )
            profile_ok = await self._run_profile_summary_task(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                nickname=nickname,
                self_id=self_id,
                context=summary_context,
            )

            if profile_ok is False and memory_ok is not True:
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:last_summarized_id:{bot_name}:{group_or_user_id}",
                    start_id - 1,
                )

        except Exception as e:
            logger.error(
                f"[Giftia Passive Memory] 后台总结执行异常: {e}", exc_info=True
            )
            await self.plugin.db.upsert_kv_data(
                f"passive_memory:last_summarized_id:{bot_name}:{group_or_user_id}",
                start_id - 1,
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
                # 依然推进边界，跳过该区间
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:last_summarized_id:{fmt_key}", end_id
                )
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:silent_count:{fmt_key}", 0
                )
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:silence_armed:{fmt_key}", 0
                )
                return f"该区间内机器人未参与发言（消息范围: id {start_id} 到 {end_id}），跳过提炼。"

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
                return f"成功处理了 {len(db_messages)} 条消息的被动总结（消息范围: id {start_id} 到 {end_id}）。"
            except Exception as e:
                # 失败时回滚边界
                await self.plugin.db.upsert_kv_data(
                    f"passive_memory:last_summarized_id:{fmt_key}", last_summarized_id
                )
                logger.error(f"强制提炼记忆执行失败: {e}", exc_info=True)
                return f"提炼记忆失败: {e}"
