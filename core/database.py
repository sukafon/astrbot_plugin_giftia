import json
from datetime import datetime

import aiosqlite

from astrbot.api import logger
from astrbot.api.star import StarTools

from .schemas import MediaCaption, MessageData, Status


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
                    reply_decision INTEGER,
                    use_rag INTEGER,
                    is_recalled INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)  # 0: 判断拒绝, 1: 判断通过, 2: 未审查
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
                    url TEXT,
                    media_type TEXT,
                    genre TEXT,
                    character TEXT,
                    source TEXT,
                    text TEXT,
                    caption TEXT,
                    query_times INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
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
            INSERT OR IGNORE INTO chat_history (group_or_user_id, nickname, user_id, message_id, content, reply_decision, use_rag, is_recalled, bot_name, media_ids, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.group_or_user_id,
                message.nickname,
                message.user_id,
                message.message_id,
                message.content,
                2,
                2,
                0,
                bot_name,
                json.dumps(message.media_id_list),
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
                SELECT nickname, user_id, message_id, content, media_ids, is_recalled, created_at
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
            )
            for row in rows
        ]

    async def get_message_by_id(
        self, message_id: str, group_or_user_id: str, bot_name: str
    ) -> MessageData | None:
        """通过消息ID获取消息"""
        async with self.conn.execute(
            """
            SELECT nickname, user_id, message_id, content, media_ids, is_recalled, created_at
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
            )
        return None

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
            WHERE bot_name = ? AND group_or_user_id = ? AND message_id = ?
            """,
            (reply_decision, use_rag, bot_name, group_or_user_id, message_id),
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
            WHERE bot_name = ? AND group_or_user_id = ? AND message_id = ?
            """,
            (reply_decision, bot_name, group_or_user_id, message_id),
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
            WHERE bot_name = ? AND group_or_user_id = ? AND message_id IN ({})
            """.format(",".join(["?"] * len(message_ids))),
            (is_recalled, bot_name, group_or_user_id, *message_ids),
        )
        await self.conn.commit()

    async def delete_message(
        self, bot_name: str, group_or_user_id: str, message_id: str
    ):
        await self.conn.execute(
            """
            DELETE FROM chat_history
            WHERE bot_name = ? AND group_or_user_id = ? AND message_id = ?
            """,
            (bot_name, group_or_user_id, message_id),
        )
        await self.conn.commit()

    async def insert_media_caption(
        self,
        media_caption: MediaCaption,
    ):
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO media_caption (hash_val, url, media_type, genre, character, source, text, caption, query_times, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                media_caption.hash_val,
                media_caption.url,
                media_caption.media_type,
                media_caption.genre,
                media_caption.character,
                media_caption.source,
                media_caption.text,
                media_caption.caption,
                0,
                update_time,
                update_time,
            ),
        )
        await self.conn.commit()

    async def get_media_caption_by_hash(self, hash_val: str) -> MediaCaption | None:
        async with self.conn.execute(
            """
            SELECT hash_val, url, media_type, genre, character, source, text, caption, query_times FROM media_caption WHERE hash_val = ?
            """,
            (hash_val,),
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            await self.increment_media_query_times(row["hash_val"])
            caption = MediaCaption(
                hash_val=row["hash_val"],
                url=row["url"],
                media_type=row["media_type"],
                genre=row["genre"],
                character=row["character"],
                source=row["source"],
                text=row["text"],
                caption=row["caption"],
            )
            return caption
        return None

    async def get_media_caption_by_url(self, url: str) -> MediaCaption | None:
        async with self.conn.execute(
            """
            SELECT hash_val, url, media_type, genre, character, source, text, caption, query_times FROM media_caption WHERE url = ?
            """,
            (url,),
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            await self.increment_media_query_times(row["hash_val"])
            caption = MediaCaption(
                hash_val=row["hash_val"],
                url=row["url"],
                media_type=row["media_type"],
                genre=row["genre"],
                character=row["character"],
                source=row["source"],
                text=row["text"],
                caption=row["caption"],
            )
            return caption
        return None

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

    async def get_bot_status(self, group_or_user_id: str, bot_name: str) -> Status:
        async with self.conn.execute(
            """
            SELECT mood, state, memory, action, energy FROM bot_status WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (group_or_user_id, bot_name),
        ) as cursor:
            row = await cursor.fetchone()
        return (
            Status(
                mood=row["mood"],
                state=row["state"],
                memory=row["memory"],
                action=row["action"],
                energy=row["energy"],
            )
            if row
            else Status(
                mood="清爽",
                state="刚刚苏醒",
                memory="系统初始化完毕。缓存已清空，准备加载记忆碎片...",
                action="伸了个懒腰，准备开始新的一天",
                energy="100",
            )
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
