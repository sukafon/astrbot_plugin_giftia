import random

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At, File, Image, Plain, Record, Video
from astrbot.core.message.components import BaseMessageComponent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)


class AIoCQHTTPAction:
    """
    这个类用于处理AIOCQHTTP的互动
    """

    def __init__(self, sticker_summaries: list[str] | None = None):
        self.sticker_summaries = sticker_summaries or ["图片"]

    async def send_message(
        self,
        event: AstrMessageEvent,
        message_chain: list[BaseMessageComponent],
    ) -> tuple[bool, int | None]:
        """发送消息
        Args:
            event: 消息事件
            message_chain: 消息链
        """

        if event.get_platform_name() == "aiocqhttp" and isinstance(
            event, AiocqhttpMessageEvent
        ):
            try:
                message_data = await self._msg_chain_to_data(message_chain)
                group_id = event.get_group_id()
                if group_id:
                    resp = await event.bot.send_group_msg(
                        group_id=int(group_id), message=message_data
                    )
                else:
                    resp = await event.bot.send_private_msg(
                        user_id=int(event.get_sender_id()), message=message_data
                    )
                if resp and isinstance(resp, dict) and resp.get("message_id"):
                    message_id = resp["message_id"]
                    return True, message_id
                else:
                    logger.error(f"[Giftia] 发送消息失败: {resp}")
                    return True, None
            except Exception as e:
                logger.error(f"[Giftia] 发送消息失败: {e}")
                return False, None
        else:
            logger.warning("[Giftia] 发送消息失败: 当前仅支持aiocqhttp平台")
            return False, None

    async def delete_messages(
        self, event: AstrMessageEvent, message_ids: list[int]
    ) -> str | None:
        """撤回消息"""
        if event.get_platform_name() == "aiocqhttp" and isinstance(
            event, AiocqhttpMessageEvent
        ):
            try:
                for message_id in message_ids:
                    await event.bot.delete_msg(message_id=message_id)
                return None
            except Exception as e:
                logger.warning(f"撤回消息失败: {str(e)}")
                return str(e)
        else:
            logger.warning("[Giftia] 撤回消息失败: 当前仅支持aiocqhttp平台")
            return "当前仅支持aiocqhttp平台"

    async def like(
        self, event: AstrMessageEvent, user_id: int, count: int
    ) -> str | None:
        """点赞"""
        if event.get_platform_name() == "aiocqhttp" and isinstance(
            event, AiocqhttpMessageEvent
        ):
            # 超过50个赞截断成50个
            total_likes = min(count, 50)
            # 计算分组
            full_groups = total_likes // 10
            remainder = total_likes % 10
            batches = [10] * full_groups
            if remainder > 0:
                batches.append(remainder)
            for index, count in enumerate(batches):
                try:
                    await event.bot.send_like(user_id=user_id, times=count)
                except Exception as e:
                    logger.warning(f"点赞失败: {str(e)}")
                    # 如果是第一次点赞失败，返回错误信息
                    if index == 0:
                        return str(e)
                    return None
            return None
        else:
            logger.warning("[Giftia] 点赞失败: 仅支持aiocqhttp平台")
            return "当前仅支持aiocqhttp平台"

    async def group_kick(
        self,
        event: AstrMessageEvent,
        group_id: int,
        user_id: int,
        reject_add_request=False,
    ) -> str | None:
        """踢出群成员"""
        if event.get_platform_name() == "aiocqhttp" and isinstance(
            event, AiocqhttpMessageEvent
        ):
            try:
                await event.bot.set_group_kick(
                    group_id=group_id,
                    user_id=user_id,
                    reject_add_request=reject_add_request,
                )
                return None
            except Exception as e:
                logger.warning(f"踢出群成员失败: {str(e)}")
                return str(e)
        else:
            logger.warning("[Giftia] 当前仅支持aiocqhttp平台")
            return "当前仅支持aiocqhttp平台"

    async def group_ban(
        self,
        event: AstrMessageEvent,
        group_id: int,
        user_id: int,
        duration: int = 30 * 60,
    ) -> str | None:
        """禁言"""
        if event.get_platform_name() == "aiocqhttp" and isinstance(
            event, AiocqhttpMessageEvent
        ):
            try:
                await event.bot.set_group_ban(
                    group_id=group_id,
                    user_id=user_id,
                    duration=duration,
                )
                return None
            except Exception as e:
                logger.warning(f"提出群成员失败: {str(e)}")
                return str(e)
        else:
            logger.warning("[Giftia] 当前仅支持aiocqhttp平台")
            return "当前仅支持aiocqhttp平台"

    async def group_leave(self, event: AstrMessageEvent, group_id: int) -> str | None:
        """退群"""
        if event.get_platform_name() == "aiocqhttp" and isinstance(
            event, AiocqhttpMessageEvent
        ):
            try:
                await event.bot.set_group_leave(group_id=group_id)
                return None
            except Exception as e:
                logger.warning(f"退群失败: {str(e)}")
                return str(e)
        else:
            logger.warning("[Giftia] 当前仅支持aiocqhttp平台")
            return "当前仅支持aiocqhttp平台"

    async def msg_emoji_like(
        self,
        event: AstrMessageEvent,
        message_id: int,
        emoji_id: int,
        set=True,
    ):
        """贴表情"""
        if event.get_platform_name() == "aiocqhttp" and isinstance(
            event, AiocqhttpMessageEvent
        ):
            try:
                await event.bot.set_msg_emoji_like(
                    message_id=message_id,
                    emoji_id=emoji_id,
                    set=set,
                )
                return None
            except Exception as e:
                logger.warning(f"贴表情失败: {str(e)}")
                return str(e)
        else:
            logger.warning("[Giftia] 当前仅支持aiocqhttp平台")
            return "当前仅支持aiocqhttp平台"

    async def group_poke(
        self,
        event: AstrMessageEvent,
        group_id: int,
        user_id: int,
    ) -> str | None:
        """戳一戳"""
        if event.get_platform_name() == "aiocqhttp" and isinstance(
            event, AiocqhttpMessageEvent
        ):
            try:
                # logger.info(f"尝试戳一戳: group_id={group_id}, user_id={user_id}")
                await event.bot.group_poke(
                    group_id=group_id,
                    user_id=user_id,
                )
                return None
            except Exception as e:
                logger.warning(f"戳一戳失败: {str(e)}")
                return str(e)
        else:
            logger.warning("[Giftia] 当前仅支持aiocqhttp平台")
            return "当前仅支持aiocqhttp平台"

    async def _msg_chain_to_data(
        self,
        message_chain: list[BaseMessageComponent],
    ) -> list:
        """
        将消息链转换为aiocqhttp的数据结构
        """
        message_data: list = []
        for component in message_chain:
            if isinstance(component, Plain):
                if not component.text.strip():
                    continue
                # 检查前面是不是@，如果是@，添加\u200b字符
                if message_data and message_data[-1].get("type") == "at":
                    component.text = "\u200b " + component.text
                message_data.append(await component.to_dict())
            # 如果是@，也需要检查前面是不是@
            elif isinstance(component, At):
                if message_data and message_data[-1].get("type") == "at":
                    message_data.append(
                        {
                            "type": "text",
                            "data": {"text": "\u200b \u200b"},
                        }
                    )
                message_data.append(await component.to_dict())
            elif isinstance(component, Image | Record):
                # For Image and Record segments, we convert them to base64
                bs64 = await component.convert_to_base64()
                data_dict: dict = {
                    "file": f"base64://{bs64}",
                }
                if isinstance(component, Image):
                    # 同时兼容NapCat/Lagrange/go-cqhttp的命名规范
                    data_dict["subType"] = 1
                    data_dict["sub_type"] = 1
                    data_dict["subtype"] = 1
                    data_dict["summary"] = random.choice(self.sticker_summaries)
                message_data.append(
                    {
                        "type": component.type.lower(),
                        "data": data_dict,
                    }
                )
            elif isinstance(component, File):
                # For File segments, we need to handle the file differently
                d = await component.to_dict()
                message_data.append(d)
            elif isinstance(component, Video):
                d = await component.to_dict()
                message_data.append(d)
            else:
                message_data.append(await component.to_dict())
        return message_data
