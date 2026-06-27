import time
import json
import asyncio

from astrbot.core import AstrBotConfig
from astrbot.api import logger

from ..database.database import Database
from .http_manager import HttpManager
from .scheduler import Scheduler


class ToolsFunc:
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

    def register_funcs(self):
        """注册定时任务函数"""
        self.task_manager.register_func(
            "backup_message_to_r2", self.backup_message_to_r2
        )
        self.task_manager.register_func(
            "auto_clean_media_cache", self.auto_clean_media_cache
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
                cfg = json.loads(raw_cfg) if raw_cfg else {"enabled": False, "keep_genres": ["表情包", "sticker"]}
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

    async def auto_clean_media_cache(self) -> dict:
        """自动清理非表情包类型的超出会话窗口的媒体文件"""
        logger.info("[Giftia] 开始自动清理媒体缓存文件...")
        try:
            from astrbot.core.star.star_tools import StarTools
            
            # 1. 读取自动清理配置
            raw_cfg = await self.db.get_kv_data("auto_clean_media_config")
            try:
                cfg = json.loads(raw_cfg) if raw_cfg else {"enabled": False, "keep_genres": ["表情包", "sticker"]}
            except Exception:
                cfg = {"enabled": False, "keep_genres": ["表情包", "sticker"]}
                
            keep_genres = cfg.get("keep_genres", ["表情包", "sticker"])
            
            # 2. 查询所有应保留的媒体哈希
            # A. 自定义表情包（stickers 表）
            async with self.db.conn.execute("SELECT sticker_id FROM stickers") as cursor:
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
                        conditions.append(f"(genre IN ({placeholders}) OR genre IS NULL OR genre = '')")
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
                        caption_keep_hashes = {r["hash_val"] for r in rows if r["hash_val"]}

            # C. 活跃会话消息引用的媒体哈希
            active_media_hashes = set()
            # 获取所有会话列表
            async with self.db.conn.execute(
                "SELECT DISTINCT bot_name, group_or_user_id FROM chat_history"
            ) as cursor:
                sessions = await cursor.fetchall()
                
            msg_number = min(self.config.get("message_history", {}).get("msg_number", 300), 100)
            
            for session in sessions:
                bot_name = session["bot_name"]
                group_or_user_id = session["group_or_user_id"]
                if not bot_name or not group_or_user_id:
                    continue
                async with self.db.conn.execute(
                    "SELECT media_ids FROM chat_history WHERE bot_name = ? AND group_or_user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (bot_name, group_or_user_id, msg_number)
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
                            safety_limit = twenty_four_hours_ago if is_transcribed else one_hour_ago
                            
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
                                    await self.db.conn.execute("DELETE FROM media_caption WHERE hash_val = ?", (hash_val,))
                                    await self.db.conn.commit()
                                    if self.data_cache:
                                        self.data_cache.caption.pop(hash_val, None)
                        except Exception as e:
                            logger.error(f"[Giftia] 自动清理缓存文件失败 {hash_val}: {e}")
                            
            msg = f"自动清理完成，共物理删除 {cleaned_count} 个过期媒体文件，释放空间 {freed_bytes} 字节"
            logger.info(f"[Giftia] {msg}")
            return {
                "status": "success",
                "count": cleaned_count,
                "size_bytes": freed_bytes,
                "message": msg
            }
        except Exception as e:
            logger.error(f"[Giftia] 自动清理媒体缓存失败: {e}")
            return {
                "status": "error",
                "message": f"自动清理媒体缓存失败: {str(e)}"
            }
