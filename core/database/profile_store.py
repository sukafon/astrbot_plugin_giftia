from datetime import datetime

from .utils import parse_aliases


class ProfileStoreMixin:
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

    async def get_user_aliases(
        self,
        bot_name: str,
        group_or_user_id: str,
        user_id: str,
        limit: int = 6,
    ) -> list[dict]:
        """获取用户外号，按统计数量优先，同数量时旧外号优先"""
        limit = max(1, int(limit or 6))
        async with self.conn.execute(
            """
            SELECT alias, alias_count, first_seen_at, last_seen_at
            FROM user_aliases
            WHERE bot_name = ? AND group_or_user_id = ? AND user_id = ?
            ORDER BY alias_count DESC, first_seen_at ASC, id ASC
            LIMIT ?
            """,
            (bot_name, group_or_user_id, user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_user_aliases_text(
        self,
        bot_name: str,
        group_or_user_id: str,
        user_id: str,
        limit: int = 6,
    ) -> str:
        aliases = await self.get_user_aliases(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            user_id=user_id,
            limit=limit,
        )
        return "，".join(item["alias"] for item in aliases)

    async def get_session_user_aliases(
        self, bot_name: str, group_or_user_id: str
    ) -> list[dict]:
        """获取当前会话内所有已知用户外号，用于后端观测计数。"""
        async with self.conn.execute(
            """
            SELECT user_id, alias
            FROM user_aliases
            WHERE bot_name = ? AND group_or_user_id = ?
            ORDER BY user_id ASC, alias_count DESC, first_seen_at ASC, id ASC
            """,
            (bot_name, group_or_user_id),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def increment_user_alias_counts(
        self,
        bot_name: str,
        group_or_user_id: str,
        observations: list[tuple[str, str, int]],
    ) -> None:
        """批量增加已知外号的观测次数。不存在的外号不会被创建。"""
        if not observations:
            return

        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for user_id, alias, count in observations:
            clean_user_id = str(user_id or "").strip()
            clean_alias = str(alias or "").strip()
            try:
                clean_count = max(1, int(count or 1))
            except (TypeError, ValueError):
                clean_count = 1
            if not clean_user_id or not clean_alias:
                continue
            await self.conn.execute(
                """
                UPDATE user_aliases
                SET
                    alias_count = alias_count + ?,
                    last_seen_at = ?,
                    updated_at = ?
                WHERE bot_name = ?
                  AND group_or_user_id = ?
                  AND user_id = ?
                  AND alias = ?
                """,
                (
                    clean_count,
                    update_time,
                    update_time,
                    bot_name,
                    group_or_user_id,
                    clean_user_id,
                    clean_alias,
                ),
            )
        await self.conn.commit()

    async def upsert_user_aliases(
        self,
        bot_name: str,
        group_or_user_id: str,
        user_id: str,
        aliases: str | list[str] | tuple[str, ...] | None,
        increment_count: bool = True,
    ) -> None:
        """记录用户外号。increment_count=True 表示本窗口观测到一次。"""
        alias_items = parse_aliases(aliases)
        if not alias_items:
            return

        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if increment_count:
            conflict_update = """
                alias_count=user_aliases.alias_count + excluded.alias_count,
                last_seen_at=excluded.last_seen_at,
                updated_at=excluded.updated_at
            """
        else:
            conflict_update = """
                updated_at=excluded.updated_at
            """

        for alias in alias_items:
            await self.conn.execute(
                f"""
                INSERT INTO user_aliases (
                    bot_name,
                    group_or_user_id,
                    user_id,
                    alias,
                    alias_count,
                    first_seen_at,
                    last_seen_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(bot_name, group_or_user_id, user_id, alias) DO UPDATE SET
                    {conflict_update}
                """,
                (
                    bot_name,
                    group_or_user_id,
                    user_id,
                    alias,
                    update_time,
                    update_time,
                    update_time,
                    update_time,
                ),
            )
        await self.conn.commit()

    async def delete_user_aliases(
        self, bot_name: str, group_or_user_id: str, user_id: str
    ) -> None:
        await self.conn.execute(
            """
            DELETE FROM user_aliases WHERE user_id = ? AND group_or_user_id = ? AND bot_name = ?
            """,
            (user_id, group_or_user_id, bot_name),
        )
        await self.conn.commit()

    async def get_user_profile_record(
        self, bot_name: str, group_or_user_id: str, user_id: str
    ) -> dict | None:
        """获取用户画像完整记录"""
        async with self.conn.execute(
            """
            SELECT
                up.profile,
                up.call_name,
                up.aliases,
                up.personality,
                up.interests,
                up.attitude,
                up.agreements,
                up.extra,
                COALESCE(up.relation, r.relation) AS relation,
                CASE WHEN up.title IS NOT NULL THEN up.title ELSE r.title END AS title
            FROM user_profiles up
            LEFT JOIN relations r ON up.bot_name = r.bot_name
                AND up.group_or_user_id = r.group_or_user_id
                AND up.user_id = r.user_id
            WHERE up.user_id = ? AND up.group_or_user_id = ? AND up.bot_name = ?
            LIMIT 1
            """,
            (user_id, group_or_user_id, bot_name),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None

        record = dict(row)
        record["aliases"] = await self.get_user_aliases_text(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            user_id=user_id,
            limit=6,
        )
        return record

    async def search_user_profiles(
        self,
        bot_name: str,
        group_or_user_id: str,
        query: str,
        limit: int = 5,
    ) -> list[dict]:
        """在当前会话内模糊搜索用户画像"""
        query = (query or "").strip()
        if not query:
            return []

        like = f"%{query}%"
        limit = max(1, min(int(limit or 5), 20))
        async with self.conn.execute(
            """
            SELECT
                up.user_id,
                up.group_or_user_id,
                up.bot_name,
                up.profile,
                up.call_name,
                up.aliases,
                up.personality,
                up.interests,
                up.attitude,
                up.agreements,
                up.extra,
                COALESCE(up.relation, r.relation) AS relation,
                CASE WHEN up.title IS NOT NULL THEN up.title ELSE r.title END AS title,
                (
                    SELECT ch.nickname
                    FROM chat_history ch
                    WHERE ch.bot_name = up.bot_name
                      AND ch.group_or_user_id = up.group_or_user_id
                      AND ch.user_id = up.user_id
                      AND ch.nickname IS NOT NULL
                      AND ch.nickname != ''
                    ORDER BY ch.created_at DESC
                    LIMIT 1
                ) AS nickname
            FROM user_profiles up
            LEFT JOIN relations r ON up.bot_name = r.bot_name
                AND up.group_or_user_id = r.group_or_user_id
                AND up.user_id = r.user_id
            WHERE up.bot_name = ?
              AND up.group_or_user_id = ?
              AND (
                up.user_id LIKE ?
                OR up.call_name LIKE ?
                OR up.aliases LIKE ?
                OR EXISTS (
                    SELECT 1
                    FROM user_aliases ua
                    WHERE ua.bot_name = up.bot_name
                      AND ua.group_or_user_id = up.group_or_user_id
                      AND ua.user_id = up.user_id
                      AND ua.alias LIKE ?
                )
                OR up.title LIKE ?
                OR up.profile LIKE ?
                OR up.personality LIKE ?
                OR up.interests LIKE ?
                OR up.attitude LIKE ?
                OR up.agreements LIKE ?
                OR up.extra LIKE ?
                OR EXISTS (
                    SELECT 1
                    FROM chat_history ch
                    WHERE ch.bot_name = up.bot_name
                      AND ch.group_or_user_id = up.group_or_user_id
                      AND ch.user_id = up.user_id
                      AND ch.nickname LIKE ?
                )
              )
            ORDER BY
                CASE
                    WHEN up.user_id = ? THEN 0
                    WHEN up.call_name = ? THEN 1
                    WHEN EXISTS (
                        SELECT 1
                        FROM user_aliases ua
                        WHERE ua.bot_name = up.bot_name
                          AND ua.group_or_user_id = up.group_or_user_id
                          AND ua.user_id = up.user_id
                          AND ua.alias = ?
                    ) THEN 2
                    WHEN up.aliases = ? THEN 3
                    ELSE 4
                END,
                up.updated_at DESC
            LIMIT ?
            """,
            (
                bot_name,
                group_or_user_id,
                like,
                like,
                like,
                like,
                like,
                like,
                like,
                like,
                like,
                like,
                like,
                like,
                query,
                query,
                query,
                query,
                limit,
            ),
        ) as cursor:
            rows = await cursor.fetchall()

        results = [dict(row) for row in rows]
        for item in results:
            item["aliases"] = await self.get_user_aliases_text(
                bot_name=item["bot_name"],
                group_or_user_id=item["group_or_user_id"],
                user_id=item["user_id"],
                limit=6,
            )
        return results

    async def upsert_user_profile(
        self,
        bot_name: str,
        group_or_user_id: str,
        user_id: str,
        profile: str | None = None,
        relation: int | None = None,
        title: str | None = None,
        profile_fields: dict[str, str | None] | None = None,
    ):
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        profile_columns = [
            "call_name",
            "personality",
            "interests",
            "attitude",
            "agreements",
            "extra",
        ]
        profile_fields = profile_fields or {}
        update_fields = ["updated_at=excluded.updated_at"]
        if profile is not None:
            update_fields.insert(-1, "profile=excluded.profile")
        for column in profile_columns:
            if column in profile_fields:
                update_fields.insert(-1, f"{column}=excluded.{column}")
        if relation is not None:
            update_fields.insert(-1, "relation=excluded.relation")
        if title is not None:
            update_fields.insert(-1, "title=excluded.title")
        update_clause = ",\n                ".join(update_fields)
        await self.conn.execute(
            f"""
            INSERT INTO user_profiles (
                user_id,
                group_or_user_id,
                bot_name,
                profile,
                call_name,
                aliases,
                personality,
                interests,
                attitude,
                agreements,
                extra,
                relation,
                title,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, group_or_user_id, bot_name) DO UPDATE SET
                {update_clause}
            """,
            (
                user_id,
                group_or_user_id,
                bot_name,
                profile if profile is not None else "",
                profile_fields.get("call_name"),
                None,
                profile_fields.get("personality"),
                profile_fields.get("interests"),
                profile_fields.get("attitude"),
                profile_fields.get("agreements"),
                profile_fields.get("extra"),
                relation,
                title,
                update_time,
                update_time,
            ),
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
        await self.conn.execute(
            """
            DELETE FROM relations WHERE user_id = ? AND group_or_user_id = ? AND bot_name = ?
            LIMIT 1
            """,
            (user_id, group_or_user_id, bot_name),
        )
        await self.conn.execute(
            """
            DELETE FROM user_aliases WHERE user_id = ? AND group_or_user_id = ? AND bot_name = ?
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
        await self.conn.execute(
            """
            DELETE FROM relations WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (group_or_user_id, bot_name),
        )
        await self.conn.execute(
            """
            DELETE FROM user_aliases WHERE group_or_user_id = ? AND bot_name = ?
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


    async def upsert_relation(
        self, bot_name: str, group_or_user_id: str, user_id: str, relation: int
    ):
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            """
            INSERT INTO user_profiles (bot_name, group_or_user_id, user_id, profile, relation, title, created_at, updated_at)
            VALUES (
                ?, ?, ?, '', ?,
                (
                    SELECT title
                    FROM relations
                    WHERE user_id = ? AND group_or_user_id = ? AND bot_name = ?
                    LIMIT 1
                ),
                ?, ?
            )
            ON CONFLICT(user_id, group_or_user_id, bot_name) DO UPDATE SET
                relation=excluded.relation,
                title=CASE
                    WHEN user_profiles.title IS NULL THEN excluded.title
                    ELSE user_profiles.title
                END,
                updated_at=excluded.updated_at
            """,
            (
                bot_name,
                group_or_user_id,
                user_id,
                relation,
                user_id,
                group_or_user_id,
                bot_name,
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
            INSERT INTO user_profiles (bot_name, group_or_user_id, user_id, profile, relation, title, created_at, updated_at)
            VALUES (
                ?, ?, ?, '',
                (
                    SELECT relation
                    FROM relations
                    WHERE user_id = ? AND group_or_user_id = ? AND bot_name = ?
                    LIMIT 1
                ),
                ?, ?, ?
            )
            ON CONFLICT(user_id, group_or_user_id, bot_name) DO UPDATE SET
                relation=CASE
                    WHEN user_profiles.relation IS NULL THEN excluded.relation
                    ELSE user_profiles.relation
                END,
                title=excluded.title,
                updated_at=excluded.updated_at
            """,
            (
                bot_name,
                group_or_user_id,
                user_id,
                user_id,
                group_or_user_id,
                bot_name,
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
            SELECT relation, title FROM user_profiles WHERE user_id = ? AND group_or_user_id = ? AND bot_name = ?
            LIMIT 1
            """,
            (user_id, group_or_user_id, bot_name),
        ) as cursor:
            row = await cursor.fetchone()
        if row and (row["relation"] is not None or row["title"] is not None):
            return row["relation"] if row["relation"] is not None else 0, row["title"] or ""

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
            UPDATE user_profiles
            SET relation = NULL, title = NULL, updated_at = ?
            WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                group_or_user_id,
                bot_name,
            ),
        )
        await self.conn.execute(
            """
            DELETE FROM relations WHERE group_or_user_id = ? AND bot_name = ?
            """,
            (group_or_user_id, bot_name),
        )
        await self.conn.commit()

