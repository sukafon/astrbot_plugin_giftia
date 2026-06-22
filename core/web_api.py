import json
import time
from datetime import datetime

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request


class GiftiaWebApi:
    """Giftia plugin web APIs for dashboard pages."""

    def __init__(self, giftia):
        self.giftia = giftia

    async def get_chat_history(self):
        """Get chat logs with pagination and filters."""
        try:
            page = int(request.query.get("page", 1))
            limit = int(request.query.get("limit", 20))
            bot_name = request.query.get("bot_name")
            group_or_user_id = request.query.get("group_or_user_id")
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

    async def get_media(self):
        """Get media captions with pagination and filters."""
        try:
            page = int(request.query.get("page", 1))
            limit = int(request.query.get("limit", 20))
            media_type = request.query.get("media_type")
            search = request.query.get("search")

            offset = (page - 1) * limit
            conditions = []
            params = []

            if media_type:
                conditions.append("media_type = ?")
                params.append(media_type)
            if search:
                conditions.append("(caption LIKE ? OR file_name LIKE ? OR hash_val LIKE ?)")
                params.append(f"%{search}%")
                params.append(f"%{search}%")
                params.append(f"%{search}%")

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            # Query count
            count_sql = f"SELECT COUNT(*) as total FROM media_caption {where_clause}"
            async with self.giftia.db.conn.execute(count_sql, params) as cursor:
                row = await cursor.fetchone()
                total = row["total"] if row else 0

            # Query data
            data_sql = f"""
                SELECT id, hash_val, file_name, url, media_type, genre, character, source, text, caption, query_times, created_at
                FROM media_caption
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
                            "hash_val": r["hash_val"],
                            "file_name": r["file_name"],
                            "url": r["url"],
                            "media_type": r["media_type"],
                            "genre": r["genre"],
                            "character": r["character"],
                            "source": r["source"],
                            "text": r["text"],
                            "caption": r["caption"],
                            "query_times": r["query_times"],
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
            logger.error(f"[Giftia API] get_media error: {e}")
            return error_response(f"获取媒体转述列表失败: {str(e)}")

    async def update_media(self):
        """Update media caption text."""
        try:
            body = await request.json()
            hash_val = body.get("hash_val")
            caption = body.get("caption")
            text = body.get("text")
            genre = body.get("genre")
            character = body.get("character")
            source = body.get("source")

            if not hash_val:
                return error_response("缺少 hash_val 参数")

            # Fetch existing cache to verify and update
            media_caption = await self.giftia.data_cache.get_caption_by_hash(hash_val)
            if not media_caption:
                return error_response("媒体记录不存在")

            media_caption.caption = caption
            if text is not None:
                media_caption.text = text
            if genre is not None:
                media_caption.genre = genre
            if character is not None:
                media_caption.character = character
            if source is not None:
                media_caption.source = source

            # Update DB
            await self.giftia.db.conn.execute(
                """
                UPDATE media_caption
                SET caption = ?, text = ?, genre = ?, character = ?, source = ?, updated_at = ?
                WHERE hash_val = ?
                """,
                (
                    caption,
                    media_caption.text,
                    media_caption.genre,
                    media_caption.character,
                    media_caption.source,
                    datetime.now().isoformat(),
                    hash_val,
                ),
            )
            await self.giftia.db.conn.commit()

            # Update cache
            self.giftia.data_cache.caption[hash_val] = media_caption

            return json_response({"status": "success", "message": "保存媒体描述成功"})
        except Exception as e:
            logger.error(f"[Giftia API] update_media error: {e}")
            return error_response(f"修改媒体描述失败: {str(e)}")

    async def delete_media(self):
        """Delete media caption cache."""
        try:
            body = await request.json()
            hash_val = body.get("hash_val")

            if not hash_val:
                return error_response("缺少 hash_val 参数")

            # Delete from DB
            await self.giftia.db.conn.execute(
                "DELETE FROM media_caption WHERE hash_val = ?", (hash_val,)
            )
            await self.giftia.db.conn.commit()

            # Remove from cache
            self.giftia.data_cache.caption.pop(hash_val, None)

            # Remove from local persistent disk cache
            try:
                from astrbot.core.star.star_tools import StarTools

                cache_file = (
                    StarTools.get_data_dir("astrbot_plugin_giftia")
                    / "media_cache"
                    / hash_val
                )
                if cache_file.exists():
                    cache_file.unlink()
                # Also delete thumbnail if exists
                thumb_file = (
                    StarTools.get_data_dir("astrbot_plugin_giftia")
                    / "media_cache"
                    / "thumbnails"
                    / hash_val
                )
                if thumb_file.exists():
                    thumb_file.unlink()
            except Exception as e:
                logger.error(f"[Giftia API] delete_media file error: {e}")

            return json_response({"status": "success", "message": "删除媒体描述成功"})
        except Exception as e:
            logger.error(f"[Giftia API] delete_media error: {e}")
            return error_response(f"删除媒体描述失败: {str(e)}")

    async def get_memories(self):
        """Get memories with pagination and filters."""
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

    async def add_memory(self):
        """Add new memory item."""
        try:
            body = await request.json()
            bot_name = body.get("bot_name")
            group_or_user_id = body.get("group_or_user_id")
            text = body.get("text")
            user_id = body.get("user_id") or "admin"

            if not bot_name or not group_or_user_id or not text:
                return error_response("缺少必要参数 (bot_name, group_or_user_id, text)")

            memory_id = await self.giftia.data_cache.add_memory(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                text=text,
                user_id=user_id,
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
        """Update bot mood or active state."""
        try:
            body = await request.json()
            bot_name = body.get("bot_name")
            group_or_user_id = body.get("group_or_user_id")
            mood = body.get("mood")
            state = body.get("state")

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

            self.giftia.data_cache.bot_status[fmt_key] = status
            await self.giftia.db.upsert_bot_status(group_or_user_id, bot_name, status)

            return json_response({"status": "success", "message": "更新 Bot 状态成功"})
        except Exception as e:
            logger.error(f"[Giftia API] update_bot_status error: {e}")
            return error_response(f"更新 Bot 状态失败: {str(e)}")

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

    async def get_media_file(self, hash_val: str):
        """Get cached media file by hash value."""
        try:
            import mimetypes

            from astrbot.api.web import file_response
            from astrbot.core.star.star_tools import StarTools

            cache_file = (
                StarTools.get_data_dir("astrbot_plugin_giftia")
                / "media_cache"
                / hash_val
            )
            if not cache_file.exists():
                return error_response("文件不存在或已被删除", status_code=404)

            content_type = None
            try:
                # Query db to get content_type based on original file name or url
                media_caption = await self.giftia.db.get_media_caption_by_hash(hash_val)
                if media_caption:
                    file_name = media_caption.file_name or media_caption.url
                    if file_name:
                        content_type, _ = mimetypes.guess_type(file_name)
                    if not content_type:
                        if media_caption.media_type == "image":
                            content_type = "image/jpeg"
                        elif media_caption.media_type in ("audio", "voice"):
                            content_type = "audio/mpeg"
            except Exception as e:
                logger.warning(f"[Giftia API] 无法从数据库获取媒体类型: {e}")

            if not content_type or content_type == "application/octet-stream":
                # fallback: check magic bytes
                try:
                    with open(cache_file, "rb") as f:
                        header = f.read(12)
                    if header.startswith(b"\x89PNG"):
                        content_type = "image/png"
                    elif header.startswith(b"\xff\xd8"):
                        content_type = "image/jpeg"
                    elif header.startswith(b"GIF8"):
                        content_type = "image/gif"
                    elif header.startswith(b"RIFF") and header[8:12] == b"WEBP":
                        content_type = "image/webp"
                    elif header.startswith(b"RIFF") and header[8:12] == b"WAVE":
                        content_type = "audio/wav"
                    elif (
                        header.startswith(b"ID3")
                        or header.startswith(b"\xff\xfb")
                        or header.startswith(b"\xff\xf3")
                        or header.startswith(b"\xff\xf2")
                    ):
                        content_type = "audio/mpeg"
                except Exception:
                    pass

            if not content_type:
                content_type = "application/octet-stream"

            return file_response(cache_file, content_type=content_type)
        except Exception as e:
            logger.error(f"[Giftia API] get_media_file error: {e}")
            return error_response(f"获取媒体文件失败: {str(e)}")

    async def get_media_file_b64(self, hash_val: str):
        """Get cached media file as base64 string (JSON response)."""
        try:
            import base64
            import mimetypes

            from astrbot.core.star.star_tools import StarTools

            cache_file = (
                StarTools.get_data_dir("astrbot_plugin_giftia")
                / "media_cache"
                / hash_val
            )
            if not cache_file.exists():
                return error_response("文件不存在或已被删除", status_code=404)

            # Determine content type
            content_type = None
            try:
                media_caption = await self.giftia.db.get_media_caption_by_hash(hash_val)
                if media_caption:
                    file_name = media_caption.file_name or media_caption.url
                    if file_name:
                        content_type, _ = mimetypes.guess_type(file_name)
                    if not content_type:
                        if media_caption.media_type == "image":
                            content_type = "image/jpeg"
                        elif media_caption.media_type in ("audio", "voice"):
                            content_type = "audio/mpeg"
            except Exception as e:
                logger.warning(f"[Giftia API] 无法从数据库获取媒体类型: {e}")

            if not content_type or content_type == "application/octet-stream":
                try:
                    with open(cache_file, "rb") as f:
                        header = f.read(12)
                    if header.startswith(b"\x89PNG"):
                        content_type = "image/png"
                    elif header.startswith(b"\xff\xd8"):
                        content_type = "image/jpeg"
                    elif header.startswith(b"GIF8"):
                        content_type = "image/gif"
                    elif header.startswith(b"RIFF") and header[8:12] == b"WEBP":
                        content_type = "image/webp"
                    elif header.startswith(b"RIFF") and header[8:12] == b"WAVE":
                        content_type = "audio/wav"
                    elif (
                        header.startswith(b"ID3")
                        or header.startswith(b"\xff\xfb")
                        or header.startswith(b"\xff\xf3")
                        or header.startswith(b"\xff\xf2")
                    ):
                        content_type = "audio/mpeg"
                except Exception:
                    pass

            if not content_type:
                content_type = "image/jpeg"

            # Read file bytes and encode to base64
            with open(cache_file, "rb") as f:
                file_bytes = f.read()

            b64_str = base64.b64encode(file_bytes).decode("utf-8")

            return json_response(
                {"status": "success", "base64": b64_str, "content_type": content_type}
            )
        except Exception as e:
            logger.error(f"[Giftia API] get_media_file_b64 error: {e}")
            return error_response(f"获取媒体 Base64 失败: {str(e)}")

    async def get_media_file_thumbnail_b64(self, hash_val: str):
        """Get cached media thumbnail as base64 string (JSON response).

        Args:
            hash_val: The hash of the media file.

        Returns:
            A dict containing the response status, base64 string, and content type.
        """
        try:
            import base64
            import mimetypes

            from astrbot.core.star.star_tools import StarTools

            cache_file = (
                StarTools.get_data_dir("astrbot_plugin_giftia")
                / "media_cache"
                / hash_val
            )
            if not cache_file.exists():
                return error_response("文件不存在或已被删除", status_code=404)

            # Determine content type of original file
            content_type = None
            try:
                media_caption = await self.giftia.db.get_media_caption_by_hash(hash_val)
                if media_caption:
                    file_name = media_caption.file_name or media_caption.url
                    if file_name:
                        content_type, _ = mimetypes.guess_type(file_name)
                    if not content_type:
                        if media_caption.media_type == "image":
                            content_type = "image/jpeg"
                        elif media_caption.media_type in ("audio", "voice"):
                            content_type = "audio/mpeg"
            except Exception as e:
                logger.warning(f"[Giftia API] 无法从数据库获取媒体类型: {e}")

            if not content_type or content_type == "application/octet-stream":
                try:
                    with open(cache_file, "rb") as f:
                        header = f.read(12)
                    if header.startswith(b"\x89PNG"):
                        content_type = "image/png"
                    elif header.startswith(b"\xff\xd8"):
                        content_type = "image/jpeg"
                    elif header.startswith(b"GIF8"):
                        content_type = "image/gif"
                    elif header.startswith(b"RIFF") and header[8:12] == b"WEBP":
                        content_type = "image/webp"
                    elif header.startswith(b"RIFF") and header[8:12] == b"WAVE":
                        content_type = "audio/wav"
                    elif (
                        header.startswith(b"ID3")
                        or header.startswith(b"\xff\xfb")
                        or header.startswith(b"\xff\xf3")
                        or header.startswith(b"\xff\xf2")
                    ):
                        content_type = "audio/mpeg"
                except Exception:
                    pass

            if not content_type:
                content_type = "image/jpeg"

            target_file = cache_file

            # If it's an image, try to load/generate thumbnail
            if content_type and content_type.startswith("image/"):
                thumb_dir = cache_file.parent / "thumbnails"
                thumb_file = thumb_dir / hash_val
                use_thumbnail = False

                try:
                    thumb_dir.mkdir(parents=True, exist_ok=True)
                    need_generate = True
                    if thumb_file.exists():
                        try:
                            if cache_file.stat().st_mtime <= thumb_file.stat().st_mtime:
                                need_generate = False
                                use_thumbnail = True
                                # Read magic bytes from cached thumbnail to determine correct content type
                                with open(thumb_file, "rb") as f:
                                    header = f.read(12)
                                if b"WEBP" in header:
                                    content_type = "image/webp"
                                elif header.startswith(b"\xff\xd8"):
                                    content_type = "image/jpeg"
                                elif header.startswith(b"\x89PNG"):
                                    content_type = "image/png"
                        except Exception as mtime_err:
                            logger.warning(
                                f"[Giftia API] Error checking cached thumbnail {hash_val}: {mtime_err}"
                            )

                    if need_generate:
                        from PIL import Image as PILImage

                        with PILImage.open(cache_file) as img:
                            # If animated (GIF, animated WebP, etc.), extract first frame
                            if getattr(img, "is_animated", False):
                                img.seek(0)
                                img = img.copy()

                            img.thumbnail((150, 150))

                            temp_thumb_path = thumb_file.with_name(
                                thumb_file.name + ".tmp"
                            )
                            try:
                                img.save(temp_thumb_path, format="WEBP")
                                content_type = "image/webp"
                            except Exception:
                                try:
                                    img.save(temp_thumb_path, format="PNG")
                                    content_type = "image/png"
                                except Exception:
                                    # Fallback to JPEG requires converting to RGB mode to support RGBA/P formats
                                    rgb_img = img.convert("RGB")
                                    rgb_img.save(temp_thumb_path, format="JPEG")
                                    content_type = "image/jpeg"

                            import os

                            os.replace(temp_thumb_path, thumb_file)
                            use_thumbnail = True
                except Exception as img_err:
                    logger.warning(
                        f"[Giftia API] Failed to generate/load thumbnail for {hash_val}, falling back to original: {img_err}"
                    )

                target_file = thumb_file if use_thumbnail else cache_file

            # Read target file bytes and encode to base64
            with open(target_file, "rb") as f:
                file_bytes = f.read()

            b64_str = base64.b64encode(file_bytes).decode("utf-8")

            return json_response(
                {"status": "success", "base64": b64_str, "content_type": content_type}
            )
        except Exception as e:
            logger.error(f"[Giftia API] get_media_file_thumbnail_b64 error: {e}")
            return error_response(f"获取媒体缩略图 Base64 失败: {str(e)}")

    async def get_media_genres(self) -> dict:
        """Get distinct genres list from media_caption table.

        Returns:
            A dict containing the response status and the list of genres.
        """
        try:
            genres = []
            async with self.giftia.db.conn.execute(
                "SELECT DISTINCT genre FROM media_caption WHERE genre IS NOT NULL AND genre != ''"
            ) as cursor:
                rows = await cursor.fetchall()
                genres = [r["genre"] for r in rows if r["genre"]]
            return json_response({"status": "success", "genres": genres})
        except Exception as e:
            logger.error(f"[Giftia API] get_media_genres error: {e}")
            return error_response(f"获取风格列表失败: {str(e)}")

    async def clean_media_cache(self) -> dict:
        """Clean media file cache by criteria (dry_run or actual).

        Returns:
            A dict containing the status, matching file count, total size freed in bytes,
            dry_run flag, and a message.
        """
        try:
            body = await request.json()
            media_type = body.get("media_type", "all")
            max_query_times = body.get("max_query_times")
            dry_run = body.get("dry_run", False)

            conditions = []
            params = []

            if media_type == "image":
                conditions.append("media_type = 'image'")
            elif media_type == "audio":
                conditions.append("media_type IN ('audio', 'voice')")

            genres = body.get("genres")
            exclude_genres = body.get("exclude_genres", False)

            if genres is not None:
                if not exclude_genres and not genres:
                    conditions.append("1 = 0")
                elif genres:
                    has_unspecified = "" in genres
                    specified_genres = [g for g in genres if g != ""]

                    if not exclude_genres:
                        if specified_genres:
                            placeholders = ",".join(["?"] * len(specified_genres))
                            if has_unspecified:
                                conditions.append(
                                    f"(genre IN ({placeholders}) OR genre IS NULL OR genre = '')"
                                )
                            else:
                                conditions.append(f"genre IN ({placeholders})")
                            params.extend(specified_genres)
                        else:
                            conditions.append("(genre IS NULL OR genre = '')")
                    else:
                        if specified_genres:
                            placeholders = ",".join(["?"] * len(specified_genres))
                            if has_unspecified:
                                conditions.append(
                                    f"(genre NOT IN ({placeholders}) AND genre IS NOT NULL AND genre != '')"
                                )
                            else:
                                conditions.append(
                                    f"(genre NOT IN ({placeholders}) OR genre IS NULL OR genre = '')"
                                )
                            params.extend(specified_genres)
                        else:
                            conditions.append("genre IS NOT NULL AND genre != ''")

            if max_query_times is not None:
                try:
                    max_query_times = int(max_query_times)
                    conditions.append("query_times <= ?")
                    params.append(max_query_times)
                except ValueError:
                    pass

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            sql = f"SELECT hash_val FROM media_caption {where_clause}"

            matching_hashes = []
            async with self.giftia.db.conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                matching_hashes = [r["hash_val"] for r in rows if r["hash_val"]]

            from astrbot.core.star.star_tools import StarTools

            cache_dir = StarTools.get_data_dir("astrbot_plugin_giftia") / "media_cache"

            cleaned_count = 0
            freed_bytes = 0

            for hash_val in matching_hashes:
                cache_file = cache_dir / hash_val
                if cache_file.exists():
                    file_size = cache_file.stat().st_size
                    cleaned_count += 1
                    freed_bytes += file_size
                    if not dry_run:
                        try:
                            cache_file.unlink()
                        except Exception as file_err:
                            logger.error(
                                f"[Giftia API] Failed to delete cache file {hash_val}: {file_err}"
                            )
                        # Also delete thumbnail if exists
                        try:
                            thumb_file = cache_dir / "thumbnails" / hash_val
                            if thumb_file.exists():
                                thumb_file.unlink()
                        except Exception as thumb_err:
                            logger.error(
                                f"[Giftia API] Failed to delete thumbnail file {hash_val}: {thumb_err}"
                            )

            action_msg = "预估" if dry_run else "成功"
            return json_response(
                {
                    "status": "success",
                    "count": cleaned_count,
                    "size_bytes": freed_bytes,
                    "dry_run": dry_run,
                    "message": f"{action_msg}清理了 {cleaned_count} 个媒体文件，释放空间 {freed_bytes} 字节",
                }
            )
        except Exception as e:
            logger.error(f"[Giftia API] clean_media_cache error: {e}")
            return error_response(f"清理媒体文件缓存失败: {str(e)}")
