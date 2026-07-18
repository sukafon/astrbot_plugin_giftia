import json
from datetime import datetime
import aiosqlite
from .base import BaseRepository
from ...utils.schemas import MessageData
from .chat_history import _decode_json_dict

class ForwardedMessagesRepository(BaseRepository):
    def __init__(self, conn: aiosqlite.Connection, chat_history_repo):
        super().__init__(conn)
        self.chat_history_repo = chat_history_repo

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
            owner_msg = await self.chat_history_repo.get_message_by_id(
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
