import json
from collections import defaultdict
from datetime import datetime, timedelta
import aiosqlite
from astrbot.api import logger
from .base import BaseRepository

class TokenUsageRepository(BaseRepository):
    async def log_token_usage(
        self,
        bot_name: str,
        group_or_user_id: str,
        type: str,
        provider_id: str,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        extra_info: dict | None = None,
    ):
        try:
            extra_info_str = json.dumps(extra_info, ensure_ascii=False) if extra_info else None
            await self.conn.execute(
                """
                INSERT INTO token_usage (
                    bot_name, group_or_user_id, type, provider_id, model_name,
                    prompt_tokens, completion_tokens, total_tokens, extra_info
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bot_name or "",
                    group_or_user_id or "",
                    type,
                    provider_id or "",
                    model_name or "",
                    prompt_tokens or 0,
                    completion_tokens or 0,
                    total_tokens or 0,
                    extra_info_str,
                ),
            )
            await self.conn.commit()
        except Exception as e:
            logger.error(f"[Database] Failed to log token usage: {e}")

    def _get_time_range_thresholds(self, time_range: str) -> tuple[str | None, str | None]:
        """返回对应的时间阈值 (usage_threshold, daily_threshold)"""
        if not time_range:
            return None, None
        now = datetime.now()
        if time_range == "today":
            today_str = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
            tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            return today_str, tomorrow_str
        elif time_range == "week":
            week_dt = now - timedelta(days=7)
            return week_dt.strftime("%Y-%m-%d %H:%M:%S"), week_dt.strftime("%Y-%m-%d")
        elif time_range == "month":
            month_dt = now - timedelta(days=30)
            return month_dt.strftime("%Y-%m-%d %H:%M:%S"), month_dt.strftime("%Y-%m-%d")
        elif time_range == "3months":
            m3_dt = now - timedelta(days=90)
            return m3_dt.strftime("%Y-%m-%d %H:%M:%S"), m3_dt.strftime("%Y-%m-%d")
        elif time_range == "year":
            year_dt = now - timedelta(days=365)
            return year_dt.strftime("%Y-%m-%d %H:%M:%S"), year_dt.strftime("%Y-%m-%d")
        return None, None

    async def get_token_stats(
        self,
        bot_name: str = None,
        group_or_user_id: str = None,
        time_range: str = None,
    ) -> dict:
        try:
            conditions_usage = []
            params_usage = []
            conditions_daily = []
            params_daily = []

            if bot_name:
                conditions_usage.append("bot_name = ?")
                params_usage.append(bot_name)
                conditions_daily.append("bot_name = ?")
                params_daily.append(bot_name)

            if group_or_user_id:
                conditions_usage.append("group_or_user_id = ?")
                params_usage.append(group_or_user_id)
                conditions_daily.append("group_or_user_id = ?")
                params_daily.append(group_or_user_id)

            if time_range:
                usage_thresh, daily_thresh = self._get_time_range_thresholds(time_range)
                if usage_thresh:
                    conditions_usage.append("created_at >= ?")
                    params_usage.append(usage_thresh)
                if daily_thresh:
                    conditions_daily.append("date >= ?")
                    params_daily.append(daily_thresh)

            where_usage = f"WHERE {' AND '.join(conditions_usage)}" if conditions_usage else ""
            where_daily = f"WHERE {' AND '.join(conditions_daily)}" if conditions_daily else ""

            # Query token_usage (今日明细)
            sql_usage = f"""
                SELECT group_or_user_id, model_name, type,
                       SUM(prompt_tokens) as prompt,
                       SUM(completion_tokens) as completion,
                       SUM(total_tokens) as total
                FROM token_usage
                {where_usage}
                GROUP BY group_or_user_id, model_name, type
            """

            # Query token_daily_stats (历史归档)
            sql_daily = f"""
                SELECT group_or_user_id, model_name, type,
                       SUM(prompt_tokens) as prompt,
                       SUM(completion_tokens) as completion,
                       SUM(total_tokens) as total
                FROM token_daily_stats
                {where_daily}
                GROUP BY group_or_user_id, model_name, type
            """

            merged = defaultdict(lambda: {"prompt": 0, "completion": 0, "total": 0})

            async with self.conn.execute(sql_usage, params_usage) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    key = (r["group_or_user_id"] or "", r["model_name"] or "", r["type"])
                    merged[key]["prompt"] += r["prompt"] or 0
                    merged[key]["completion"] += r["completion"] or 0
                    merged[key]["total"] += r["total"] or 0

            async with self.conn.execute(sql_daily, params_daily) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    key = (r["group_or_user_id"] or "", r["model_name"] or "", r["type"])
                    merged[key]["prompt"] += r["prompt"] or 0
                    merged[key]["completion"] += r["completion"] or 0
                    merged[key]["total"] += r["total"] or 0

            summary = {
                "total_tokens": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_chars_tts": 0,
                "decision_tokens": 0,
                "decision_prompt_tokens": 0,
                "decision_completion_tokens": 0,
                "reply_tokens": 0,
                "reply_prompt_tokens": 0,
                "reply_completion_tokens": 0,
                "image_caption_tokens": 0,
                "audio_caption_tokens": 0,
                "tts_chars": 0,
                "passive_summary_tokens": 0,
                "sticker_analysis_tokens": 0,
            }

            group_map = defaultdict(lambda: {"tokens": 0, "tts_chars": 0})
            model_map = defaultdict(lambda: {"prompt": 0, "completion": 0, "total": 0, "tts_chars": 0})

            for key, val in merged.items():
                group_id, model_name, type_name = key
                prompt = val["prompt"]
                completion = val["completion"]
                total = val["total"]

                if type_name != "tts":
                    summary["total_tokens"] += total
                    summary["total_prompt_tokens"] += prompt
                    summary["total_completion_tokens"] += completion

                    group_map[group_id]["tokens"] += total
                    model_map[model_name]["prompt"] += prompt
                    model_map[model_name]["completion"] += completion
                    model_map[model_name]["total"] += total
                else:
                    summary["total_chars_tts"] += total
                    summary["tts_chars"] += total

                    group_map[group_id]["tts_chars"] += total
                    model_map[model_name]["tts_chars"] += total

                if type_name == "decision":
                    summary["decision_tokens"] += total
                    summary["decision_prompt_tokens"] += prompt
                    summary["decision_completion_tokens"] += completion
                elif type_name == "reply":
                    summary["reply_tokens"] += total
                    summary["reply_prompt_tokens"] += prompt
                    summary["reply_completion_tokens"] += completion
                elif type_name == "image_caption":
                    summary["image_caption_tokens"] += total
                elif type_name == "audio_caption":
                    summary["audio_caption_tokens"] += total
                elif type_name == "passive_summary":
                    summary["passive_summary_tokens"] += total
                elif type_name == "sticker_analysis":
                    summary["sticker_analysis_tokens"] += total

            by_group = []
            for gid, gval in group_map.items():
                by_group.append({
                    "group_or_user_id": gid or "私聊/系统",
                    "total_tokens": gval["tokens"],
                    "total_chars_tts": gval["tts_chars"]
                })
            by_group.sort(key=lambda x: x["total_tokens"], reverse=True)

            by_model = []
            for mname, mval in model_map.items():
                by_model.append({
                    "model_name": mname or "未知",
                    "prompt_tokens": mval["prompt"],
                    "completion_tokens": mval["completion"],
                    "total_tokens": mval["total"],
                    "total_chars_tts": mval["tts_chars"]
                })
            by_model.sort(key=lambda x: x["total_tokens"], reverse=True)

            return {
                "summary": summary,
                "by_group": by_group,
                "by_model": by_model,
            }
        except Exception as e:
            logger.error(f"[Database] get_token_stats error: {e}", exc_info=True)
            return {
                "summary": {},
                "by_group": [],
                "by_model": [],
            }

    async def get_token_logs(
        self,
        page: int = 1,
        page_size: int = 20,
        type: str = None,
        bot_name: str = None,
        group_or_user_id: str = None,
        time_range: str = None,
    ) -> tuple[list[dict], int]:
        try:
            conditions = []
            params = []
            if type:
                conditions.append("type = ?")
                params.append(type)
            if bot_name:
                conditions.append("bot_name = ?")
                params.append(bot_name)
            if group_or_user_id:
                conditions.append("group_or_user_id = ?")
                params.append(group_or_user_id)
            if time_range:
                now = datetime.now()
                if time_range == "today":
                    today_str = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
                    conditions.append("created_at >= ?")
                    params.append(today_str)
                elif time_range == "week":
                    week_str = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
                    conditions.append("created_at >= ?")
                    params.append(week_str)
                elif time_range == "month":
                    month_str = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
                    conditions.append("created_at >= ?")
                    params.append(month_str)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            # Count total
            sql_count = f"SELECT COUNT(*) as count FROM token_usage {where_clause}"
            async with self.conn.execute(sql_count, params) as cursor:
                total_count = (await cursor.fetchone())["count"]

            # Query limit offset
            offset = (page - 1) * page_size
            sql_select = f"""
                SELECT id, bot_name, group_or_user_id, type, provider_id, model_name,
                       prompt_tokens, completion_tokens, total_tokens, created_at
                FROM token_usage
                {where_clause}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
            """
            select_params = params + [page_size, offset]
            logs = []
            async with self.conn.execute(sql_select, select_params) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    logs.append({
                        "id": r["id"],
                        "bot_name": r["bot_name"],
                        "group_or_user_id": r["group_or_user_id"],
                        "type": r["type"],
                        "provider_id": r["provider_id"],
                        "model_name": r["model_name"],
                        "prompt_tokens": r["prompt_tokens"],
                        "completion_tokens": r["completion_tokens"],
                        "total_tokens": r["total_tokens"],
                        "created_at": r["created_at"],
                    })

            return logs, total_count
        except Exception as e:
            logger.error(f"[Database] get_token_logs error: {e}")
            return [], 0

    async def squash_token_logs(self) -> int:
        """将昨日及以前的详细 Token 日志合并压扁并归档，随后清理详细明细"""
        try:
            # 1. 确定今日凌晨的临界时间点（本地时间）
            cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

            # 2. 查询所有早于临界点的详细记录
            sql_select = """
                SELECT bot_name, group_or_user_id, type, provider_id, model_name,
                       prompt_tokens, completion_tokens, total_tokens, extra_info, created_at, call_count
                FROM token_usage
                WHERE created_at < ?
            """

            rows_to_squash = []
            async with self.conn.execute(sql_select, (cutoff,)) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    rows_to_squash.append({
                        "bot_name": r["bot_name"],
                        "group_or_user_id": r["group_or_user_id"],
                        "type": r["type"],
                        "provider_id": r["provider_id"],
                        "model_name": r["model_name"],
                        "prompt_tokens": r["prompt_tokens"] or 0,
                        "completion_tokens": r["completion_tokens"] or 0,
                        "total_tokens": r["total_tokens"] or 0,
                        "extra_info": r["extra_info"],
                        "created_at": r["created_at"],
                        "call_count": dict(r).get("call_count", 1) or 1,
                    })

            if not rows_to_squash:
                return 0

            # 3. 按维度分类累加
            daily_sums = defaultdict(lambda: {"prompt": 0, "completion": 0, "total": 0, "count": 0})
            for r in rows_to_squash:
                # 提取日期 YYYY-MM-DD
                date_str = r["created_at"].split(" ")[0] if r["created_at"] else datetime.now().strftime("%Y-%m-%d")

                # 提取 status 字段
                status = "success"
                if r["extra_info"]:
                    try:
                        info = json.loads(r["extra_info"])
                        status = info.get("status", "success")
                    except Exception:
                        pass

                key = (
                    date_str,
                    r["bot_name"] or "",
                    r["group_or_user_id"] or "",
                    r["type"],
                    r["provider_id"] or "",
                    r["model_name"] or "",
                    status
                )
                daily_sums[key]["prompt"] += r["prompt_tokens"]
                daily_sums[key]["completion"] += r["completion_tokens"]
                daily_sums[key]["total"] += r["total_tokens"]
                daily_sums[key]["count"] += r["call_count"]

            # 4. 批量 Upsert 合并到历史表
            upsert_count = 0
            for key, sums in daily_sums.items():
                date_val, bot_val, group_val, type_val, provider_val, model_val, status_val = key
                await self.conn.execute(
                    """
                    INSERT INTO token_daily_stats (
                        date, bot_name, group_or_user_id, type, provider_id, model_name, status,
                        prompt_tokens, completion_tokens, total_tokens, call_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, bot_name, group_or_user_id, type, provider_id, model_name, status)
                    DO UPDATE SET
                        prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                        completion_tokens = completion_tokens + excluded.completion_tokens,
                        total_tokens = total_tokens + excluded.total_tokens,
                        call_count = call_count + excluded.call_count
                    """,
                    (
                        date_val, bot_val, group_val, type_val, provider_val, model_val, status_val,
                        sums["prompt"], sums["completion"], sums["total"], sums["count"]
                    )
                )
                upsert_count += 1

            # 5. 删除已拍扁的详细日志
            cursor_del = await self.conn.execute("DELETE FROM token_usage WHERE created_at < ?", (cutoff,))
            deleted_count = cursor_del.rowcount
            await self.conn.commit()

            logger.info(f"[Database] Squash completed: consolidated {deleted_count} detailed rows into {upsert_count} summary rows.")
            return deleted_count
        except Exception as e:
            logger.error(f"[Database] squash_token_logs error: {e}", exc_info=True)
            return 0

    async def clear_token_logs(
        self,
        before_days: int = None,
        bot_name: str = None,
        group_or_user_id: str = None,
        time_range: str = None,
    ) -> int:
        try:
            # 1. Clear token_usage matching filters
            conds_usage = []
            params_usage = []
            if before_days is not None:
                cutoff = (datetime.now() - timedelta(days=before_days)).strftime("%Y-%m-%d %H:%M:%S")
                conds_usage.append("created_at < ?")
                params_usage.append(cutoff)
            if bot_name:
                conds_usage.append("bot_name = ?")
                params_usage.append(bot_name)
            if group_or_user_id:
                conds_usage.append("group_or_user_id = ?")
                params_usage.append(group_or_user_id)
            if time_range:
                usage_thresh, _ = self._get_time_range_thresholds(time_range)
                if usage_thresh:
                    conds_usage.append("created_at >= ?")
                    params_usage.append(usage_thresh)

            where_usage = f"WHERE {' AND '.join(conds_usage)}" if conds_usage else ""
            sql_usage = f"DELETE FROM token_usage {where_usage}"
            cursor_usage = await self.conn.execute(sql_usage, params_usage)
            usage_deleted = cursor_usage.rowcount

            # 2. Clear token_daily_stats matching filters
            conds_daily = []
            params_daily = []
            if before_days is not None:
                cutoff_date = (datetime.now() - timedelta(days=before_days)).strftime("%Y-%m-%d")
                conds_daily.append("date < ?")
                params_daily.append(cutoff_date)
            if bot_name:
                conds_daily.append("bot_name = ?")
                params_daily.append(bot_name)
            if group_or_user_id:
                conds_daily.append("group_or_user_id = ?")
                params_daily.append(group_or_user_id)
            if time_range:
                _, daily_thresh = self._get_time_range_thresholds(time_range)
                if daily_thresh:
                    conds_daily.append("date >= ?")
                    params_daily.append(daily_thresh)

            where_daily = f"WHERE {' AND '.join(conds_daily)}" if conds_daily else ""
            sql_daily = f"DELETE FROM token_daily_stats {where_daily}"
            cursor_daily = await self.conn.execute(sql_daily, params_daily)
            daily_deleted = cursor_daily.rowcount

            await self.conn.commit()
            return usage_deleted + daily_deleted
        except Exception as e:
            logger.error(f"[Database] clear_token_logs error: {e}", exc_info=True)
            return 0
