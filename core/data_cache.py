import time
from collections import defaultdict, deque
from dataclasses import asdict

from cachetools import LRUCache

from .database import Database
from .http_manager import HttpManager
from .schemas import MediaCaption, MessageData, Status

MAX_CAPTION_CACHE_SIZE = 1000


class DataCache:
    """数据缓存"""

    # 键是文件二进制数据的xxhash，而不是URL的xxhash
    caption: LRUCache[str, MediaCaption]  # xxhash:caption
    # url:xxhash，由于hash统一为文件二进制数据的hash，所以需要一个url到hash的映射
    url_to_hash: LRUCache[str, str]
    # 聊天记录缓存
    recent_messages: defaultdict[
        str, deque[MessageData]
    ]  # bot_name:group_id:MessageData
    # 机器人状态缓存
    bot_status: dict[str, Status]  # bot_name:group_id:status

    def __init__(
        self,
        db: Database,
        http_manager: HttpManager,
        msg_number=50,
        energy_recovery_interval=90,
    ):
        self.db = db
        self.msg_number = msg_number
        self.http_manager = http_manager
        self.caption = LRUCache(maxsize=MAX_CAPTION_CACHE_SIZE)
        self.url_to_hash = LRUCache(maxsize=MAX_CAPTION_CACHE_SIZE)
        # 用户画像缓存
        self.user_profiles: dict[str, str] = {}
        # 群画像缓存
        self.group_profiles: dict[str, str] = {}
        self.bot_status: dict[str, Status] = {}
        self.energy_recovery_interval = energy_recovery_interval

        # 使用 defaultdict 自动管理每个会话的 deque
        # lambda 确保每个新 key 都会得到一个指定长度的 deque
        self.recent_messages: defaultdict[str, deque[MessageData]] = defaultdict(
            lambda: deque(maxlen=self.msg_number)
        )

    async def get_recent_message(
        self, bot_name: str, group_id: str, limit: int = 50
    ) -> list[MessageData]:
        """获取近期聊天记录"""
        fmt_key = f"{bot_name}:{group_id}"
        messages = self.recent_messages.get(fmt_key)
        # 只有在缓存满足需求量时，才直接使用缓存
        if messages and (len(messages) >= limit or len(messages) == self.msg_number):
            return list(messages)[-limit:]
        # 否则从数据库中获取
        fetch_limit = max(limit, self.msg_number)
        db_messages = await self.db.get_messages(
            group_or_user_id=group_id, bot_name=bot_name, limit=fetch_limit
        )
        # 缓存最新的一批
        self.recent_messages[fmt_key] = deque(db_messages, maxlen=self.msg_number)
        return db_messages[-limit:]

    async def get_message_by_id(
        self, bot_name: str, group_id: str, message_id: str
    ) -> MessageData | None:
        """通过消息ID获取消息"""
        fmt_key = f"{bot_name}:{group_id}"
        messages = self.recent_messages.get(fmt_key)
        if messages:
            for msg in messages:
                if msg.message_id == message_id:
                    return msg
        return await self.db.get_message_by_id(
            group_or_user_id=group_id, bot_name=bot_name, message_id=message_id
        )

    async def add_message(
        self, bot_name: str, group_id: str, msg_data: MessageData
    ) -> None:
        """添加消息，先加入缓存，再写入数据库"""
        fmt_key = f"{bot_name}:{group_id}"
        self.recent_messages[fmt_key].append(msg_data)
        # 将消息写入数据库
        await self.db.insert_message(bot_name=bot_name, message=msg_data)

    async def add_cache_message(
        self, bot_name: str, group_id: str, msg_data: MessageData
    ) -> None:
        """添加消息到缓存，不持久化，如点赞失败、撤回、戳一戳等操作，防止AI重复触发"""
        fmt_key = f"{bot_name}:{group_id}"
        self.recent_messages[fmt_key].append(msg_data)

    async def get_bot_status(self, bot_name: str, group_id: str) -> Status:
        fmt_key = f"{bot_name}:{group_id}"
        status = self.bot_status.get(fmt_key)
        if status:
            # 当前时间
            current_time = time.time()
            # 恢复的能量
            recovered_energy = (
                current_time - status.timestamp
            ) / self.energy_recovery_interval
            status.energy = str(min(float(status.energy) + recovered_energy, 100.0))
            status.timestamp = current_time
            return status
        status = await self.db.get_bot_status(
            bot_name=bot_name, group_or_user_id=group_id
        )
        status.timestamp = time.time()
        self.bot_status[fmt_key] = status
        return status

    async def set_message_recalled(
        self, bot_name: str, group_or_user_id: str, message_ids: list[str]
    ) -> None:
        """将消息标记为撤回"""
        fmt_key = f"{bot_name}:{group_or_user_id}"
        messages = self.recent_messages.get(fmt_key)
        if messages:
            for msg in messages:
                if msg.message_id in message_ids:
                    msg.is_recalled = 1
        await self.db.update_message_recall(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            message_ids=message_ids,
            is_recalled=1,
        )

    async def set_bot_status(
        self, bot_name: str, group_id: str, status: Status
    ) -> None:
        current_status = await self.get_bot_status(bot_name, group_id)
        # 使用 asdict 将 dataclass 转换为字典，即便开启了 slots 也能正常工作
        status_data = asdict(status)
        # 仅对非空值进行差分覆盖
        for key, value in status_data.items():
            if value is not None and value != "":
                setattr(current_status, key, value)
        await self.db.upsert_bot_status(
            bot_name=bot_name,
            group_or_user_id=group_id,
            status=current_status,
        )

    async def get_caption_by_hash(self, hash_val: str) -> MediaCaption | None:
        media_caption = self.caption.get(hash_val)
        if media_caption:
            return media_caption
        # 从数据库中获取
        media_caption = await self.db.get_media_caption_by_hash(hash_val)
        if media_caption:
            await self.set_caption(media_caption, False)
            return media_caption
        return None

    async def get_caption_by_url(
        self, url: str
    ) -> tuple[str | None, MediaCaption | None]:
        hash_val = self.url_to_hash.get(url)
        if hash_val and self.caption.get(hash_val):
            return hash_val, self.caption[hash_val]
        # 从数据库中获取
        media_caption = await self.db.get_media_caption_by_url(url)
        if media_caption:
            await self.set_caption(media_caption)
            return media_caption.hash_val, media_caption
        return None, None

    async def set_caption(self, caption: MediaCaption, is_new: bool = True) -> None:
        self.caption[caption.hash_val] = caption
        self.url_to_hash[caption.url] = caption.hash_val
        if is_new:
            await self.db.insert_media_caption(media_caption=caption)

    async def get_user_profile(
        self, bot_name: str, group_or_user_id: str, user_id: str
    ) -> str | None:
        """获取用户画像"""
        fmt_key = f"{bot_name}:{group_or_user_id}:{user_id}"
        profile = self.user_profiles.get(fmt_key)
        if profile:
            return profile
        # 从数据库中获取
        profile_data = await self.db.get_user_profile(
            user_id=user_id,
            group_or_user_id=group_or_user_id,
            bot_name=bot_name,
        )
        if profile_data:
            self.user_profiles[fmt_key] = profile_data
            return profile_data
        return None

    async def set_user_profile(
        self, bot_name: str, group_or_user_id: str, user_id: str, profile: str
    ) -> None:
        """设置用户画像"""
        fmt_key = f"{bot_name}:{group_or_user_id}:{user_id}"
        self.user_profiles[fmt_key] = profile
        await self.db.upsert_user_profile(
            user_id=user_id,
            group_or_user_id=group_or_user_id,
            bot_name=bot_name,
            profile=profile,
        )

    async def get_group_profile(
        self, bot_name: str, group_or_user_id: str
    ) -> str | None:
        """获取群画像"""
        fmt_key = f"{bot_name}:{group_or_user_id}"
        profile = self.group_profiles.get(fmt_key)
        if profile:
            return profile
        # 从数据库中获取
        profile_data = await self.db.get_group_profile(
            group_or_user_id=group_or_user_id, bot_name=bot_name
        )
        if profile_data:
            self.group_profiles[fmt_key] = profile_data
            return profile_data
        return None

    async def set_group_profile(
        self, bot_name: str, group_or_user_id: str, profile: str
    ) -> None:
        """设置群画像"""
        fmt_key = f"{bot_name}:{group_or_user_id}"
        self.group_profiles[fmt_key] = profile
        await self.db.upsert_group_profile(
            group_or_user_id=group_or_user_id,
            bot_name=bot_name,
            profile=profile,
        )
