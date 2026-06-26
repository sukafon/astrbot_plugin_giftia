import json
import time
from datetime import datetime

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request


class DataApi:
    """Data management APIs: chat history, memories, bot status, profiles."""

    def __init__(self, giftia):
        self.giftia = giftia

    async def get_chat_history(self):
        """Get chat logs with pagination and filters."""
        try:
            page = int(request.query.get("page", 1))
            limit = int(request.query.get("limit", 20))
            bot_name = request.query.get("bot_name")
            group_or_user_id = request.query.get("group_or_user_id")
            user_id = request.query.get("user_id")
            reply_decision = request.query.get("reply_decision")
            use_rag = request.query.get("use_rag")
            search = request.query.get("search")

            offset = (page - 1) * limit
            conditions = []
            params = []

            if bot_name:
                conditions.append("bot_name = ?")
                params.append(bot_name)
            if group_or_user_id:
                conditions.append("group_or_user_id = ?")
                params.append(group_or_user_id)
            if user_id:
                conditions.append("user_id = ?")
                params.append(user_id)
            if reply_decision is not None and reply_decision != "":
                conditions.append("reply_decision = ?")
                params.append(int(reply_decision))
            if use_rag is not None and use_rag != "":
                conditions.append("use_rag = ?")
                params.append(int(use_rag))
            if search:
                conditions.append("content LIKE ?")
                params.append(f"%{search}%")

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            # Query count
            count_sql = f"SELECT COUNT(*) as total FROM chat_history {where_clause}"
            async with self.giftia.db.conn.execute(count_sql, params) as cursor:
                row = await cursor.fetchone()
                total = row["total"] if row else 0

            # Query data
            data_sql = f"""
                SELECT id, bot_name, group_or_user_id, nickname, user_id, message_id,
                       content, media_ids, role, reply_decision, use_rag, is_recalled, created_at
                FROM chat_history
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            data_params = params + [limit, offset]
            items = []
            async with self.giftia.db.conn.execute(data_sql, data_params) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    items.append(
                        {
                            "id": r["id"],
                            "bot_name": r["bot_name"],
                            "group_or_user_id": r["group_or_user_id"],
                            "nickname": r["nickname"],
                            "user_id": r["user_id"],
                            "message_id": r["message_id"],
                            "content": r["content"],
                            "media_ids": json.loads(r["media_ids"])
                            if r["media_ids"]
                            else [],
                            "role": r["role"],
                            "reply_decision": r["reply_decision"],
                            "use_rag": r["use_rag"],
                            "is_recalled": r["is_recalled"],
                            "created_at": r["created_at"],
                        }
                    )

            return json_response(
                {
                    "status": "success",
                    "data": {
                        "items": items,
                        "total": total,
                        "page": page,
                        "limit": limit,
                    },
                }
            )
        except Exception as e:
            logger.error(f"[Giftia API] get_chat_history error: {e}")
            return error_response(f"获取聊天记录失败: {str(e)}")

    async def get_chat_history_filter_options(self):
        """Get bot/session filter options for chat history."""
        try:
            bot_name = request.query.get("bot_name")
            user_id = request.query.get("user_id")
            reply_decision = request.query.get("reply_decision")
            use_rag = request.query.get("use_rag")
            search = request.query.get("search")

            bots = []
            async with self.giftia.db.conn.execute(
                """
                SELECT DISTINCT bot_name
                FROM chat_history
                WHERE bot_name IS NOT NULL AND bot_name != ''
                ORDER BY bot_name ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
                bots = [row["bot_name"] for row in rows if row["bot_name"]]

            selected_bot_name = bot_name if bot_name in bots else (bots[0] if bots else "")

            sessions = []
            if selected_bot_name:
                conditions = ["bot_name = ?"]
                params = [selected_bot_name]
                if user_id:
                    conditions.append("user_id = ?")
                    params.append(user_id)
                if reply_decision is not None and reply_decision != "":
                    conditions.append("reply_decision = ?")
                    params.append(int(reply_decision))
                if use_rag is not None and use_rag != "":
                    conditions.append("use_rag = ?")
                    params.append(int(use_rag))
                if search:
                    conditions.append("content LIKE ?")
                    params.append(f"%{search}%")

                where_clause = "WHERE " + " AND ".join(conditions)
                async with self.giftia.db.conn.execute(
                    f"""
                    SELECT group_or_user_id, COUNT(*) as total, MAX(created_at) as latest_at
                    FROM chat_history
                    {where_clause}
                    GROUP BY group_or_user_id
                    ORDER BY latest_at DESC, group_or_user_id ASC
                    """,
                    params,
                ) as cursor:
                    rows = await cursor.fetchall()
                    sessions = [
                        {
                            "group_or_user_id": row["group_or_user_id"],
                            "total": row["total"],
                        }
                        for row in rows
                        if row["group_or_user_id"]
                    ]

            return json_response(
                {
                    "status": "success",
                    "data": {
                        "bots": bots,
                        "selected_bot_name": selected_bot_name,
                        "sessions": sessions,
                    },
                }
            )
        except Exception as e:
            logger.error(f"[Giftia API] get_chat_history_filter_options error: {e}")
            return error_response(f"获取决策审计筛选项失败: {str(e)}")

    # ── Memory APIs ─────────────────────────────────────────────────────

    async def get_memories(self):
        """Get memories with pagination and filters."""
        try:
            page = int(request.query.get("page", 1))
            limit = int(request.query.get("limit", 20))
            bot_name = request.query.get("bot_name")
            group_or_user_id = request.query.get("group_or_user_id")
            associated_user_id = request.query.get("associated_user_id")
            search = request.query.get("search")

            offset = (page - 1) * limit
            conditions = []
            params = []

            if bot_name:
                conditions.append("bot_name = ?")
                params.append(bot_name)
            if group_or_user_id:
                conditions.append("group_or_user_id = ?")
                params.append(group_or_user_id)
            if associated_user_id:
                conditions.append("metadata LIKE ?")
                params.append(f"%{associated_user_id}%")
            if search:
                conditions.append("text LIKE ?")
                params.append(f"%{search}%")

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            # Query count
            count_sql = f"SELECT COUNT(*) as total FROM memories {where_clause}"
            async with self.giftia.db.conn.execute(count_sql, params) as cursor:
                row = await cursor.fetchone()
                total = row["total"] if row else 0

            # Query data
            data_sql = f"""
                SELECT id, bot_name, group_or_user_id, memory_id, text, metadata, created_at
                FROM memories
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            data_params = params + [limit, offset]
            items = []
            async with self.giftia.db.conn.execute(data_sql, data_params) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    items.append(
                        {
                            "id": r["id"],
                            "bot_name": r["bot_name"],
                            "group_or_user_id": r["group_or_user_id"],
                            "memory_id": r["memory_id"],
                            "text": r["text"],
                            "metadata": json.loads(r["metadata"])
                            if r["metadata"]
                            else {},
                            "created_at": r["created_at"],
                        }
                    )

            return json_response(
                {
                    "status": "success",
                    "data": {
                        "items": items,
                        "total": total,
                        "page": page,
                        "limit": limit,
                    },
                }
            )
        except Exception as e:
            logger.error(f"[Giftia API] get_memories error: {e}")
            return error_response(f"获取记忆列表失败: {str(e)}")

    async def get_memory_filter_options(self):
        """Get bot/session filter options for memories."""
        try:
            bot_name = request.query.get("bot_name")
            associated_user_id = request.query.get("associated_user_id")
            search = request.query.get("search")

            bots = []
            async with self.giftia.db.conn.execute(
                """
                SELECT DISTINCT bot_name
                FROM memories
                WHERE bot_name IS NOT NULL AND bot_name != ''
                ORDER BY bot_name ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
                bots = [row["bot_name"] for row in rows if row["bot_name"]]

            selected_bot_name = bot_name if bot_name in bots else (bots[0] if bots else "")

            sessions = []
            if selected_bot_name:
                conditions = ["bot_name = ?"]
                params = [selected_bot_name]
                if associated_user_id:
                    conditions.append("metadata LIKE ?")
                    params.append(f"%{associated_user_id}%")
                if search:
                    conditions.append("text LIKE ?")
                    params.append(f"%{search}%")

                where_clause = "WHERE " + " AND ".join(conditions)
                async with self.giftia.db.conn.execute(
                    f"""
                    SELECT group_or_user_id, COUNT(*) as total, MAX(created_at) as latest_at
                    FROM memories
                    {where_clause}
                    GROUP BY group_or_user_id
                    ORDER BY latest_at DESC, group_or_user_id ASC
                    """,
                    params,
                ) as cursor:
                    rows = await cursor.fetchall()
                    sessions = [
                        {
                            "group_or_user_id": row["group_or_user_id"],
                            "total": row["total"],
                        }
                        for row in rows
                        if row["group_or_user_id"]
                    ]

            return json_response(
                {
                    "status": "success",
                    "data": {
                        "bots": bots,
                        "selected_bot_name": selected_bot_name,
                        "sessions": sessions,
                    },
                }
            )
        except Exception as e:
            logger.error(f"[Giftia API] get_memory_filter_options error: {e}")
            return error_response(f"获取长期记忆筛选项失败: {str(e)}")

    async def add_memory(self):
        """Add new memory item."""
        try:
            body = await request.json()
            bot_name = body.get("bot_name")
            group_or_user_id = body.get("group_or_user_id")
            text = body.get("text")
            user_id = body.get("user_id") or "admin"
            associated_user_ids = body.get("associated_user_ids")

            if isinstance(associated_user_ids, str):
                associated_user_ids = [uid.strip() for uid in associated_user_ids.split(",") if uid.strip()]

            if not bot_name or not group_or_user_id or not text:
                return error_response("缺少必要参数 (bot_name, group_or_user_id, text)")

            memory_id = await self.giftia.data_cache.add_memory(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                text=text,
                user_id=user_id,
                associated_user_ids=associated_user_ids,
            )

            if not memory_id:
                return error_response("添加记忆失败，大模型 Embedding 出错")

            return json_response(
                {
                    "status": "success",
                    "message": "添加记忆成功",
                    "data": {"memory_id": memory_id},
                }
            )
        except Exception as e:
            logger.error(f"[Giftia API] add_memory error: {e}")
            return error_response(f"添加记忆失败: {str(e)}")

    async def update_memory(self):
        """Update existing memory text by replacing it."""
        try:
            body = await request.json()
            memory_id = body.get("memory_id")
            bot_name = body.get("bot_name")
            group_or_user_id = body.get("group_or_user_id")
            text = body.get("text")
            user_id = body.get("user_id") or "admin"
            associated_user_ids = body.get("associated_user_ids")

            if isinstance(associated_user_ids, str):
                associated_user_ids = [uid.strip() for uid in associated_user_ids.split(",") if uid.strip()]

            if not memory_id or not bot_name or not group_or_user_id or not text:
                return error_response("缺少必要参数")

            # Delete old memory first
            await self.giftia.data_cache.delete_memory(memory_id)

            # Re-insert with updated text
            new_memory_id = await self.giftia.data_cache.add_memory(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                text=text,
                user_id=user_id,
                associated_user_ids=associated_user_ids,
            )

            if not new_memory_id:
                return error_response("更新记忆失败，大模型 Embedding 出错")

            return json_response(
                {
                    "status": "success",
                    "message": "更新记忆成功",
                    "data": {"memory_id": new_memory_id},
                }
            )
        except Exception as e:
            logger.error(f"[Giftia API] update_memory error: {e}")
            return error_response(f"更新记忆失败: {str(e)}")

    async def delete_memory(self):
        """Delete memory item."""
        try:
            body = await request.json()
            memory_id = body.get("memory_id")

            if not memory_id:
                return error_response("缺少 memory_id 参数")

            success = await self.giftia.data_cache.delete_memory(memory_id)
            if not success:
                return error_response("删除记忆失败")

            return json_response({"status": "success", "message": "删除记忆成功"})
        except Exception as e:
            logger.error(f"[Giftia API] delete_memory error: {e}")
            return error_response(f"删除记忆失败: {str(e)}")

    # ── Bot Status APIs ─────────────────────────────────────────────────

    async def get_bot_status(self):
        """Get active bot status list."""
        try:
            sql = """
                SELECT id, bot_name, group_or_user_id, mood, state, memory, action, energy, created_at, updated_at
                FROM bot_status
                ORDER BY updated_at DESC
            """
            items = []
            async with self.giftia.db.conn.execute(sql) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    items.append(
                        {
                            "id": r["id"],
                            "bot_name": r["bot_name"],
                            "group_or_user_id": r["group_or_user_id"],
                            "mood": r["mood"],
                            "state": r["state"],
                            "memory": r["memory"],
                            "action": r["action"],
                            "energy": r["energy"],
                            "created_at": r["created_at"],
                            "updated_at": r["updated_at"],
                        }
                    )

            return json_response({"status": "success", "data": items})
        except Exception as e:
            logger.error(f"[Giftia API] get_bot_status error: {e}")
            return error_response(f"获取状态列表失败: {str(e)}")

    async def fill_energy(self):
        """Replenish bot energy to max 100."""
        try:
            body = await request.json()
            bot_name = body.get("bot_name")
            group_or_user_id = body.get("group_or_user_id")

            if not bot_name or not group_or_user_id:
                return error_response("缺少必要参数")

            fmt_key = f"{bot_name}:{group_or_user_id}"
            status = self.giftia.data_cache.bot_status.get(fmt_key)
            if not status:
                status = await self.giftia.db.get_bot_status(group_or_user_id, bot_name)

            status.energy = "100.0"
            status.timestamp = time.time()
            self.giftia.data_cache.bot_status[fmt_key] = status

            # Persist to database
            await self.giftia.db.upsert_bot_status(group_or_user_id, bot_name, status)

            return json_response(
                {"status": "success", "message": f"成功为 {bot_name} 补充能量"}
            )
        except Exception as e:
            logger.error(f"[Giftia API] fill_energy error: {e}")
            return error_response(f"补充能量失败: {str(e)}")

    async def update_bot_status(self):
        """Update bot mood, state, memory, or action."""
        try:
            body = await request.json()
            bot_name = body.get("bot_name")
            group_or_user_id = body.get("group_or_user_id")
            mood = body.get("mood")
            state = body.get("state")
            memory = body.get("memory")
            action = body.get("action")

            if not bot_name or not group_or_user_id:
                return error_response("缺少必要参数")

            fmt_key = f"{bot_name}:{group_or_user_id}"
            status = self.giftia.data_cache.bot_status.get(fmt_key)
            if not status:
                status = await self.giftia.db.get_bot_status(group_or_user_id, bot_name)

            if mood is not None:
                status.mood = mood
            if state is not None:
                status.state = state
            if memory is not None:
                status.memory = memory
            if action is not None:
                status.action = action

            self.giftia.data_cache.bot_status[fmt_key] = status
            await self.giftia.db.upsert_bot_status(group_or_user_id, bot_name, status)

            return json_response({"status": "success", "message": "更新 Bot 状态成功"})
        except Exception as e:
            logger.error(f"[Giftia API] update_bot_status error: {e}")
            return error_response(f"更新 Bot 状态失败: {str(e)}")

    # ── User Profile APIs ───────────────────────────────────────────────

    async def get_user_profiles(self):
        """Get user profiles with pagination and filters."""
        try:
            page = int(request.query.get("page", 1))
            limit = int(request.query.get("limit", 20))
            bot_name = request.query.get("bot_name")
            group_or_user_id = request.query.get("group_or_user_id")
            user_id = request.query.get("user_id")
            search = request.query.get("search")

            offset = (page - 1) * limit
            conditions = []
            params = []

            if bot_name:
                conditions.append("up.bot_name = ?")
                params.append(bot_name)
            if group_or_user_id:
                conditions.append("up.group_or_user_id = ?")
                params.append(group_or_user_id)
            if user_id:
                conditions.append("up.user_id = ?")
                params.append(user_id)
            if search:
                conditions.append("up.profile LIKE ?")
                params.append(f"%{search}%")

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            # Query count
            count_sql = f"SELECT COUNT(*) as total FROM user_profiles up {where_clause}"
            async with self.giftia.db.conn.execute(count_sql, params) as cursor:
                row = await cursor.fetchone()
                total = row["total"] if row else 0

            # Query data
            data_sql = f"""
                SELECT up.id, up.bot_name, up.group_or_user_id, up.user_id, up.profile, up.created_at, up.updated_at,
                       r.relation, r.title
                FROM user_profiles up
                LEFT JOIN relations r ON up.bot_name = r.bot_name AND up.group_or_user_id = r.group_or_user_id AND up.user_id = r.user_id
                {where_clause}
                ORDER BY up.updated_at DESC
                LIMIT ? OFFSET ?
            """
            data_params = params + [limit, offset]
            items = []
            async with self.giftia.db.conn.execute(data_sql, data_params) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    items.append(
                        {
                            "id": r["id"],
                            "bot_name": r["bot_name"],
                            "group_or_user_id": r["group_or_user_id"],
                            "user_id": r["user_id"],
                            "profile": r["profile"],
                            "relation": r["relation"]
                            if r["relation"] is not None
                            else 0,
                            "title": r["title"] if r["title"] is not None else "",
                            "created_at": r["created_at"],
                            "updated_at": r["updated_at"],
                        }
                    )

            return json_response(
                {
                    "status": "success",
                    "data": {
                        "items": items,
                        "total": total,
                        "page": page,
                        "limit": limit,
                    },
                }
            )
        except Exception as e:
            logger.error(f"[Giftia API] get_user_profiles error: {e}")
            return error_response(f"获取用户画像列表失败: {str(e)}")

    async def get_user_profile_filter_options(self):
        """Get bot/session filter options for user profiles."""
        try:
            bot_name = request.query.get("bot_name")
            user_id = request.query.get("user_id")
            search = request.query.get("search")

            bots = []
            async with self.giftia.db.conn.execute(
                """
                SELECT DISTINCT bot_name
                FROM user_profiles
                WHERE bot_name IS NOT NULL AND bot_name != ''
                ORDER BY bot_name ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
                bots = [row["bot_name"] for row in rows if row["bot_name"]]

            selected_bot_name = bot_name if bot_name in bots else (bots[0] if bots else "")

            sessions = []
            if selected_bot_name:
                conditions = ["bot_name = ?"]
                params = [selected_bot_name]
                if user_id:
                    conditions.append("user_id = ?")
                    params.append(user_id)
                if search:
                    conditions.append("profile LIKE ?")
                    params.append(f"%{search}%")

                where_clause = "WHERE " + " AND ".join(conditions)
                async with self.giftia.db.conn.execute(
                    f"""
                    SELECT group_or_user_id, COUNT(*) as total, MAX(updated_at) as latest_at
                    FROM user_profiles
                    {where_clause}
                    GROUP BY group_or_user_id
                    ORDER BY latest_at DESC, group_or_user_id ASC
                    """,
                    params,
                ) as cursor:
                    rows = await cursor.fetchall()
                    sessions = [
                        {
                            "group_or_user_id": row["group_or_user_id"],
                            "total": row["total"],
                        }
                        for row in rows
                        if row["group_or_user_id"]
                    ]

            return json_response(
                {
                    "status": "success",
                    "data": {
                        "bots": bots,
                        "selected_bot_name": selected_bot_name,
                        "sessions": sessions,
                    },
                }
            )
        except Exception as e:
            logger.error(f"[Giftia API] get_user_profile_filter_options error: {e}")
            return error_response(f"获取用户画像筛选项失败: {str(e)}")

    async def update_user_profile(self):
        """Update/Upsert user profile."""
        try:
            body = await request.json()
            bot_name = body.get("bot_name")
            group_or_user_id = body.get("group_or_user_id")
            user_id = body.get("user_id")
            profile = body.get("profile")
            relation = body.get("relation")
            title = body.get("title")

            if not bot_name or not group_or_user_id or not user_id or profile is None:
                return error_response(
                    "缺少必要参数 (bot_name, group_or_user_id, user_id, profile)"
                )

            await self.giftia.db.upsert_user_profile(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                user_id=user_id,
                profile=profile,
            )

            if relation is not None or title is not None:
                fmt_key = f"{bot_name}:{group_or_user_id}:{user_id}"
                (
                    current_relation,
                    current_title,
                ) = await self.giftia.data_cache.get_user_relation(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    user_id=user_id,
                )
                new_rel = int(relation) if relation is not None else current_relation
                new_title = str(title) if title is not None else current_title

                if relation is not None:
                    await self.giftia.db.upsert_relation(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        user_id=user_id,
                        relation=new_rel,
                    )
                if title is not None:
                    await self.giftia.db.upsert_relation_title(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        user_id=user_id,
                        title=new_title,
                    )

                self.giftia.data_cache.relations[fmt_key] = (new_rel, new_title)

            return json_response({"status": "success", "message": "更新用户画像成功"})
        except Exception as e:
            logger.error(f"[Giftia API] update_user_profile error: {e}")
            return error_response(f"更新用户画像失败: {str(e)}")

    async def delete_user_profile(self):
        """Delete user profile."""
        try:
            body = await request.json()
            bot_name = body.get("bot_name")
            group_or_user_id = body.get("group_or_user_id")
            user_id = body.get("user_id")

            if not bot_name or not group_or_user_id or not user_id:
                return error_response(
                    "缺少必要参数 (bot_name, group_or_user_id, user_id)"
                )

            await self.giftia.db.delete_user_profile(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                user_id=user_id,
            )
            return json_response({"status": "success", "message": "删除用户画像成功"})
        except Exception as e:
            logger.error(f"[Giftia API] delete_user_profile error: {e}")
            return error_response(f"删除用户画像失败: {str(e)}")

    # ── Group Profile APIs ──────────────────────────────────────────────

    async def get_group_profiles(self):
        """Get group profiles with pagination and filters."""
        try:
            page = int(request.query.get("page", 1))
            limit = int(request.query.get("limit", 20))
            bot_name = request.query.get("bot_name")
            group_or_user_id = request.query.get("group_or_user_id")
            search = request.query.get("search")

            offset = (page - 1) * limit
            conditions = []
            params = []

            if bot_name:
                conditions.append("bot_name = ?")
                params.append(bot_name)
            if group_or_user_id:
                conditions.append("group_or_user_id = ?")
                params.append(group_or_user_id)
            if search:
                conditions.append("profile LIKE ?")
                params.append(f"%{search}%")

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            # Query count
            count_sql = f"SELECT COUNT(*) as total FROM group_profiles {where_clause}"
            async with self.giftia.db.conn.execute(count_sql, params) as cursor:
                row = await cursor.fetchone()
                total = row["total"] if row else 0

            # Query data
            data_sql = f"""
                SELECT id, bot_name, group_or_user_id, profile, created_at, updated_at
                FROM group_profiles
                {where_clause}
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
            """
            data_params = params + [limit, offset]
            items = []
            async with self.giftia.db.conn.execute(data_sql, data_params) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    items.append(
                        {
                            "id": r["id"],
                            "bot_name": r["bot_name"],
                            "group_or_user_id": r["group_or_user_id"],
                            "profile": r["profile"],
                            "created_at": r["created_at"],
                            "updated_at": r["updated_at"],
                        }
                    )

            return json_response(
                {
                    "status": "success",
                    "data": {
                        "items": items,
                        "total": total,
                        "page": page,
                        "limit": limit,
                    },
                }
            )
        except Exception as e:
            logger.error(f"[Giftia API] get_group_profiles error: {e}")
            return error_response(f"获取群聊画像列表失败: {str(e)}")

    async def get_group_profile_filter_options(self):
        """Get bot/session filter options for group profiles."""
        try:
            bot_name = request.query.get("bot_name")
            search = request.query.get("search")

            bots = []
            async with self.giftia.db.conn.execute(
                """
                SELECT DISTINCT bot_name
                FROM group_profiles
                WHERE bot_name IS NOT NULL AND bot_name != ''
                ORDER BY bot_name ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
                bots = [row["bot_name"] for row in rows if row["bot_name"]]

            selected_bot_name = bot_name if bot_name in bots else (bots[0] if bots else "")

            sessions = []
            if selected_bot_name:
                conditions = ["bot_name = ?"]
                params = [selected_bot_name]
                if search:
                    conditions.append("profile LIKE ?")
                    params.append(f"%{search}%")

                where_clause = "WHERE " + " AND ".join(conditions)
                async with self.giftia.db.conn.execute(
                    f"""
                    SELECT group_or_user_id, COUNT(*) as total, MAX(updated_at) as latest_at
                    FROM group_profiles
                    {where_clause}
                    GROUP BY group_or_user_id
                    ORDER BY latest_at DESC, group_or_user_id ASC
                    """,
                    params,
                ) as cursor:
                    rows = await cursor.fetchall()
                    sessions = [
                        {
                            "group_or_user_id": row["group_or_user_id"],
                            "total": row["total"],
                        }
                        for row in rows
                        if row["group_or_user_id"]
                    ]

            return json_response(
                {
                    "status": "success",
                    "data": {
                        "bots": bots,
                        "selected_bot_name": selected_bot_name,
                        "sessions": sessions,
                    },
                }
            )
        except Exception as e:
            logger.error(f"[Giftia API] get_group_profile_filter_options error: {e}")
            return error_response(f"获取群画像筛选项失败: {str(e)}")

    async def update_group_profile(self):
        """Update/Upsert group profile."""
        try:
            body = await request.json()
            bot_name = body.get("bot_name")
            group_or_user_id = body.get("group_or_user_id")
            profile = body.get("profile")

            if not bot_name or not group_or_user_id or profile is None:
                return error_response(
                    "缺少必要参数 (bot_name, group_or_user_id, profile)"
                )

            await self.giftia.db.upsert_group_profile(
                group_or_user_id=group_or_user_id,
                bot_name=bot_name,
                profile=profile,
            )
            return json_response({"status": "success", "message": "更新群聊画像成功"})
        except Exception as e:
            logger.error(f"[Giftia API] update_group_profile error: {e}")
            return error_response(f"更新群聊画像失败: {str(e)}")

    async def delete_group_profile(self):
        """Delete group profile."""
        try:
            body = await request.json()
            bot_name = body.get("bot_name")
            group_or_user_id = body.get("group_or_user_id")

            if not bot_name or not group_or_user_id:
                return error_response("缺少必要参数 (bot_name, group_or_user_id)")

            await self.giftia.db.delete_group_profile(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
            )
            return json_response({"status": "success", "message": "删除群聊画像成功"})
        except Exception as e:
            logger.error(f"[Giftia API] delete_group_profile error: {e}")
            return error_response(f"删除群聊画像失败: {str(e)}")
