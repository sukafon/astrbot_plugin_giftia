import time

from astrbot.core import AstrBotConfig

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
    ):
        self.config = config
        self.task_manager = task_manager
        self.db = db
        self.http_manager = http_manager
        self.register_funcs()
        self.task_manager.start()
        # 启动时添加备份任务
        if self.config.get("r2_config", {}).get("r2_enabled", False):
            self.add_backup_message_to_r2()

    def register_funcs(self):
        """注册定时任务函数"""
        self.task_manager.register_func(
            "backup_message_to_r2", self.backup_message_to_r2
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
