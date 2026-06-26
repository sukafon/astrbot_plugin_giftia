import json
from datetime import datetime

import aiosqlite

from astrbot.api import logger
from astrbot.api.star import StarTools

from ..utils.schemas import MediaCaption, MemoryItem, MessageData, Status, Sticker


class Database:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn
        self.db_path = (
            StarTools.get_data_dir("astrbot_plugin_giftia") / "chat_history.db"
        )

    @classmethod
    async def connect(cls):
        db_path = StarTools.get_data_dir("astrbot_plugin_giftia") / "chat_history.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # 打开持久化连接
        conn = await aiosqlite.connect(db_path)
        # 设置行工厂，使得查询结果可以通过列名访问
        conn.row_factory = aiosqlite.Row
        async with conn.cursor() as cursor:
            # 开启 WAL 模式，提高并发性能
            await cursor.execute("PRAGMA journal_mode=WAL;")
            # 创建聊天记录表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_name TEXT NOT NULL,
                    group_or_user_id TEXT,
                    nickname TEXT,
                    user_id TEXT,
                    message_id TEXT,
                    content TEXT,
                    media_ids TEXT,
                    role TEXT,
                    reply_decision INTEGER,
                    use_rag INTEGER,
                    is_recalled INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)  # 0: 判断拒绝, 1: 判断通过, 2: 未审查
            # 自动迁移已有的 chat_history 表，添加 role 列
            try:
                await cursor.execute("ALTER TABLE chat_history ADD COLUMN role TEXT")
            except aiosqlite.OperationalError as e:
                if (
                    "duplicate" not in str(e).lower()
                    and "already exists" not in str(e).lower()
                ):
                    logger.warning(f"Failed to add role column to chat_history: {e}")
            # 创建索引
            await cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_group_bot ON chat_history (group_or_user_id, bot_name, created_at)"
            )
            await cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_message_id_unique ON chat_history (message_id, group_or_user_id, bot_name)"
            )
            # 创建媒体的文字转述（caption）表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS media_caption (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hash_val TEXT NOT NULL UNIQUE,
                    file_name TEXT,
                    url TEXT,
                    media_type TEXT,
                    genre TEXT,
                    character TEXT,
                    source TEXT,
                    text TEXT,
                    caption TEXT,
                    is_captioned INTEGER DEFAULT 1,
                    query_times INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
            try:
                await cursor.execute(
                    "ALTER TABLE media_caption ADD COLUMN is_captioned INTEGER DEFAULT 1"
                )
            except aiosqlite.OperationalError as e:
                if (
                    "duplicate" not in str(e).lower()
                    and "already exists" not in str(e).lower()
                ):
                    logger.warning(
                        f"Failed to add is_captioned column to media_caption: {e}"
                    )
            # 创建索引
            await cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_url ON media_caption (url)"
            )
            await cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_hash_val ON media_caption (hash_val)"
            )
            # 创建机器人状态表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_name TEXT NOT NULL,
                    group_or_user_id TEXT NOT NULL,
                    mood TEXT,
                    state TEXT,
                    memory TEXT,
                    action TEXT,
                    energy TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
            # 创建索引
            await cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_group_unique ON bot_status (group_or_user_id, bot_name)"
            )
            # 创建用户画像表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    group_or_user_id TEXT NOT NULL,
                    profile TEXT,
                    bot_name TEXT NOT NULL,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
            # 创建索引
            await cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_id_unique ON user_profiles (user_id, group_or_user_id, bot_name)"
            )
            # 创建群画像表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_or_user_id TEXT NOT NULL,
                    profile TEXT,
                    bot_name TEXT NOT NULL,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
            # 创建索引
            await cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_group_id_unique ON group_profiles (group_or_user_id, bot_name)"
            )
            # 创建记忆缓存表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_name TEXT NOT NULL,
                    group_or_user_id TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    vector BLOB,
                    metadata TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
            # 创建索引
            await cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_id_unique ON memories (memory_id)"
            )
            await cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_group_id_bot_name_created_at_index ON memories (group_or_user_id, bot_name, created_at)"
            )
            # 创建键值对表
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
            # 创建索引
            await cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_kv_key_unique ON kv_store (key)"
            )
            # 创建关系表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_name TEXT NOT NULL,
                    group_or_user_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    relation INTEGER,
                    title TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
            # 创建索引
            await cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_relation_unique ON relations (user_id, group_or_user_id, bot_name)"
            )
            # 创建表情包表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS stickers (
                    sticker_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT,
                    tags TEXT,
                    description TEXT,
                    filename TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
            # 机器人表情包列表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS stickers_bot (
                    bot_name TEXT PRIMARY KEY,
                    sticker_ids TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
        # 提交
        await conn.commit()
        return cls(conn)

    async def insert_message(
        self,
        bot_name: str,
        message: MessageData,
    ):
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO chat_history (group_or_user_id, nickname, user_id, message_id, content, reply_decision, use_rag, is_recalled, bot_name, media_ids, role, created_at, updated_at)
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
        return [
            MessageData(
                nickname=row["nickname"],
                user_id=row["user_id"],
                time=row["created_at"],
                message_id=row["message_id"],
                content=row["content"],
                is_recalled=row["is_recalled"],
                media_id_list=json.loads(row["media_ids"]) if row["media_ids"] else [],
                role=row["role"] if "role" in row.keys() else "message",
            )
            for row in rows
        ]

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
        return [
            MessageData(
                nickname=row["nickname"],
                user_id=row["user_id"],
                time=row["created_at"],
                message_id=row["message_id"],
                content=row["content"],
                is_recalled=row["is_recalled"],
                media_id_list=json.loads(row["media_ids"]) if row["media_ids"] else [],
                role=row["role"] if "role" in row.keys() else "message",
            )
            for row in rows
        ]

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
            return MessageData(
                nickname=row["nickname"],
                user_id=row["user_id"],
                time=row["created_at"],
                message_id=row["message_id"],
                content=row["content"],
                is_recalled=row["is_recalled"],
                media_id_list=json.loads(row["media_ids"]) if row["media_ids"] else [],
                role=row["role"] if "role" in row.keys() else "message",
            )
        return None

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
            rows = reversed(list(rows))

        return [
            MessageData(
                nickname=row["nickname"],
                user_id=row["user_id"],
                time=row["created_at"],
                message_id=row["message_id"],
                content=row["content"],
                is_recalled=row["is_recalled"],
                media_id_list=json.loads(row["media_ids"]) if row["media_ids"] else [],
                role=row["role"] if "role" in row.keys() else "message",
            )
            for row in rows
        ]

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

        messages = [
            MessageData(
                nickname=row["nickname"],
                user_id=row["user_id"],
                time=row["created_at"],
                message_id=row["message_id"],
                content=row["content"],
                is_recalled=row["is_recalled"],
                media_id_list=json.loads(row["media_ids"]) if row["media_ids"] else [],
                role=row["role"] if "role" in row.keys() else "message",
            )
            for row in all_rows
        ]

        # 把目标消息插入到中间
        messages.insert(len(rows_before_list), target_msg)

        return messages

    # 清空聊天记录
    async def delete_chat_history(self, bot_name: str, group_or_user_id: str):
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

    async def get_user_profile(
        self, bot_name: str, group_or_user_id: str, user_id: str
    ) -> str | None:
        """获取用户画像"""
        async with self.conn.execute(
            """
            SELECT profile FROM user_profiles WHERE user_id = ? AND group_or_user_id = ? AND bot_name = ?
            LIMIT 1
            """,
            (user_id, group_or_user_id, bot_name),
        ) as cursor:
            row = await cursor.fetchone()
        return row["profile"] if row else None

    async def upsert_user_profile(
        self, bot_name: str, group_or_user_id: str, user_id: str, profile: str
    ):
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            INSERT INTO user_profiles (user_id, group_or_user_id, bot_name, profile, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, group_or_user_id, bot_name) DO UPDATE SET
                profile=excluded.profile,
                updated_at=excluded.updated_at
            """,
            (user_id, group_or_user_id, bot_name, profile, update_time, update_time),
        )
        await self.conn.commit()

    async def delete_user_profile(
        self, bot_name: str, group_or_user_id: str, user_id: str
    ):
        """删除用户画像"""
        await self.conn.execute(
            """
            DELETE FROM user_profiles WHERE user_id = ? AND group_or_user_id = ? AND bot_name = ?
            LIMIT 1
            """,
            (user_id, group_or_user_id, bot_name),
        )
        await self.conn.commit()

    # 删除整个群的用户画像
    async def delete_group_user_profiles(self, bot_name: str, group_or_user_id: str):
        """删除整个群的用户画像"""
        await self.conn.execute(
            """
            DELETE FROM user_profiles WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (group_or_user_id, bot_name),
        )
        await self.conn.commit()

    async def get_group_profile(
        self, group_or_user_id: str, bot_name: str
    ) -> str | None:
        """获取群画像"""
        async with self.conn.execute(
            """
            SELECT profile FROM group_profiles WHERE group_or_user_id = ? AND bot_name = ?
            LIMIT 1
            """,
            (group_or_user_id, bot_name),
        ) as cursor:
            row = await cursor.fetchone()
        return row["profile"] if row else None

    async def upsert_group_profile(
        self, group_or_user_id: str, bot_name: str, profile: str
    ):
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            INSERT INTO group_profiles (group_or_user_id, bot_name, profile, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(group_or_user_id, bot_name) DO UPDATE SET
                profile=excluded.profile,
                updated_at=excluded.updated_at
            """,
            (group_or_user_id, bot_name, profile, update_time, update_time),
        )
        await self.conn.commit()

    async def delete_group_profile(self, bot_name: str, group_or_user_id: str):
        """删除群画像"""
        await self.conn.execute(
            """
            DELETE FROM group_profiles WHERE group_or_user_id = ? AND bot_name = ?
            LIMIT 1
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
            INSERT INTO memories (bot_name, group_or_user_id, memory_id, text, vector, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bot_name,
                group_or_user_id,
                memory.memory_id,
                memory.text,
                memory.vector,
                memory.metadata,
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
                SELECT memory_id, text, vector, metadata, created_at, updated_at
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
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

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
    async def upsert_relation(
        self, bot_name: str, group_or_user_id: str, user_id: str, relation: int
    ):
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            INSERT INTO relations (bot_name, group_or_user_id, user_id, relation, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, group_or_user_id, bot_name) DO UPDATE SET
                relation=excluded.relation,
                updated_at=excluded.updated_at
            """,
            (
                bot_name,
                group_or_user_id,
                user_id,
                relation,
                update_time,
                update_time,
            ),
        )
        await self.conn.commit()

    # 更新关系头衔
    async def upsert_relation_title(
        self, bot_name: str, group_or_user_id: str, user_id: str, title: str
    ):
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            INSERT INTO relations (bot_name, group_or_user_id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, group_or_user_id, bot_name) DO UPDATE SET
                title=excluded.title,
                updated_at=excluded.updated_at
            """,
            (
                bot_name,
                group_or_user_id,
                user_id,
                title,
                update_time,
                update_time,
            ),
        )
        await self.conn.commit()

    # 获取关系数据
    async def get_relation(
        self, bot_name: str, group_or_user_id: str, user_id: str
    ) -> tuple[int, str]:
        async with self.conn.execute(
            """
            SELECT relation, title FROM relations WHERE user_id = ? AND group_or_user_id = ? AND bot_name = ?
            LIMIT 1
            """,
            (user_id, group_or_user_id, bot_name),
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            return row["relation"], row["title"]
        return 0, ""

    async def delete_all_relations(self, bot_name: str, group_or_user_id: str):
        """删除指定群或私聊的所有好感度和头衔数据"""
        await self.conn.execute(
            """
            DELETE FROM relations WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (group_or_user_id, bot_name),
        )
        await self.conn.commit()

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
