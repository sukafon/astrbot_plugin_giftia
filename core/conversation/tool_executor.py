import mcp
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.astr_agent_context import AgentContextWrapper, AstrAgentContext
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

from ..utils.schemas import MessageData, XmlLlmResult


class ToolExecutor:
    def __init__(self, plugin):
        self.plugin = plugin

    async def execute_tools_and_queries(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        nickname: str,
        group_or_user_id: str,
        llm_result: XmlLlmResult,
        recent_messages: list,
        relevant_memories: list[str],
        other_data: list[str],
        tool_results: list[dict],
        times: int,
    ) -> list[str]:
        """
        执行大模型请求的所有工具调用与数据库/定时任务查询。
        更新传入的 relevant_memories, other_data, tool_results 列表。
        返回工具执行产生的 image_base64 列表。
        """
        success_logs = []
        iso_string = datetime.now().isoformat()

        # 1. 查询定时任务
        if llm_result.all_tasks:
            for group_id in llm_result.all_tasks:
                if not group_id:
                    group_id = group_or_user_id
                task_id_prefix = bot_name + "_" + group_id + "_"
                tasks = self.plugin.task_manager.get_prefix_jobs(task_id_prefix)
                if not tasks:
                    other_data.append("# 查询到的定时任务\n这个群没有设置定时任务")
                else:
                    other_data.append("# 查询到的定时任务\n" + "\n".join(tasks))

        # 2. 处理 RAG 检索
        if llm_result.search_memories and self.plugin.embedding_conf.get("enabled", False):
            for target_group_or_user_id, query in llm_result.search_memories:
                embedding_memories = await self.plugin.passive_memory_manager.search_and_filter_memories(
                    bot_name=bot_name,
                    group_or_user_id=target_group_or_user_id,
                    query=query,
                    recent_messages=recent_messages,
                    limit=self.plugin.embedding_conf.get("limit", 5),
                    threshold=self.plugin.embedding_conf.get("threshold", 0.7),
                )
                if embedding_memories and self.plugin.rerank_conf.get("enabled", False):
                    rerank_memories = await self.plugin.ltm.rerank_memories(
                        query=query,
                        memories=embedding_memories,
                        top_k=self.plugin.rerank_conf.get("top_k", 5),
                        threshold=self.plugin.rerank_conf.get("threshold", 0.45),
                    )
                    for memory in rerank_memories:
                        relevant_memories.append(memory["text"])
                else:
                    for memory in embedding_memories:
                        relevant_memories.append(memory["text"])
            if len(relevant_memories) == 0:
                relevant_memories.append("没有找到相关记忆")

        # 3. 搜索聊天历史记录
        if llm_result.search_histories:
            for item in llm_result.search_histories:
                limit = min(item.get("limit", 30), 50)
                msgs = await self.plugin.db.search_messages(
                    group_or_user_id=item["group_or_user_id"],
                    bot_name=bot_name,
                    user_id=item.get("user_id") or None,
                    keyword=item.get("keyword") or None,
                    start_time=item.get("start_time") or None,
                    end_time=item.get("end_time") or None,
                    sort_order=item.get("sort_order") or "desc",
                    limit=limit,
                )
                if not msgs:
                    other_data.append("# 查询到的历史记录\n未找到相关历史记录")
                else:
                    lines = [
                        f"[{m.time}] {m.nickname}({m.user_id}): {m.content}"
                        for m in msgs
                    ]
                    other_data.append("# 查询到的历史记录\n" + "\n".join(lines))

        # 4. 获取消息上下文
        if llm_result.get_message_contexts:
            for item in llm_result.get_message_contexts:
                limit = min(item.get("limit", 30), 50)
                msgs = await self.plugin.db.get_message_context(
                    message_id=item["message_id"],
                    group_or_user_id=item["group_or_user_id"],
                    bot_name=bot_name,
                    limit=limit,
                )
                if not msgs:
                    other_data.append(
                        f"# 消息上下文(ID:{item['message_id']})\n未找到上下文"
                    )
                else:
                    lines = []
                    for m in msgs:
                        prefix = (
                            "=> "
                            if str(m.message_id) == str(item["message_id"])
                            else "   "
                        )
                        lines.append(
                            f"{prefix}[{m.time}] {m.nickname}({m.user_id}): {m.content}"
                        )
                    other_data.append(
                        f"# 消息上下文(ID:{item['message_id']})\n" + "\n".join(lines)
                    )

        # 5. 执行函数/MCP工具调用
        image_base64 = []
        if len(llm_result.tools_to_call) > 0:
            for tool_name, tool_args in llm_result.tools_to_call:
                clean_tool_name = (
                    tool_name.split(":")[-1] if ":" in tool_name else tool_name
                )
                tool = self.plugin.context.get_llm_tool_manager().get_func(clean_tool_name)
                if tool is None:
                    tool = self.plugin.context.get_llm_tool_manager().get_func(tool_name)

                if tool is None:
                    logger.error(f"{bot_name} 工具 {tool_name} 不存在")
                    result = {
                        "name": tool_name,
                        "result": "工具不存在",
                    }
                    tool_results.append(result)
                    continue

                # 手动调用工具
                run_context = AgentContextWrapper(
                    context=AstrAgentContext(context=self.plugin.context, event=event),
                    tool_call_timeout=self.plugin.tools_config.get("timeout", 120),
                )

                result = []
                try:
                    async for tool_result in FunctionToolExecutor.execute(
                        tool, run_context, **tool_args
                    ):
                        if isinstance(tool_result, str):
                            result.append(tool_result)
                        elif isinstance(tool_result, mcp.types.CallToolResult):
                            for content in tool_result.content:
                                if isinstance(content, mcp.types.TextContent):
                                    result.append(content.text)
                                elif isinstance(content, mcp.types.ImageContent):
                                    result.append("图片已直接发送给用户")
                                    image_base64.append("base64://" + content.data)
                except Exception as e:
                    logger.error(
                        f"Error executing tool {tool_name}: {e}", exc_info=True
                    )
                    result.append(f"工具执行失败: {e}")

                result_dict = {
                    "name": tool_name,
                    "results": "\n".join(result),
                }
                tool_results.append(result_dict)
                success_logs.append(
                    f"<tool_call name={tool_name} args={tool_args} status='finished' />"
                )

        # 6. 写入操作日志
        if len(success_logs) > 0:
            await self.plugin.data_cache.add_message(
                bot_name,
                group_or_user_id,
                MessageData(
                    nickname=nickname,
                    user_id=event.get_self_id(),
                    group_or_user_id=group_or_user_id,
                    time=iso_string,
                    message_id="",
                    content="\n".join(success_logs),
                    is_recalled=False,
                    media_id_list=[],
                    role="operation_log",
                ),
            )

        return image_base64
