from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import (
    File,
    Image,
    Plain,
    Record,
    Video,
)
from astrbot.core.message.components import BaseMessageComponent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)


class AIoCQHTTPAction:
    """
    这个类用于处理AIOCQHTTP的互动
    """

    def __init__(self):
        pass

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

        # 针对 aiocqhttp 平台使用更底层的 API 以确保获取 message_id 用于撤回
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
                    # 私聊过滤掉贴表情
                    message_data = [
                        item for item in message_data if item.get("type") != "Face"
                    ]
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
            total_likes = min(int(count), 50)
            # 计算分组
            full_groups = total_likes // 10
            remainder = total_likes % 10
            batches = [10] * full_groups
            if remainder > 0:
                batches.append(remainder)
            for index, count in enumerate(batches):
                try:
                    await event.bot.send_like(user_id=int(user_id), times=count)
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
    ) -> bool:
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
                return True
            except Exception as e:
                logger.warning(f"踢出群成员失败: {str(e)}")
                return False
        else:
            logger.warning("[Giftia] 当前仅支持aiocqhttp平台")
            return False

    async def group_ban(
        self,
        event: AstrMessageEvent,
        group_id: int,
        user_id: int,
        duration: int = 30 * 60,
    ) -> bool:
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
                return True
            except Exception as e:
                logger.warning(f"提出群成员失败: {str(e)}")
                return False
        else:
            logger.warning("[Giftia] 当前仅支持aiocqhttp平台")
            return False

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

    @staticmethod
    async def _msg_chain_to_data(
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
                message_data.append(await component.to_dict())
            elif isinstance(component, Image | Record):
                # For Image and Record segments, we convert them to base64
                bs64 = await component.convert_to_base64()
                message_data.append(
                    {
                        "type": component.type.lower(),
                        "data": {
                            "file": f"base64://{bs64}",
                        },
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
