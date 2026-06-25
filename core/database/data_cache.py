import asyncio
import json
import time
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import datetime

from cachetools import LRUCache

from astrbot.api import logger

from .database import Database
from ..utils.http_manager import HttpManager
from ..memory.memory import LTM
from ..utils.schemas import MediaCaption, MemoryItem, MessageData, Status

MAX_CAPTION_CACHE_SIZE = 500


class DataCache:
    """数据缓存"""

    # 键是文件二进制数据的xxhash，而不是URL的xxhash
    caption: LRUCache[str, MediaCaption]  # xxhash:caption
    # filename:xxhash，需要一个文件名到hash的映射
    filename_to_hash: LRUCache[str, str]
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
        ltm: LTM,
        memory_number: int = 20,
        msg_number: int = 50,
        energy_recovery_interval: int = 90,
    ):
        self.db = db
        self.http_manager = http_manager
        self.ltm = ltm
        self.memory_number = memory_number
        self.msg_number = msg_number
        self.energy_recovery_interval = energy_recovery_interval
        self.caption = LRUCache(maxsize=MAX_CAPTION_CACHE_SIZE)
        self.filename_to_hash = LRUCache(maxsize=MAX_CAPTION_CACHE_SIZE)
        # 用户画像缓存
        self.user_profiles: dict[str, str] = {}
        # 群画像缓存
        self.group_profiles: dict[str, str] = {}
        self.bot_status: dict[str, Status] = {}

        # 关系缓存
        self.relations: dict[
            str, tuple[int, str]
        ] = {}  # bot_name:group_or_user_id:user_id -> (relation, title)

        # 使用 defaultdict 自动管理每个会话的 deque
        # lambda 确保每个新 key 都会得到一个指定长度的 deque
        self.recent_messages: defaultdict[str, deque[MessageData]] = defaultdict(
            lambda: deque(maxlen=self.msg_number)
        )
        # 记忆缓存
        self.memories: defaultdict[str, deque[MemoryItem]] = defaultdict(
            lambda: deque(maxlen=self.memory_number)
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

        if not status:
            status = await self.db.get_bot_status(
                bot_name=bot_name, group_or_user_id=group_id
            )
            self.bot_status[fmt_key] = status

        # 当前时间
        current_time = time.time()

        if status.timestamp > 0:
            # 恢复的能量
            recovered_energy = (
                current_time - status.timestamp
            ) / self.energy_recovery_interval
            clean_energy = status.energy.strip().strip('"').strip("'")
            try:
                status.energy = str(min(float(clean_energy) + recovered_energy, 100.0))
            except Exception as e:
                logger.error(f"能量数据异常：{e}，自动重置为100")
                status.energy = "100.0"

        status.timestamp = current_time
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

    async def delete_message(
        self, bot_name: str, group_or_user_id: str, message_id: str
    ) -> None:
        """删除消息"""
        fmt_key = f"{bot_name}:{group_or_user_id}"
        messages = self.recent_messages.get(fmt_key)
        if messages:
            for msg in messages:
                if msg.message_id == message_id:
                    messages.remove(msg)
                    break
        await self.db.delete_message(
            bot_name=bot_name, group_or_user_id=group_or_user_id, message_id=message_id
        )

    async def set_bot_status(
        self, bot_name: str, group_id: str, status: Status
    ) -> None:
        current_status = await self.get_bot_status(bot_name, group_id)
        # 使用 asdict 将 dataclass 转换为字典，即便开启了 slots 也能正常工作
        status_data = asdict(status)
        # 仅对非空值进行差分覆盖
        for key, value in status_data.items():
            if key != "timestamp" and value is not None and value != "":
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

    async def get_caption_by_filename(
        self, filename: str
    ) -> tuple[str | None, MediaCaption | None]:
        if not filename or is_temp_or_local_path(filename):
            return None, None
        hash_val = self.filename_to_hash.get(filename)
        if hash_val and self.caption.get(hash_val):
            return hash_val, self.caption[hash_val]
        # 从数据库中获取
        media_caption = await self.db.get_media_caption_by_filename(filename)
        if media_caption:
            await self.set_caption(media_caption, False)
            return media_caption.hash_val, media_caption
        return None, None

    async def set_caption(self, caption: MediaCaption, is_new: bool = True) -> None:
        self.caption[caption.hash_val] = caption
        if caption.file_name and not is_temp_or_local_path(caption.file_name):
            self.filename_to_hash[caption.file_name] = caption.hash_val
        if is_new:
            await self.db.insert_media_caption(media_caption=caption)

    async def update_caption(self, caption: MediaCaption) -> None:
        self.caption[caption.hash_val] = caption
        if caption.file_name and not is_temp_or_local_path(caption.file_name):
            self.filename_to_hash[caption.file_name] = caption.hash_val
        await self.db.update_media_caption(media_caption=caption)

    async def clear_caption(self):
        self.caption.clear()
        self.filename_to_hash.clear()
        await self.db.clear_media_caption()

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

    async def update_relation(
        self, bot_name: str, group_or_user_id: str, user_id: str, relation: int
    ) -> None:
        """更新关系"""
        fmt_key = f"{bot_name}:{group_or_user_id}:{user_id}"
        current_relation = self.relations.get(fmt_key, (0, ""))[0]
        new_relation = current_relation + relation
        self.relations[fmt_key] = (
            new_relation,
            self.relations.get(fmt_key, (0, ""))[1],
        )
        await self.db.upsert_relation(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            user_id=user_id,
            relation=new_relation,
        )

    async def set_relation_title(
        self, bot_name: str, group_or_user_id: str, user_id: str, title: str
    ) -> None:
        """设置关系头衔"""
        fmt_key = f"{bot_name}:{group_or_user_id}:{user_id}"
        current_relation = self.relations.get(fmt_key, (0, ""))[0]
        self.relations[fmt_key] = (current_relation, title)
        await self.db.upsert_relation_title(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            user_id=user_id,
            title=title,
        )

    async def get_user_relation(
        self, bot_name: str, group_or_user_id: str, user_id: str
    ) -> tuple[int, str]:
        """获取用户关系"""
        fmt_key = f"{bot_name}:{group_or_user_id}:{user_id}"
        relation = self.relations.get(fmt_key)
        if relation:
            return relation
        # 从数据库中获取
        relation_data = await self.db.get_relation(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            user_id=user_id,
        )
        if relation_data:
            self.relations[fmt_key] = relation_data
            return relation_data
        return 0, ""

    async def get_memories(
        self, bot_name: str, group_or_user_id: str, limit: int = 20
    ) -> list[MemoryItem]:
        """获取记忆"""
        fmt_key = f"{bot_name}:{group_or_user_id}"
        memories = self.memories.get(fmt_key)
        if memories and len(memories) >= limit:
            return list(memories)[-limit:]
        # 从数据库中获取
        db_memories = await self.db.get_memories(
            group_or_user_id=group_or_user_id, bot_name=bot_name, limit=limit
        )
        # 缓存最新的一批
        self.memories[fmt_key] = deque(db_memories, maxlen=limit)
        return db_memories

    async def add_memory(
        self, bot_name: str, group_or_user_id: str, text: str, user_id: str, associated_user_ids: list[str] = None
    ) -> str | None:
        """添加记忆"""
        fmt_key = f"{bot_name}:{group_or_user_id}"
        now = datetime.now().isoformat()
        meta_dict = {"user_id": user_id}
        if associated_user_ids:
            meta_dict["associated_user_ids"] = associated_user_ids
        meta_str = json.dumps(meta_dict)
        
        result = await self.ltm.add_memory(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            text=text,
            time=now,
            metadata=meta_str,
        )
        if result is None:
            return
        memory_id, vector = result
        memory = MemoryItem(
            memory_id=memory_id,
            text=text,
            vector=vector,
            metadata=meta_str,
            updated_at=now,
            created_at=now,
        )
        self.memories[fmt_key].append(memory)
        # 将记忆写入数据库
        await self.db.insert_memory(
            bot_name=bot_name, group_or_user_id=group_or_user_id, memory=memory
        )
        return memory_id

    async def delete_memory(self, memory_id: str) -> bool:
        """删除记忆"""
        result = await self.ltm.delete_memory(memory_id=memory_id)
        if result is None:
            return False
        await self.db.delete_memory(memory_id=memory_id)
        # 从缓存中删除
        for memories in self.memories.values():
            for memory in memories:
                if memory.memory_id == memory_id:
                    memories.remove(memory)
                    break
        return True

    async def delete_all_memories(self, bot_name: str, group_or_user_id: str):
        """删除全部记忆"""
        fmt_key = f"{bot_name}:{group_or_user_id}"
        targets = [
            self.group_profiles,
            self.memories,
            self.bot_status,
            self.recent_messages,
        ]
        for cache in targets:
            cache.pop(fmt_key, None)

        prefix = f"{fmt_key}:"
        for cache_dict in [self.user_profiles, self.relations]:
            keys_to_delete = [k for k in cache_dict.keys() if k.startswith(prefix)]
            for k in keys_to_delete:
                cache_dict.pop(k, None)

        await asyncio.gather(
            self.db.delete_group_user_profiles(
                bot_name=bot_name, group_or_user_id=group_or_user_id
            ),
            self.db.delete_group_profile(
                bot_name=bot_name, group_or_user_id=group_or_user_id
            ),
            self.ltm.delete_all_memories(
                bot_name=bot_name, group_or_user_id=group_or_user_id
            ),
            self.db.delete_all_memories(
                bot_name=bot_name, group_or_user_id=group_or_user_id
            ),
            self.db.delete_bot_status(
                group_or_user_id=group_or_user_id, bot_name=bot_name
            ),
            self.db.delete_all_relations(
                bot_name=bot_name, group_or_user_id=group_or_user_id
            ),
            self.db.delete_chat_history(
                group_or_user_id=group_or_user_id, bot_name=bot_name
            ),
            return_exceptions=True,
        )


def is_temp_or_local_path(s: str | None) -> bool:
    """Determines whether a file path or filename is temporary or local.

    Args:
        s: The file path or filename to check.

    Returns:
        True if the path or filename points to a local file or temporary pattern,
        False otherwise.
    """
    if not s:
        return False
    if s.startswith(("http://", "https://")):
        return False
    if s.startswith("file://") or any(
        marker in s
        for marker in [
            "media_image_",
            "media_audio_",
            "media_file_",
            "io_temp_img_",
            "compressed_",
        ]
    ):
        return True
    import os

    try:
        if os.path.isabs(s) or os.path.exists(s):
            return True
    except Exception:
        pass
    return False
