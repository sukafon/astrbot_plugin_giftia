import asyncio
import json
import time
from datetime import datetime, timedelta

from astrbot.api import logger
from astrbot.core import AstrBotConfig

from ..database.database import Database
from .http_manager import HttpManager
from .scheduler import Scheduler


class ToolsFunc:
    DEFAULT_AUTO_CLEAN_MEMORY_CONFIG = {
        "enabled": False,
        "max_importance": 3,
        "max_hit_count": 1,
        "min_age_days": 60,
        "last_hit_before_days": 30,
        "include_never_hit": True,
        "max_delete_per_run": 20,
        "cron": "30 3 * * *",
    }

    def __init__(
        self,
        config: AstrBotConfig,
        task_manager: Scheduler,
        db: Database,
        http_manager: HttpManager,
        data_cache=None,
    ):
        self.config = config
        self.task_manager = task_manager
        self.db = db
        self.http_manager = http_manager
        self.data_cache = data_cache
        self.register_funcs()
        self.task_manager.start()
        # 启动时添加备份任务
        if self.config.get("r2_config", {}).get("r2_enabled", False):
            self.add_backup_message_to_r2()
        # 启动时更新自动清理任务
        self.update_auto_clean_media_job()
        self.update_auto_clean_memory_job()

    def register_funcs(self):
        """注册定时任务函数"""
        self.task_manager.register_func(
            "backup_message_to_r2", self.backup_message_to_r2
        )
        self.task_manager.register_func(
            "auto_clean_media_cache", self.auto_clean_media_cache
        )
        self.task_manager.register_func(
            "auto_clean_memories", self.auto_clean_memories
        )

    def add_backup_message_to_r2(self):
        """添加备份消息到R2的定时任务"""
        # 先检查是否已经添加过该任务
        if self.task_manager.get_job_info("system_r2_backup_message"):
            return
        self.task_manager.add_job(
            task_id="system_r2_backup_message",
            func_name="backup_message_to_r2",
            time_expr=self.config.get("r2_config", {}).get("backup_time", "0 * * * *"),
        )

    async def backup_message_to_r2(self):
        """备份聊天记录到R2"""
        raw_time = await self.db.get_kv_data("last_backup_message_time", 0.0)
        last_backup_time = float(raw_time) if raw_time is not None else 0.0
        backup_path = await self.db.backup_chat_history_db(last_backup_time)
        if backup_path:
            success = await self.http_manager.upload_file(backup_path)
            if success:
                await self.db.upsert_kv_data("last_backup_message_time", time.time())

    def update_auto_clean_media_job(self):
        """添加/更新或移除自动清理媒体文件缓存的定时任务"""

        async def _update():
            raw_cfg = await self.db.get_kv_data("auto_clean_media_config")
            try:
                cfg = (
                    json.loads(raw_cfg)
                    if raw_cfg
                    else {"enabled": False, "keep_genres": ["表情包", "sticker"]}
                )
            except Exception:
                cfg = {"enabled": False, "keep_genres": ["表情包", "sticker"]}

            if cfg.get("enabled", False):
                self.task_manager.add_job(
                    task_id="system_auto_clean_media_cache",
                    func_name="auto_clean_media_cache",
                    time_expr="0 3 * * *",  # 每天凌晨 03:00
                )
            else:
                self.task_manager.remove_job("system_auto_clean_media_cache")

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(_update())
            else:
                asyncio.run(_update())
        except RuntimeError:
            asyncio.run(_update())

    @classmethod
    def normalize_auto_clean_memory_config(cls, raw_cfg=None) -> dict:
        """归一化长期记忆自动清理配置。"""
        if isinstance(raw_cfg, str):
            try:
                raw_cfg = json.loads(raw_cfg) if raw_cfg else {}
            except Exception:
                raw_cfg = {}
        if not isinstance(raw_cfg, dict):
            raw_cfg = {}

        cfg = dict(cls.DEFAULT_AUTO_CLEAN_MEMORY_CONFIG)
        cfg.update(raw_cfg)

        def clean_int(key: str, default: int, min_value: int, max_value: int | None = None):
            try:
                value = int(cfg.get(key, default))
            except (TypeError, ValueError):
                value = default
            value = max(min_value, value)
            if max_value is not None:
                value = min(max_value, value)
            cfg[key] = value

        def clean_bool(key: str, default: bool):
            value = cfg.get(key, default)
            if value is None:
                cfg[key] = default
                return
            if isinstance(value, bool):
                cfg[key] = value
                return
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"1", "true", "yes", "on"}:
                    cfg[key] = True
                    return
                if lowered in {"0", "false", "no", "off"}:
                    cfg[key] = False
                    return
            cfg[key] = bool(value)

        clean_bool("enabled", False)
        clean_bool("include_never_hit", True)
        clean_int("max_importance", 3, 1, 7)
        clean_int("max_hit_count", 1, 0, None)
        clean_int("min_age_days", 60, 7, None)
        clean_int("last_hit_before_days", 30, 7, None)
        clean_int("max_delete_per_run", 20, 1, 200)
        cron = str(cfg.get("cron") or "30 3 * * *").strip()
        cfg["cron"] = cron or "30 3 * * *"
        return cfg

    def update_auto_clean_memory_job(self):
        """添加/更新或移除自动清理长期记忆的定时任务"""

        async def _update():
            raw_cfg = await self.db.get_kv_data("auto_clean_memory_config")
            cfg = self.normalize_auto_clean_memory_config(raw_cfg)

            if cfg.get("enabled", False):
                self.task_manager.add_job(
                    task_id="system_auto_clean_memories",
                    func_name="auto_clean_memories",
                    time_expr=cfg.get("cron", "30 3 * * *"),
                )
            else:
                self.task_manager.remove_job("system_auto_clean_memories")

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(_update())
            else:
                asyncio.run(_update())
        except RuntimeError:
            asyncio.run(_update())

    def _build_auto_clean_memory_query(self, cfg: dict) -> tuple[str, list]:
        max_importance = min(int(cfg.get("max_importance", 3)), 7)
        max_hit_count = max(0, int(cfg.get("max_hit_count", 1)))
        min_age_days = max(7, int(cfg.get("min_age_days", 60)))
        last_hit_before_days = max(7, int(cfg.get("last_hit_before_days", 30)))
        include_never_hit = bool(cfg.get("include_never_hit", True))
        max_delete_per_run = max(1, min(200, int(cfg.get("max_delete_per_run", 20))))

        created_cutoff = (
            datetime.now() - timedelta(days=min_age_days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        hit_cutoff = (
            datetime.now() - timedelta(days=last_hit_before_days)
        ).strftime("%Y-%m-%d %H:%M:%S")

        conditions = [
            "COALESCE(importance, 5) <= ?",
            "COALESCE(hit_count, 0) <= ?",
            "datetime(created_at) <= datetime(?)",
        ]
        params = [max_importance, max_hit_count, created_cutoff]

        if include_never_hit:
            conditions.append(
                "((last_hit_at IS NULL OR last_hit_at = '') OR datetime(last_hit_at) <= datetime(?))"
            )
        else:
            conditions.append(
                "last_hit_at IS NOT NULL AND last_hit_at != '' AND datetime(last_hit_at) <= datetime(?)"
            )
        params.append(hit_cutoff)

        sql = f"""
            SELECT memory_id, bot_name, group_or_user_id, text, importance,
                   hit_count, last_hit_at, created_at
            FROM memories
            WHERE {' AND '.join(conditions)}
            ORDER BY COALESCE(importance, 5) ASC,
                     COALESCE(hit_count, 0) ASC,
                     datetime(created_at) ASC
            LIMIT ?
        """
        params.append(max_delete_per_run)
        return sql, params

    async def auto_clean_memories(self) -> dict:
        """自动清理低重要度、低活跃度、足够旧的长期记忆。"""
        logger.info("[Giftia] 开始自动清理长期记忆...")
        try:
            raw_cfg = await self.db.get_kv_data("auto_clean_memory_config")
            cfg = self.normalize_auto_clean_memory_config(raw_cfg)
            sql, params = self._build_auto_clean_memory_query(cfg)

            async with self.db.conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()

            candidates = [
                {
                    "memory_id": row["memory_id"],
                    "bot_name": row["bot_name"],
                    "group_or_user_id": row["group_or_user_id"],
                    "text": row["text"],
                    "importance": row["importance"],
                    "hit_count": row["hit_count"],
                    "last_hit_at": row["last_hit_at"],
                    "created_at": row["created_at"],
                }
                for row in rows
                if row["memory_id"]
            ]

            deleted_count = 0
            failed_ids = []
            if not self.data_cache:
                return {
                    "status": "error",
                    "message": "自动清理长期记忆失败: data_cache 未初始化",
                }

            for item in candidates:
                memory_id = item["memory_id"]
                try:
                    success = await self.data_cache.delete_memory(memory_id)
                except Exception as e:
                    logger.error(f"[Giftia] 自动清理长期记忆失败 {memory_id}: {e}")
                    success = False

                if success:
                    deleted_count += 1
                else:
                    failed_ids.append(memory_id)

            msg = f"自动清理完成，共删除 {deleted_count} 条长期记忆"
            if failed_ids:
                msg += f"，{len(failed_ids)} 条删除失败"
            logger.info(f"[Giftia] {msg}")
            return {
                "status": "success",
                "count": deleted_count,
                "deleted_count": deleted_count,
                "failed_ids": failed_ids,
                "candidates": candidates,
                "message": msg,
            }
        except Exception as e:
            logger.error(f"[Giftia] 自动清理长期记忆失败: {e}")
            return {"status": "error", "message": f"自动清理长期记忆失败: {str(e)}"}

    async def auto_clean_media_cache(self) -> dict:
        """自动清理非表情包类型的超出会话窗口的媒体文件"""
        logger.info("[Giftia] 开始自动清理媒体缓存文件...")
        try:
            from astrbot.core.star.star_tools import StarTools

            # 1. 读取自动清理配置
            raw_cfg = await self.db.get_kv_data("auto_clean_media_config")
            try:
                cfg = (
                    json.loads(raw_cfg)
                    if raw_cfg
                    else {"enabled": False, "keep_genres": ["表情包", "sticker"]}
                )
            except Exception:
                cfg = {"enabled": False, "keep_genres": ["表情包", "sticker"]}

            keep_genres = cfg.get("keep_genres", ["表情包", "sticker"])

            # 2. 查询所有应保留的媒体哈希
            # A. 自定义表情包（stickers 表）
            async with self.db.conn.execute(
                "SELECT sticker_id FROM stickers"
            ) as cursor:
                rows = await cursor.fetchall()
                sticker_hashes = {r["sticker_id"] for r in rows if r["sticker_id"]}

            # B. 媒体转述中属于保留风格类型的哈希
            caption_keep_hashes = set()
            if keep_genres:
                has_unspecified = "" in keep_genres
                specified_genres = [g for g in keep_genres if g != ""]

                conditions = []
                params = []
                if specified_genres:
                    placeholders = ",".join(["?"] * len(specified_genres))
                    if has_unspecified:
                        conditions.append(
                            f"(genre IN ({placeholders}) OR genre IS NULL OR genre = '')"
                        )
                    else:
                        conditions.append(f"genre IN ({placeholders})")
                    params.extend(specified_genres)
                elif has_unspecified:
                    conditions.append("(genre IS NULL OR genre = '')")

                if conditions:
                    conditions.append("caption IS NOT NULL AND caption != ''")
                    sql = f"SELECT hash_val FROM media_caption WHERE {' AND '.join(conditions)}"
                    async with self.db.conn.execute(sql, params) as cursor:
                        rows = await cursor.fetchall()
                        caption_keep_hashes = {
                            r["hash_val"] for r in rows if r["hash_val"]
                        }

            # C. 活跃会话消息引用的媒体哈希
            active_media_hashes = set()
            # 获取所有会话列表
            async with self.db.conn.execute(
                "SELECT DISTINCT bot_name, group_or_user_id FROM chat_history"
            ) as cursor:
                sessions = await cursor.fetchall()

            msg_number = min(
                self.config.get("message_history", {}).get("msg_number", 300), 100
            )

            for session in sessions:
                bot_name = session["bot_name"]
                group_or_user_id = session["group_or_user_id"]
                if not bot_name or not group_or_user_id:
                    continue
                async with self.db.conn.execute(
                    "SELECT media_ids FROM chat_history WHERE bot_name = ? AND group_or_user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (bot_name, group_or_user_id, msg_number),
                ) as cursor:
                    history_rows = await cursor.fetchall()
                    for hr in history_rows:
                        if hr["media_ids"]:
                            try:
                                h_list = json.loads(hr["media_ids"])
                                if isinstance(h_list, list):
                                    active_media_hashes.update(h_list)
                            except Exception:
                                pass

            # D. 查询所有已经有成功转述（caption 非空）的媒体哈希，为了做时间窗口和清理判定
            async with self.db.conn.execute(
                "SELECT hash_val FROM media_caption WHERE caption IS NOT NULL AND caption != ''"
            ) as cursor:
                rows = await cursor.fetchall()
                all_transcribed_hashes = {r["hash_val"] for r in rows if r["hash_val"]}

            # 合并所有保留哈希集
            retain_hashes = sticker_hashes | caption_keep_hashes | active_media_hashes

            # 3. 遍历媒体文件物理缓存执行清理
            cache_dir = StarTools.get_data_dir("astrbot_plugin_giftia") / "media_cache"
            cleaned_count = 0
            freed_bytes = 0

            if cache_dir.exists():
                # 安全时间窗口：保留最近 24 小时修改过的任何已转述文件，未转述仅保留 1 小时
                now = time.time()
                twenty_four_hours_ago = now - 24 * 3600
                one_hour_ago = now - 3600

                for cache_file in cache_dir.iterdir():
                    # 跳过子目录（如 thumbnails）
                    if cache_file.is_dir():
                        continue

                    hash_val = cache_file.name
                    if hash_val not in retain_hashes:
                        try:
                            # 检查文件修改时间
                            mtime = cache_file.stat().st_mtime
                            is_transcribed = hash_val in all_transcribed_hashes
                            safety_limit = (
                                twenty_four_hours_ago
                                if is_transcribed
                                else one_hour_ago
                            )

                            if mtime < safety_limit:
                                file_size = cache_file.stat().st_size
                                cache_file.unlink()
                                cleaned_count += 1
                                freed_bytes += file_size

                                # 删除对应的缩略图
                                thumb_file = cache_dir / "thumbnails" / hash_val
                                if thumb_file.exists():
                                    thumb_file.unlink()

                                # 如果是无有效转述的死媒体记录，同步清理数据库行与内存 LRUCache
                                if not is_transcribed:
                                    await self.db.conn.execute(
                                        "DELETE FROM media_caption WHERE hash_val = ?",
                                        (hash_val,),
                                    )
                                    await self.db.conn.commit()
                                    if self.data_cache:
                                        self.data_cache.caption.pop(hash_val, None)
                        except Exception as e:
                            logger.error(
                                f"[Giftia] 自动清理缓存文件失败 {hash_val}: {e}"
                            )

            msg = f"自动清理完成，共物理删除 {cleaned_count} 个过期媒体文件，释放空间 {freed_bytes} 字节"
            logger.info(f"[Giftia] {msg}")
            return {
                "status": "success",
                "count": cleaned_count,
                "size_bytes": freed_bytes,
                "message": msg,
            }
        except Exception as e:
            logger.error(f"[Giftia] 自动清理媒体缓存失败: {e}")
            return {"status": "error", "message": f"自动清理媒体缓存失败: {str(e)}"}
