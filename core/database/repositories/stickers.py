import json
from datetime import datetime
import aiosqlite
from .base import BaseRepository
from ...utils.schemas import Sticker

class StickersRepository(BaseRepository):
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

    async def insert_sticker_bot(self, sticker_id: str, bot_name: str):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
