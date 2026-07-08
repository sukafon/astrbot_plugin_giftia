import json
import re
from datetime import datetime

import aiosqlite

from astrbot.api import logger
from astrbot.api.star import StarTools

from .profile_store import ProfileStoreMixin
from .schema import initialize_database

from ..utils.schemas import (
    MediaCaption,
    MemoryItem,
    MessageData,
    ShortTask,
    Status,
    Sticker,
    FORWARD_MEDIA_PATTERN,
    FORWARD_NESTED_PATTERN,
    normalize_memory_importance,
)





def _decode_json_list(raw) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _decode_json_dict(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _forward_stats(forward: dict) -> tuple[int, int, int]:
    nodes = forward.get("nodes") if isinstance(forward.get("nodes"), list) else []
    media_ids = set()
    nested_ids = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        content = str(node.get("content") or "")
        media_ids.update(FORWARD_MEDIA_PATTERN.findall(content))
        nested_ids.update(FORWARD_NESTED_PATTERN.findall(content))
        raw_media_ids = node.get("media_ids")
        if isinstance(raw_media_ids, list):
            media_ids.update(str(media_id) for media_id in raw_media_ids if media_id)
    return len(nodes), len(media_ids), len(nested_ids)


def _row_has(row: aiosqlite.Row, key: str) -> bool:
    return key in row.keys()


def _row_to_message(row: aiosqlite.Row) -> MessageData:
    return MessageData(
        db_id=row["id"] if _row_has(row, "id") and row["id"] is not None else 0,
        nickname=row["nickname"],
        user_id=row["user_id"],
        group_or_user_id=(
            row["group_or_user_id"] if _row_has(row, "group_or_user_id") else ""
        ),
        time=row["created_at"],
        message_id=row["message_id"],
        content=row["content"] or "",
        is_recalled=row["is_recalled"],
        media_id_list=_decode_json_list(row["media_ids"]),
        forward_messages=[],
        role=row["role"] if _row_has(row, "role") and row["role"] else "message",
    )


class Database(ProfileStoreMixin):
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn
        self.db_path = (
            StarTools.get_data_dir("astrbot_plugin_giftia") / "chat_history.db"
        )

    @classmethod
    async def connect(cls):
        db_path = StarTools.get_data_dir("astrbot_plugin_giftia") / "chat_history.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        await initialize_database(conn)
        return cls(conn)

    async def _upsert_forward_messages(self, bot_name: str, message: MessageData):
        if not message.forward_messages:
            return
        updated_at = datetime.now().isoformat()
        created_at = message.time or updated_at
        for forward in message.forward_messages:
            if not isinstance(forward, dict):
                continue
            forward_id = str(forward.get("id") or "").strip()
            if not forward_id:
                continue
            node_count, media_count, nested_count = _forward_stats(forward)
            await self.conn.execute(
                """
                INSERT INTO forwarded_message (
                    forward_id, bot_name, group_or_user_id, owner_message_id,
                    source, source_id, node_count, media_count, nested_count,
                    content, summary, is_summarized, query_times, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 0, ?, ?)
                ON CONFLICT(forward_id, bot_name, group_or_user_id) DO UPDATE SET
                    owner_message_id = excluded.owner_message_id,
                    source = excluded.source,
                    source_id = excluded.source_id,
                    node_count = excluded.node_count,
                    media_count = excluded.media_count,
                    nested_count = excluded.nested_count,
                    content = excluded.content,
                    updated_at = excluded.updated_at
                """,
                (
                    forward_id,
                    bot_name,
                    message.group_or_user_id,
                    message.message_id,
                    str(forward.get("source") or ""),
                    str(forward.get("source_id") or ""),
                    node_count,
                    media_count,
                    nested_count,
                    json.dumps(forward, ensure_ascii=False),
                    created_at,
                    updated_at,
                ),
            )

    async def _attach_forward_messages(
        self, bot_name: str, group_or_user_id: str, messages: list[MessageData]
    ) -> list[MessageData]:
        message_ids = [msg.message_id for msg in messages if msg.message_id]
        if not message_ids:
            return messages
        placeholders = ",".join("?" for _ in message_ids)
        async with self.conn.execute(
            f"""
            SELECT owner_message_id, content
            FROM forwarded_message
            WHERE bot_name = ?
              AND group_or_user_id = ?
              AND owner_message_id IN ({placeholders})
            ORDER BY id ASC
            """,
            (bot_name, group_or_user_id, *message_ids),
        ) as cursor:
            rows = await cursor.fetchall()

        forward_map: dict[str, list[dict]] = {}
        for row in rows:
            forward = _decode_json_dict(row["content"])
            if forward:
                forward_map.setdefault(row["owner_message_id"], []).append(forward)
        for msg in messages:
            msg.forward_messages = forward_map.get(msg.message_id, [])
        return messages

    async def insert_message(
        self,
        bot_name: str,
        message: MessageData,
    ):
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO chat_history (
                group_or_user_id, nickname, user_id, message_id, content,
                reply_decision, use_rag, is_recalled, bot_name, media_ids,
                role, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.group_or_user_id,
                message.nickname,
                message.user_id,
                message.message_id,
                message.content,
                2,  # 2: 未决策，3: @消息直接回复，1: 决策通过，0: 决策拒绝
                2,
                0,
                bot_name,
                json.dumps(message.media_id_list),
                message.role,
                message.time,
                message.time,
            ),
        )
        await self._upsert_forward_messages(bot_name, message)
        await self.conn.commit()

    async def get_messages(
        self, group_or_user_id: str, bot_name: str, limit: int = 100
    ) -> list[MessageData]:
        async with self.conn.execute(
            """
            SELECT * FROM (
                SELECT nickname, user_id, message_id, content, media_ids, is_recalled, role, created_at
                FROM chat_history
                WHERE group_or_user_id = ? AND bot_name = ?
                ORDER BY created_at DESC
                LIMIT ?
            )
            ORDER BY created_at ASC
            """,
            (group_or_user_id, bot_name, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        messages = [_row_to_message(row) for row in rows]
        return await self._attach_forward_messages(bot_name, group_or_user_id, messages)

    async def get_max_message_id(self, bot_name: str, group_or_user_id: str) -> int:
        """获取指定会话在 chat_history 表中的最大 id"""
        async with self.conn.execute(
            """
            SELECT MAX(id) as max_id FROM chat_history
            WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (group_or_user_id, bot_name),
        ) as cursor:
            row = await cursor.fetchone()
        return row["max_id"] if row and row["max_id"] is not None else 0

    async def get_messages_by_id_range(
        self, bot_name: str, group_or_user_id: str, start_id: int, end_id: int
    ) -> list[MessageData]:
        """获取指定 id 范围内的历史消息"""
        async with self.conn.execute(
            """
                SELECT nickname, user_id, message_id, content, media_ids, is_recalled, role, created_at
                FROM chat_history
                WHERE group_or_user_id = ? AND bot_name = ? AND id >= ? AND id <= ?
                ORDER BY id ASC
            """,
            (group_or_user_id, bot_name, start_id, end_id),
        ) as cursor:
            rows = await cursor.fetchall()
        messages = [_row_to_message(row) for row in rows]
        return await self._attach_forward_messages(bot_name, group_or_user_id, messages)

    async def get_user_messages_after_id(
        self,
        bot_name: str,
        group_or_user_id: str,
        user_id: str,
        after_id: int,
        limit: int = 200,
    ) -> list[MessageData]:
        """获取某个用户在指定数据库 id 之后的最近消息，按时间正序返回。"""
        if limit <= 0:
            return []
        async with self.conn.execute(
            """
            SELECT * FROM (
                SELECT id, nickname, user_id, message_id, content, media_ids, is_recalled, role, created_at
                FROM chat_history
                WHERE group_or_user_id = ?
                  AND bot_name = ?
                  AND user_id = ?
                  AND id > ?
                  AND COALESCE(role, 'message') != 'operation_log'
                  AND COALESCE(is_recalled, 0) = 0
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (group_or_user_id, bot_name, user_id, after_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        messages = [_row_to_message(row) for row in rows]
        return await self._attach_forward_messages(bot_name, group_or_user_id, messages)

    async def get_message_count_by_id_range(
        self, bot_name: str, group_or_user_id: str, start_id: int, end_id: int
    ) -> int:
        """获取指定 id 范围内的消息数量"""
        async with self.conn.execute(
            """
            SELECT COUNT(*) as count FROM chat_history
            WHERE group_or_user_id = ? AND bot_name = ? AND id > ? AND id <= ?
            """,
            (group_or_user_id, bot_name, start_id, end_id),
        ) as cursor:
            row = await cursor.fetchone()
        return row["count"] if row else 0

    async def get_boundary_message_id(
        self, bot_name: str, group_or_user_id: str, offset: int
    ) -> int:
        """获取从最新消息往回偏移 offset 处的条目的 id（即上下文窗口边界的 id）"""
        if offset <= 0:
            offset = 1
        async with self.conn.execute(
            """
            SELECT id FROM chat_history
            WHERE group_or_user_id = ? AND bot_name = ?
            ORDER BY id DESC
            LIMIT 1 OFFSET ?
            """,
            (group_or_user_id, bot_name, offset - 1),
        ) as cursor:
            row = await cursor.fetchone()
        return row["id"] if row else 0

    async def get_message_by_id(
        self, message_id: str, group_or_user_id: str, bot_name: str
    ) -> MessageData | None:
        """通过消息ID获取消息"""
        async with self.conn.execute(
            """
            SELECT nickname, user_id, message_id, content, media_ids, is_recalled, role, created_at
            FROM chat_history
            WHERE message_id = ? AND group_or_user_id = ? AND bot_name = ?
            LIMIT 1
            """,
            (message_id, group_or_user_id, bot_name),
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            messages = await self._attach_forward_messages(
                bot_name, group_or_user_id, [_row_to_message(row)]
            )
            return messages[0]
        return None

    async def get_database_id_by_message_id(
        self, message_id: str, group_or_user_id: str, bot_name: str
    ) -> int | None:
        """获取指定 message_id 在 chat_history 表中的自增 id"""
        async with self.conn.execute(
            """
            SELECT id FROM chat_history
            WHERE message_id = ? AND group_or_user_id = ? AND bot_name = ?
            LIMIT 1
            """,
            (message_id, group_or_user_id, bot_name),
        ) as cursor:
            row = await cursor.fetchone()
        return row["id"] if row else None

    async def search_messages(
        self,
        group_or_user_id: str,
        bot_name: str,
        user_id: str | None = None,
        keyword: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        sort_order: str = "desc",
        limit: int = 100,
    ) -> list[MessageData]:
        """搜索历史消息，支持关键字、指定用户以及时间范围搜索"""
        query = """
            SELECT nickname, user_id, message_id, content, media_ids, is_recalled, role, created_at
            FROM chat_history
            WHERE group_or_user_id = ? AND bot_name = ?
        """
        params = [group_or_user_id, bot_name]

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)

        if keyword:
            query += " AND content LIKE ?"
            params.append(f"%{keyword}%")

        if start_time is not None:
            query += " AND created_at >= ?"
            params.append(start_time)

        if end_time is not None:
            query += " AND created_at <= ?"
            params.append(end_time)

        if sort_order.lower() == "asc":
            query += " ORDER BY created_at ASC"
        else:
            query += " ORDER BY created_at DESC"

        query += " LIMIT ?"
        params.append(str(limit))

        async with self.conn.execute(query, tuple(params)) as cursor:
            rows = await cursor.fetchall()

        if sort_order.lower() == "desc":
            rows = list(reversed(list(rows)))

        messages = [_row_to_message(row) for row in rows]
        return await self._attach_forward_messages(bot_name, group_or_user_id, messages)

    async def find_forward_message_by_id(
        self,
        group_or_user_id: str,
        bot_name: str,
        forward_id: str,
        limit: int = 50,
    ) -> tuple[MessageData | None, dict | None]:
        """按合并转发 id 查找所在聊天记录与完整转发结构。"""
        if not forward_id:
            return None, None

        async with self.conn.execute(
            """
            SELECT owner_message_id, content
            FROM forwarded_message
            WHERE group_or_user_id = ?
              AND bot_name = ?
              AND forward_id = ?
            LIMIT 1
            """,
            (group_or_user_id, bot_name, forward_id),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None, None
        forward = _decode_json_dict(row["content"])
        owner_msg = None
        if row["owner_message_id"]:
            owner_msg = await self.get_message_by_id(
                row["owner_message_id"], group_or_user_id, bot_name
            )
        return owner_msg, forward or None

    async def get_forward_summary(
        self, bot_name: str, group_or_user_id: str, forward_id: str
    ) -> str | None:
        async with self.conn.execute(
            """
            SELECT summary, is_summarized
            FROM forwarded_message
            WHERE bot_name = ? AND group_or_user_id = ? AND forward_id = ?
            LIMIT 1
            """,
            (bot_name, group_or_user_id, forward_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row and row["is_summarized"] and row["summary"]:
            return row["summary"]
        return None

    async def update_forward_summary(
        self, bot_name: str, group_or_user_id: str, forward_id: str, summary: str
    ):
        await self.conn.execute(
            """
            UPDATE forwarded_message
            SET summary = ?, is_summarized = 1, updated_at = ?
            WHERE bot_name = ? AND group_or_user_id = ? AND forward_id = ?
            """,
            (
                summary,
                datetime.now().isoformat(),
                bot_name,
                group_or_user_id,
                forward_id,
            ),
        )
        await self.conn.commit()

    async def increment_forward_query_times(
        self, bot_name: str, group_or_user_id: str, forward_id: str
    ):
        await self.conn.execute(
            """
            UPDATE forwarded_message
            SET query_times = COALESCE(query_times, 0) + 1, updated_at = ?
            WHERE bot_name = ? AND group_or_user_id = ? AND forward_id = ?
            """,
            (datetime.now().isoformat(), bot_name, group_or_user_id, forward_id),
        )
        await self.conn.commit()

    async def get_message_context(
        self, message_id: str, group_or_user_id: str, bot_name: str, limit: int = 30
    ) -> list[MessageData]:
        """获取特定消息前后的上下文消息"""
        # 1. 获取目标消息
        target_msg = await self.get_message_by_id(
            message_id, group_or_user_id, bot_name
        )
        if not target_msg:
            return []

        target_time = target_msg.time

        # 2. 获取前面的消息（时间小于目标消息）
        query_before = """
            SELECT nickname, user_id, message_id, content, media_ids, is_recalled, role, created_at
            FROM chat_history
            WHERE group_or_user_id = ? AND bot_name = ? AND created_at < ?
            ORDER BY created_at DESC
            LIMIT ?
        """
        async with self.conn.execute(
            query_before, (group_or_user_id, bot_name, target_time, limit)
        ) as cursor:
            rows_before = await cursor.fetchall()

        # 3. 获取后面的消息（时间大于目标消息）
        query_after = """
            SELECT nickname, user_id, message_id, content, media_ids, is_recalled, role, created_at
            FROM chat_history
            WHERE group_or_user_id = ? AND bot_name = ? AND created_at > ?
            ORDER BY created_at ASC
            LIMIT ?
        """
        async with self.conn.execute(
            query_after, (group_or_user_id, bot_name, target_time, limit)
        ) as cursor:
            rows_after = await cursor.fetchall()

        # 4. 组装结果（前面的消息需要反转以保证时间正序）
        rows_before_list = list(rows_before)
        all_rows = rows_before_list[::-1] + list(rows_after)

        messages = [_row_to_message(row) for row in all_rows]

        # 把目标消息插入到中间
        messages.insert(len(rows_before_list), target_msg)

        return await self._attach_forward_messages(bot_name, group_or_user_id, messages)

    # 清空聊天记录
    async def delete_chat_history(self, bot_name: str, group_or_user_id: str):
        await self.conn.execute(
            """
            DELETE FROM forwarded_message WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (group_or_user_id, bot_name),
        )
        await self.conn.execute(
            """
            DELETE FROM chat_history WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (group_or_user_id, bot_name),
        )
        await self.conn.commit()

    async def update_message_decision(
        self,
        bot_name: str,
        group_or_user_id: str,
        message_id: str,
        reply_decision: int,
        use_rag: int,
    ):
        await self.conn.execute(
            """
            UPDATE chat_history
            SET reply_decision = ?, use_rag = ?
            WHERE message_id = ? AND group_or_user_id = ? AND bot_name = ?
            """,
            (reply_decision, use_rag, message_id, group_or_user_id, bot_name),
        )
        await self.conn.commit()

    async def update_message_reply_decision(
        self,
        bot_name: str,
        group_or_user_id: str,
        message_id: str,
        reply_decision: int,
    ):
        await self.conn.execute(
            """
            UPDATE chat_history
            SET reply_decision = ?
            WHERE message_id = ? AND group_or_user_id = ? AND bot_name = ?
            """,
            (reply_decision, message_id, group_or_user_id, bot_name),
        )
        await self.conn.commit()

    async def update_message_recall(
        self,
        bot_name: str,
        group_or_user_id: str,
        message_ids: list[str],
        is_recalled: int,
    ):
        await self.conn.execute(
            """
            UPDATE chat_history
            SET is_recalled = ?
            WHERE message_id IN ({}) AND group_or_user_id = ? AND bot_name = ?
            """.format(",".join(["?"] * len(message_ids))),
            (is_recalled, *message_ids, group_or_user_id, bot_name),
        )
        await self.conn.commit()

    async def delete_message(
        self, bot_name: str, group_or_user_id: str, message_id: str
    ):
        await self.conn.execute(
            """
            DELETE FROM forwarded_message
            WHERE owner_message_id = ? AND group_or_user_id = ? AND bot_name = ?
            """,
            (message_id, group_or_user_id, bot_name),
        )
        await self.conn.execute(
            """
            DELETE FROM chat_history
            WHERE message_id = ? AND group_or_user_id = ? AND bot_name = ?
            """,
            (message_id, group_or_user_id, bot_name),
        )
        await self.conn.commit()

    async def insert_media_caption(
        self,
        media_caption: MediaCaption,
    ):
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            INSERT INTO media_caption (hash_val, file_name, url, media_type, genre, character, source, text, caption, is_captioned, query_times, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hash_val) DO UPDATE SET
                file_name = excluded.file_name,
                url = excluded.url,
                media_type = excluded.media_type,
                genre = excluded.genre,
                character = excluded.character,
                source = excluded.source,
                text = excluded.text,
                caption = excluded.caption,
                is_captioned = excluded.is_captioned,
                updated_at = excluded.updated_at
            """,
            (
                media_caption.hash_val,
                media_caption.file_name,
                media_caption.url,
                media_caption.media_type,
                media_caption.genre,
                media_caption.character,
                media_caption.source,
                media_caption.text,
                media_caption.caption,
                1 if media_caption.is_captioned else 0,
                0,
                update_time,
                update_time,
            ),
        )
        await self.conn.commit()

    async def get_media_caption_by_hash(self, hash_val: str) -> MediaCaption | None:
        async with self.conn.execute(
            """
            SELECT hash_val, file_name, url, media_type, genre, character, source, text, caption, is_captioned, query_times FROM media_caption WHERE hash_val = ?
            """,
            (hash_val,),
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            await self.increment_media_query_times(row["hash_val"])
            caption = MediaCaption(
                hash_val=row["hash_val"],
                file_name=row["file_name"],
                url=row["url"],
                media_type=row["media_type"],
                genre=row["genre"],
                character=row["character"],
                source=row["source"],
                text=row["text"],
                caption=row["caption"],
                is_captioned=bool(row["is_captioned"]),
            )
            return caption
        return None

    async def get_media_caption_by_filename(
        self, file_name: str
    ) -> MediaCaption | None:
        async with self.conn.execute(
            """
            SELECT hash_val, file_name, url, media_type, genre, character, source, text, caption, is_captioned, query_times FROM media_caption WHERE file_name = ?
            """,
            (file_name,),
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            await self.increment_media_query_times(row["hash_val"])
            caption = MediaCaption(
                hash_val=row["hash_val"],
                file_name=row["file_name"],
                url=row["url"],
                media_type=row["media_type"],
                genre=row["genre"],
                character=row["character"],
                source=row["source"],
                text=row["text"],
                caption=row["caption"],
                is_captioned=bool(row["is_captioned"]),
            )
            return caption
        return None

    async def update_media_caption(
        self,
        media_caption: MediaCaption,
    ):
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            UPDATE media_caption
            SET genre = ?, character = ?, source = ?, text = ?, caption = ?, is_captioned = ?, updated_at = ?
            WHERE hash_val = ?
            """,
            (
                media_caption.genre,
                media_caption.character,
                media_caption.source,
                media_caption.text,
                media_caption.caption,
                1 if media_caption.is_captioned else 0,
                update_time,
                media_caption.hash_val,
            ),
        )
        await self.conn.commit()

    async def increment_media_query_times(self, hash_val: str):
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            UPDATE media_caption
            SET query_times = query_times + 1, updated_at = ?
            WHERE hash_val = ?
            """,
            (update_time, hash_val),
        )
        await self.conn.commit()

    async def update_media_url(self, hash_val: str, url: str):
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            UPDATE media_caption
            SET url = ?, updated_at = ?
            WHERE hash_val = ?
            """,
            (url, update_time, hash_val),
        )
        await self.conn.commit()

    async def clear_media_caption(self):
        await self.conn.execute(
            """
            DELETE FROM media_caption
            """
        )
        await self.conn.commit()

    @staticmethod
    def _short_task_from_row(row) -> ShortTask:
        return ShortTask(
            task_id=row["task_id"],
            bot_name=row["bot_name"],
            group_or_user_id=row["group_or_user_id"],
            creator_user_id=row["creator_user_id"] or "",
            creator_nickname=row["creator_nickname"] or "",
            content=row["content"] or "",
            status=row["status"] or "active",
            closed_by_user_id=row["closed_by_user_id"] or "",
            close_reason=row["close_reason"] or "",
            expires_at=row["expires_at"] or "",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    async def insert_short_task(self, task: ShortTask) -> None:
        await self.conn.execute(
            """
            INSERT INTO short_tasks (
                task_id, bot_name, group_or_user_id, creator_user_id,
                creator_nickname, content, status, closed_by_user_id,
                close_reason, expires_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.bot_name,
                task.group_or_user_id,
                task.creator_user_id,
                task.creator_nickname,
                task.content,
                task.status,
                task.closed_by_user_id,
                task.close_reason,
                task.expires_at,
                task.created_at,
                task.updated_at,
            ),
        )
        await self.conn.commit()

    async def expire_short_tasks(
        self, bot_name: str | None = None, group_or_user_id: str | None = None
    ) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conditions = [
            "status = 'active'",
            "expires_at IS NOT NULL",
            "expires_at != ''",
            "expires_at <= ?",
        ]
        condition_params = [now]
        if bot_name:
            conditions.append("bot_name = ?")
            condition_params.append(bot_name)
        if group_or_user_id:
            conditions.append("group_or_user_id = ?")
            condition_params.append(group_or_user_id)

        cursor = await self.conn.execute(
            f"""
            UPDATE short_tasks
            SET status = 'expired',
                closed_by_user_id = 'system',
                close_reason = '任务已过期',
                updated_at = ?
            WHERE {" AND ".join(conditions)}
            """,
            [now] + condition_params,
        )
        await self.conn.commit()
        return cursor.rowcount or 0

    async def get_short_tasks(
        self,
        bot_name: str,
        group_or_user_id: str,
        statuses: list[str] | None = None,
        limit: int | None = None,
    ) -> list[ShortTask]:
        conditions = ["bot_name = ?", "group_or_user_id = ?"]
        params: list = [bot_name, group_or_user_id]
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            conditions.append(f"status IN ({placeholders})")
            params.extend(statuses)

        limit_sql = ""
        if limit is not None:
            limit_sql = "LIMIT ?"
            params.append(limit)

        async with self.conn.execute(
            f"""
            SELECT task_id, bot_name, group_or_user_id, creator_user_id,
                   creator_nickname, content, status, closed_by_user_id,
                   close_reason, expires_at, created_at, updated_at
            FROM short_tasks
            WHERE {" AND ".join(conditions)}
            ORDER BY
                CASE status
                    WHEN 'active' THEN 0
                    WHEN 'completed' THEN 1
                    WHEN 'canceled' THEN 2
                    WHEN 'expired' THEN 3
                    ELSE 4
                END,
                created_at ASC
            {limit_sql}
            """,
            params,
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._short_task_from_row(row) for row in rows]

    async def get_short_task(
        self, task_id: str, bot_name: str, group_or_user_id: str
    ) -> ShortTask | None:
        async with self.conn.execute(
            """
            SELECT task_id, bot_name, group_or_user_id, creator_user_id,
                   creator_nickname, content, status, closed_by_user_id,
                   close_reason, expires_at, created_at, updated_at
            FROM short_tasks
            WHERE task_id = ? AND bot_name = ? AND group_or_user_id = ?
            LIMIT 1
            """,
            (task_id, bot_name, group_or_user_id),
        ) as cursor:
            row = await cursor.fetchone()
        return self._short_task_from_row(row) if row else None

    async def count_active_short_tasks(
        self, bot_name: str, group_or_user_id: str
    ) -> int:
        async with self.conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM short_tasks
            WHERE bot_name = ? AND group_or_user_id = ? AND status = 'active'
            """,
            (bot_name, group_or_user_id),
        ) as cursor:
            row = await cursor.fetchone()
        return row["total"] if row else 0

    async def update_short_task_status(
        self,
        task_id: str,
        bot_name: str,
        group_or_user_id: str,
        status: str,
        closed_by_user_id: str = "",
        close_reason: str = "",
    ) -> bool:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self.conn.execute(
            """
            UPDATE short_tasks
            SET status = ?,
                closed_by_user_id = ?,
                close_reason = ?,
                updated_at = ?
            WHERE task_id = ? AND bot_name = ? AND group_or_user_id = ?
              AND status = 'active'
            """,
            (
                status,
                closed_by_user_id,
                close_reason,
                now,
                task_id,
                bot_name,
                group_or_user_id,
            ),
        )
        await self.conn.commit()
        return (cursor.rowcount or 0) > 0

    async def update_short_task(
        self,
        task_id: str,
        bot_name: str,
        group_or_user_id: str,
        content: str,
        status: str,
        expires_at: str,
        closed_by_user_id: str = "",
        close_reason: str = "",
    ) -> bool:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self.conn.execute(
            """
            UPDATE short_tasks
            SET content = ?,
                status = ?,
                expires_at = ?,
                closed_by_user_id = ?,
                close_reason = ?,
                updated_at = ?
            WHERE task_id = ? AND bot_name = ? AND group_or_user_id = ?
            """,
            (
                content,
                status,
                expires_at,
                closed_by_user_id,
                close_reason,
                now,
                task_id,
                bot_name,
                group_or_user_id,
            ),
        )
        await self.conn.commit()
        return (cursor.rowcount or 0) > 0

    async def delete_short_task(
        self, task_id: str, bot_name: str, group_or_user_id: str
    ) -> bool:
        cursor = await self.conn.execute(
            """
            DELETE FROM short_tasks
            WHERE task_id = ? AND bot_name = ? AND group_or_user_id = ?
            """,
            (task_id, bot_name, group_or_user_id),
        )
        await self.conn.commit()
        return (cursor.rowcount or 0) > 0

    async def get_short_task_stats(
        self, bot_name: str, group_or_user_id: str
    ) -> dict:
        await self.expire_short_tasks(bot_name, group_or_user_id)
        stats = {
            "active": 0,
            "completed": 0,
            "canceled": 0,
            "expired": 0,
            "total": 0,
            "latest_updated_at": "",
        }
        async with self.conn.execute(
            """
            SELECT status, COUNT(*) AS total, MAX(updated_at) AS latest_updated_at
            FROM short_tasks
            WHERE bot_name = ? AND group_or_user_id = ?
            GROUP BY status
            """,
            (bot_name, group_or_user_id),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            status = row["status"] or ""
            total = row["total"] or 0
            if status in stats:
                stats[status] = total
            stats["total"] += total
            latest = row["latest_updated_at"] or ""
            if latest and latest > stats["latest_updated_at"]:
                stats["latest_updated_at"] = latest
        return stats

    async def get_bot_status(self, group_or_user_id: str, bot_name: str) -> Status:
        async with self.conn.execute(
            """
            SELECT mood, state, memory, action, energy, updated_at FROM bot_status WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (group_or_user_id, bot_name),
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            try:
                ts = datetime.strptime(
                    row["updated_at"], "%Y-%m-%d %H:%M:%S"
                ).timestamp()
            except Exception:
                ts = 0.0
            return Status(
                mood=row["mood"],
                state=row["state"],
                memory=row["memory"],
                action=row["action"],
                energy=row["energy"],
                timestamp=ts,
            )
        else:
            return Status(
                mood="开心",
                state="发呆",
                memory="",
                action="拿起手机聊天",
                energy="80",
                timestamp=0.0,
            )

    async def upsert_bot_status(
        self, group_or_user_id: str, bot_name: str, status: Status
    ):
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            INSERT INTO bot_status (group_or_user_id, bot_name, mood, state, memory, action, energy, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_or_user_id, bot_name) DO UPDATE SET
                mood=excluded.mood,
                state=excluded.state,
                memory=excluded.memory,
                action=excluded.action,
                energy=excluded.energy,
                updated_at=excluded.updated_at
            """,
            (
                group_or_user_id,
                bot_name,
                status.mood,
                status.state,
                status.memory,
                status.action,
                status.energy,
                update_time,
                update_time,
            ),
        )
        await self.conn.commit()

    # 删除bot状态
    async def delete_bot_status(self, group_or_user_id: str, bot_name: str):
        await self.conn.execute(
            """
            DELETE FROM bot_status WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (group_or_user_id, bot_name),
        )
        await self.conn.commit()

    async def insert_memory(
        self, bot_name: str, group_or_user_id: str, memory: MemoryItem
    ):
        """写入记忆"""
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            INSERT INTO memories (
                bot_name, group_or_user_id, memory_id, text, vector, metadata,
                importance, hit_count, last_hit_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bot_name,
                group_or_user_id,
                memory.memory_id,
                memory.text,
                memory.vector,
                memory.metadata,
                normalize_memory_importance(memory.importance),
                int(memory.hit_count or 0),
                memory.last_hit_at or None,
                update_time,
                update_time,
            ),
        )
        await self.conn.commit()

    async def get_memories(
        self, group_or_user_id: str, bot_name: str, limit: int = 10
    ) -> list[MemoryItem]:
        """获取近期记忆，并按时间正序排列"""
        async with self.conn.execute(
            """
            SELECT * FROM (
                SELECT memory_id, text, vector, metadata, importance, hit_count, last_hit_at, created_at, updated_at
                FROM memories
                WHERE group_or_user_id = ? AND bot_name = ?
                ORDER BY created_at DESC
                LIMIT ?
            )
            ORDER BY created_at ASC
            """,
            (group_or_user_id, bot_name, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            MemoryItem(
                memory_id=row["memory_id"],
                text=row["text"],
                vector=row["vector"],
                metadata=row["metadata"],
                importance=normalize_memory_importance(row["importance"]),
                hit_count=int(row["hit_count"] or 0),
                last_hit_at=row["last_hit_at"] or "",
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def record_memory_hits(self, memory_ids: list[str], hit_at: str | None = None):
        """记录长期记忆被有效召回的次数与最近命中时间。"""
        clean_ids = []
        seen = set()
        for memory_id in memory_ids or []:
            memory_id = str(memory_id or "").strip()
            if memory_id and memory_id not in seen:
                seen.add(memory_id)
                clean_ids.append(memory_id)

        if not clean_ids:
            return

        hit_at = hit_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        placeholders = ",".join("?" for _ in clean_ids)
        await self.conn.execute(
            f"""
            UPDATE memories
            SET hit_count = COALESCE(hit_count, 0) + 1,
                last_hit_at = ?
            WHERE memory_id IN ({placeholders})
            """,
            [hit_at, *clean_ids],
        )
        await self.conn.commit()

    async def delete_memory(self, memory_id: str):
        """删除记忆"""
        await self.conn.execute(
            """
            DELETE FROM memories WHERE memory_id = ?
            """,
            (memory_id,),
        )
        await self.conn.commit()

    async def delete_all_memories(self, bot_name: str, group_or_user_id: str):
        """删除全部记忆"""
        await self.conn.execute(
            """
            DELETE FROM memories WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (group_or_user_id, bot_name),
        )
        await self.conn.commit()

    async def get_kv_data(self, key: str, default=None):
        """获取键值对数据"""
        async with self.conn.execute(
            """
            SELECT value FROM kv_store WHERE key = ?
            LIMIT 1
            """,
            (key,),
        ) as cursor:
            row = await cursor.fetchone()
        return row["value"] if row else default

    async def upsert_kv_data(self, key: str, value: str | int | float | bool):
        """更新键值对数据"""
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            INSERT INTO kv_store (key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, value, update_time, update_time),
        )
        await self.conn.commit()

    async def delete_kv_data(self, key: str):
        """删除键值对数据"""
        await self.conn.execute(
            """
            DELETE FROM kv_store WHERE key = ?
            """,
            (key,),
        )
        await self.conn.commit()

    # 更新关系数据
    # 添加表情包数据
    async def insert_sticker(
        self,
        sticker_id: str,
        name: str,
        category: str,
        tags: list[str],
        description: str,
        filename: str = "",
    ):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tags_json = json.dumps(tags, ensure_ascii=False)
        await self.conn.execute(
            """
            INSERT INTO stickers (sticker_id, name, category, tags, description, filename, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sticker_id) DO UPDATE SET
                name=excluded.name,
                category=excluded.category,
                tags=excluded.tags,
                description=excluded.description,
                filename=excluded.filename,
                updated_at=excluded.updated_at
            """,
            (
                sticker_id,
                name,
                category,
                tags_json,
                description,
                filename,
                now_str,
                now_str,
            ),
        )
        await self.conn.commit()

    async def get_sticker(self) -> list[Sticker]:
        """获取全部表情包数据"""
        async with self.conn.execute(
            "SELECT sticker_id, name, category, tags, description, filename FROM stickers"
        ) as cursor:
            rows = await cursor.fetchall()
        results: list[Sticker] = []
        for row in rows:
            # 安全处理 tags 为 None 的情况
            try:
                tags = json.loads(row["tags"]) if row["tags"] else []
            except json.JSONDecodeError:
                tags = []

            results.append(
                Sticker(
                    sticker_id=row["sticker_id"],
                    name=row["name"],
                    category=row["category"],
                    tags=tags,
                    description=row["description"],
                    filename=row["filename"],
                )
            )
        return results

    async def delete_sticker(self, sticker_id: str):
        """删除表情包数据"""
        await self.conn.execute(
            """
            DELETE FROM stickers WHERE sticker_id = ?
            """,
            (sticker_id,),
        )
        await self.conn.commit()

    # 机器人表情包列表
    async def insert_sticker_bot(self, sticker_id: str, bot_name: str):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 先查询是否已有记录
        async with self.conn.execute(
            """
            SELECT sticker_ids FROM stickers_bot WHERE bot_name = ?
            """,
            (bot_name,),
        ) as cursor:
            row = await cursor.fetchone()
        if row and row["sticker_ids"]:
            sticker_ids = set(json.loads(row["sticker_ids"]))
        else:
            sticker_ids = set()
        sticker_ids.add(sticker_id)
        sticker_ids_json = json.dumps(list(sticker_ids), ensure_ascii=False)
        await self.conn.execute(
            """
            INSERT INTO stickers_bot (bot_name, sticker_ids, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(bot_name) DO UPDATE SET
                sticker_ids=excluded.sticker_ids,
                updated_at=excluded.updated_at
            """,
            (
                bot_name,
                sticker_ids_json,
                now_str,
                now_str,
            ),
        )
        await self.conn.commit()

    async def get_sticker_categories(self) -> list[str]:
        """获取所有已知的表情包分类"""
        cursor = await self.conn.execute(
            "SELECT DISTINCT category FROM stickers WHERE category IS NOT NULL"
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows if row[0]]

    async def get_sticker_bot(self, bot_name: str) -> list[str]:
        """获取机器人表情包列表"""
        async with self.conn.execute(
            """
            SELECT sticker_ids FROM stickers_bot WHERE bot_name = ?
            """,
            (bot_name,),
        ) as cursor:
            row = await cursor.fetchone()
        if row and row["sticker_ids"]:
            return json.loads(row["sticker_ids"])
        return []

    # 删除数据表
    async def drop_table(self, table_name: str):
        """删除数据表"""
        # 检查表是否存在
        async with self.conn.execute(
            """
            SELECT name FROM sqlite_master WHERE type='table' AND name=?
            """,
            (table_name,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return False
        await self.conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        await self.conn.commit()
        return True

    async def backup_chat_history_db(self, last_backup_time: float):
        """
        备份数据库，这里不考虑数据分块，如果有必要，那么最优先应该处理的是删除早期数据
        由于数据表众多，增量备份实现过于复杂，因此只能采用全量备份
        """
        # 先检测修改时间
        wal_path = self.db_path.with_name(f"{self.db_path.name}-wal")
        # 取两个文件的最大值
        current_mtime = self.db_path.stat().st_mtime
        if wal_path.exists():
            current_mtime = max(current_mtime, wal_path.stat().st_mtime)

        # 对比时间戳：如果当前修改时间 <= 上次备份时间，则说明没有新数据
        if current_mtime <= last_backup_time:
            logger.debug("数据库自上次备份以来无变动，跳过备份。")
            return None

        # 开始备份
        backup_temp_path = (
            StarTools.get_data_dir("astrbot_plugin_giftia") / "chat_history_backup.db"
        )
        try:
            if backup_temp_path.exists():
                backup_temp_path.unlink()
            safe_path = backup_temp_path.as_posix()
            async with aiosqlite.connect(self.db_path) as backup_conn:
                await backup_conn.execute(f"VACUUM INTO '{safe_path}'")
            logger.info(f"本地临时备份已创建: {backup_temp_path}")
            return backup_temp_path
        except Exception as e:
            logger.error(f"创建本地备份临时文件失败: {e}")
            return None

    async def close(self):
        if self.conn:
            await self.conn.close()
