from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from astrbot.api import FunctionTool, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, StarTools
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

if TYPE_CHECKING:
    from ...main import Giftia

TOOLS_NAMESPACE = ["search_chat_history", "get_message_context"]


@dataclass
class SearchChatHistoryTool(FunctionTool):
    plugin: Any = None
    name: str = "search_chat_history"
    description: str = "搜索历史记录，所有参数均可选填"
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "关键字，如果你要查找两人说过的第一句话，这里可填用户ID",
                },
                "user_id": {
                    "type": "string",
                    "description": "筛选特定用户ID（QQ号）发送的消息。",
                },
                "start_time": {
                    "type": "string",
                    "description": "iso8601格式的开始时间",
                },
                "end_time": {
                    "type": "string",
                    "description": "iso8601格式的结束时间",
                },
                "sort_order": {
                    "type": "string",
                    "description": "asc|desc",
                },
                "limit": {
                    "type": "integer",
                    "description": "最大50，默认30",
                },
            },
            "required": [],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        keyword: str = "",
        user_id: str = "",
        start_time: str = "",
        end_time: str = "",
        sort_order: str = "desc",
        limit: int = 30,
    ):
        if self.plugin is None:
            logger.warning("SearchChatHistoryTool 未绑定插件实例")
            return "检索失败：未找到插件实例"
        plugin: Giftia = self.plugin
        event: AstrMessageEvent = context.context.event
        bot_name = plugin.adapter_id_map.get(event.platform_meta.id)
        if not bot_name:
            logger.warning("SearchChatHistoryTool 未找到对应的 bot_name")
            return "检索失败：未找到对应的 bot_name"

        group_or_user_id = event.get_group_id() or event.get_sender_id()
        limit = min(limit, 50)
        msgs = await plugin.db.search_messages(
            group_or_user_id=group_or_user_id,
            bot_name=bot_name,
            user_id=user_id or None,
            keyword=keyword or None,
            start_time=start_time or None,
            end_time=end_time or None,
            sort_order=sort_order or "desc",
            limit=limit,
        )
        if not msgs:
            logger.info("SearchChatHistoryTool 未找到相关历史记录")
            return "未找到相关历史记录"

        lines = [f"[{m.time}] {m.nickname}({m.user_id}): {m.content}" for m in msgs]
        return "查询到的历史记录:\n" + "\n".join(lines)


@dataclass
class GetMessageContextTool(FunctionTool):
    plugin: Any = None
    name: str = "get_message_context"
    description: str = "获取特定消息前后的上下文记录。如果你在搜索记录时得到了一条消息，可以用它的message_id来这里获取上下文。"
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "目标消息的ID。",
                },
                "limit": {
                    "type": "integer",
                    "description": "目标消息前后获取的数量，默认30。最大50。",
                },
            },
            "required": ["message_id"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        message_id: str,
        limit: int = 30,
    ):
        if self.plugin is None:
            logger.warning("GetMessageContextTool 未绑定插件实例")
            return "检索失败：未找到插件实例"
        plugin: Giftia = self.plugin
        event: AstrMessageEvent = context.context.event
        bot_name = plugin.adapter_id_map.get(event.platform_meta.id)
        if not bot_name:
            logger.warning("GetMessageContextTool 未找到对应的 bot_name")
            return "检索失败：未找到对应的 bot_name"

        group_or_user_id = event.get_group_id() or event.get_sender_id()
        limit = min(limit, 50)
        msgs = await plugin.db.get_message_context(
            message_id=message_id,
            group_or_user_id=group_or_user_id,
            bot_name=bot_name,
            limit=limit,
        )
        if not msgs:
            logger.warning("GetMessageContextTool 未找到上下文记录")
            return "未找到上下文记录"

        lines = []
        for m in msgs:
            prefix = "=> " if str(m.message_id) == str(message_id) else "   "
            lines.append(f"{prefix}[{m.time}] {m.nickname}({m.user_id}): {m.content}")
        return f"消息 {message_id} 的上下文:\n" + "\n".join(lines)


def remove_tools(context: Context):
    func_tool = context.get_llm_tool_manager()
    for name in TOOLS_NAMESPACE:
        tool = func_tool.get_func(name)
        if tool:
            StarTools.unregister_llm_tool(name)
            logger.info(f"[Giftia] 已移除 {name} 工具注册")
