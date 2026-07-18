from datetime import datetime
import aiosqlite
from .base import BaseRepository
from ...utils.schemas import Status

class BotStatusRepository(BaseRepository):
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

    async def delete_bot_status(self, group_or_user_id: str, bot_name: str):
        await self.conn.execute(
            """
            DELETE FROM bot_status WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (group_or_user_id, bot_name),
        )
        await self.conn.commit()
