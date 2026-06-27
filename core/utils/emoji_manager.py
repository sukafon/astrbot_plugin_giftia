import asyncio
import random
import time
from io import BytesIO
from pathlib import Path

from PIL import Image

from astrbot.api import logger
from astrbot.api.star import StarTools

from ..database.database import Database
from .schemas import BotSticker, Sticker


class EmojiManager:
    def __init__(self, db: Database, random_sticker_count: int = 50):
        self.db = db
        self.random_sticker_count = random_sticker_count

        # 表情包缓存，键是sticker_id，值是Sticker
        self.stickers: dict[str, Sticker] = {}
        self._stickers_loaded: bool = False
        # 机器人的表情包列表缓存，键是bot_name，值是BotSticker
        self.bot_stickers: dict[str, BotSticker] = {}
        # 表情包路径
        self.stickers_dir = StarTools.get_data_dir("astrbot_plugin_giftia") / "stickers"
        self.stickers_dir.mkdir(parents=True, exist_ok=True)

    async def save_sticker_image(self, image_bytes: bytes, sticker_id: str) -> Path:
        """
        处理并保存表情包图片到本地数据目录。
        返回保存后的本地绝对路径。
        """

        def _process_and_save() -> Path:
            ext = ""
            final_bytes = image_bytes

            if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                ext = ".png"
            elif image_bytes.startswith(b"\xff\xd8\xff"):
                ext = ".jpg"
            elif image_bytes.startswith(b"GIF8"):
                ext = ".gif"
            elif image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
                ext = ".webp"
            else:
                try:
                    with Image.open(BytesIO(image_bytes)) as img:
                        img_converted = img.convert("RGB")
                        buf = BytesIO()
                        img_converted.save(buf, format="JPEG", quality=90)
                        final_bytes = buf.getvalue()
                    ext = ".jpg"
                except Exception as e:
                    logger.warning(f"表情包图片格式转换失败，强制存为.jpg: {e}")
                    final_bytes = image_bytes
                    ext = ".jpg"

            local_path = self.stickers_dir / f"{sticker_id}{ext}"
            with open(local_path, "wb") as f:
                f.write(final_bytes)

            return local_path

        return await asyncio.to_thread(_process_and_save)

    def get_sticker_path(self, sticker_id: str) -> Path | None:
        """获取表情包文件的绝对路径"""
        sticker = self.stickers.get(sticker_id)
        if sticker and sticker.filename:
            local_path = self.stickers_dir / sticker.filename
            if local_path.exists():
                return local_path
        return None

    async def add_sticker(
        self, bot_name: str, media_id: str, sticker: Sticker | None = None
    ) -> None:
        """添加表情包"""
        if media_id not in self.stickers:
            if not sticker:
                return
            await self.db.insert_sticker(
                sticker_id=sticker.sticker_id,
                name=sticker.name,
                category=sticker.category,
                tags=sticker.tags,
                description=sticker.description,
                filename=sticker.filename,
            )
            self.stickers[media_id] = sticker

        await self.db.insert_sticker_bot(sticker_id=media_id, bot_name=bot_name)

        bot_sticker = await self.get_sticker(bot_name)
        if media_id not in bot_sticker.sticker_set:
            bot_sticker.sticker_list.append(media_id)
            bot_sticker.sticker_set.add(media_id)
            bot_sticker.timestamp = time.time()

    async def get_sticker(self, bot_name: str) -> BotSticker:
        """获取该机器人的所有表情包"""
        if not self._stickers_loaded:
            all_stickers = await self.db.get_sticker()
            for s in all_stickers:
                self.stickers[s.sticker_id] = s
            self._stickers_loaded = True

        sticker_data = self.bot_stickers.get(bot_name)
        if sticker_data:
            return sticker_data

        # 从数据库中获取
        sticker_ids = await self.db.get_sticker_bot(bot_name)
        bot_sticker = BotSticker(
            timestamp=time.time(),
            sticker_list=sticker_ids,
            sticker_set=set(sticker_ids),
        )
        self.bot_stickers[bot_name] = bot_sticker
        return bot_sticker

    async def has_sticker(self, bot_name: str, media_id: str) -> bool:
        """快速判断机器人是否已经收集了该表情包"""
        bot_sticker = await self.get_sticker(bot_name)
        return media_id in bot_sticker.sticker_set

    async def get_random_stickers(self, bot_name: str) -> str:
        """动态获取随机的表情包列表，格式化为易读的字符串"""
        bot_sticker = await self.get_sticker(bot_name)
        if not bot_sticker.sticker_list:
            return ""

        sampled_ids = random.sample(
            bot_sticker.sticker_list,
            min(len(bot_sticker.sticker_list), self.random_sticker_count),
        )

        result_lines = []
        for sid in sampled_ids:
            s = self.stickers.get(sid)
            if s:
                tags_str = ", ".join(s.tags) if s.tags else "无"
                line = f"[{s.name}](sticker_id: {s.sticker_id}) - 分类: {s.category}, 标签: {tags_str}"
                result_lines.append(line)

        return "\n".join(result_lines)
