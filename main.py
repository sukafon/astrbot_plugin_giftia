import asyncio
import json
import random
import time
import uuid
from collections import defaultdict
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import mcp
from aiocqhttp import CQHttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Node, Nodes, Plain
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.astr_agent_context import (
    AgentContextWrapper,
    AstrAgentContext,
)
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
    AiocqhttpAdapter,
)

from .core.aiocqhttp_action import AIoCQHTTPAction
from .core.call_llm import CallLLM
from .core.data_cache import DataCache
from .core.database import Database
from .core.http_manager import HttpManager
from .core.memory import LTM
from .core.message_parse import MessageData, MessageParser
from .core.prompt import (
    build_decision_prompt,
    build_reply_prompt,
)
from .core.scheduler import Scheduler
from .core.tools_func import ToolsFunc
from .core.xml_parse import MediaCaption, XmlLlmResult, XmlParse


class Giftia(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context: Context = context  # type: ignore
        self.conf = config
        bot_list = self.conf.get("bot_template", [])
        # 机器人名称，映射机器人配置
        self.bot_map: dict[str, dict] = {}
        # 机器人可以有多个适配器，多个适配器映射同一个机器人名称
        self.adapter_id_map: dict[str, str] = {}
        for bot_conf in bot_list:
            if not bot_conf.get("enabled", False):
                continue
            self.bot_map[bot_conf["name"]] = bot_conf
            for adapter_id in bot_conf.get("adapter_ids", []):
                self.adapter_id_map[adapter_id] = bot_conf["name"]

        # 图片转述提供商
        self.caption_config = self.conf.get("caption_config", {})
        # 聊天记录配置
        msg_history = self.conf.get("msg_history", {})
        self.msg_number = msg_history.get("msg_number", 50)

        # 白名单配置
        self.whitelist_config = self.conf.get("whitelist_config", {})
        self.group_whitelist_enabled = self.whitelist_config.get(
            "group_whitelist_enabled", False
        )
        self.group_whitelist = self.whitelist_config.get("group_whitelist", [])
        self.user_whitelist_enabled = self.whitelist_config.get(
            "user_whitelist_enabled", False
        )
        self.user_whitelist = self.whitelist_config.get("user_whitelist", [])

        # 并发策略
        self.concurrent_config = self.conf.get("concurrent_config", {})
        self.concurrent_strategy = self.concurrent_config.get(
            "concurrent_strategy", "discard"
        )
        self.concurrent_limit = self.concurrent_config.get("concurrent_limit", 2)
        # 节流配置
        self.user_throttle_time = self.concurrent_config.get("user_throttle_time", 10)
        self.group_throttle_time = self.concurrent_config.get("group_throttle_time", 5)
        self.throttle_map: dict[str, float] = {}
        # 常规配置
        self.normal_config = self.conf.get("normal_config", {})
        self.min_reply_interval = self.normal_config.get("min_reply_interval", 2)
        self.max_reply_interval = self.normal_config.get("max_reply_interval", 4)
        self.energy_recovery_interval = self.normal_config.get(
            "energy_recovery_interval", 90
        )
        # 记忆配置
        memory_config = self.conf.get("memory_config", {})
        self.embedding_conf = memory_config.get("embedding_conf", {})
        self.rerank_conf = memory_config.get("rerank_conf", {})

        # LLM工具配置
        self.tools_config = self.conf.get("tools_config", {})
        # 并发锁
        self.group_locks = defaultdict(lambda: asyncio.Semaphore(self.concurrent_limit))

        # 实例化
        self.http_manager = HttpManager(self.conf)
        self.aiocqhttp = AIoCQHTTPAction()

        # 缓存
        self._recall_tasks = set()

        # 正在运行的任务映射
        self.running_tasks: dict[str, asyncio.Task] = {}

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        # 实例化
        self.db = await Database.connect()
        self.data_cache = DataCache(
            self.db,
            self.http_manager,
            self.msg_number,
            self.energy_recovery_interval,
        )
        self.xml_parse = XmlParse(self.data_cache)
        self.call_llm = CallLLM(
            context=self.context,
            xml_parse=self.xml_parse,
            caption_config=self.conf.get("caption_config", {}),
            network_config=self.conf.get("network_config", {}),
        )
        self.message_parser = MessageParser(
            data_cache=self.data_cache,
            http_manager=self.http_manager,
            image_caption_enabled=self.caption_config.get(
                "image_caption_enabled", True
            ),
            audio_caption_enabled=self.caption_config.get(
                "audio_caption_enabled", True
            ),
            call_llm=self.call_llm,
        )
        self.ltm = LTM(self.embedding_conf, self.rerank_conf)
        # 定时任务
        self.task_manager = Scheduler()
        self.tools_func = ToolsFunc(
            self.conf, self.task_manager, self.db, self.http_manager
        )
        # 注册函数
        self.task_manager.register_func("remind", self.remind_task)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("工具列表")
    async def tool_list(self, event: AstrMessageEvent, index: int = 1):
        """工具列表"""
        tool_set = (
            self.context.get_llm_tool_manager().get_full_tool_set().get_light_tool_set()
        )
        # 分页
        total_pages = (len(tool_set) + 10 - 1) // 10
        # 获取当前页工具
        start = (index - 1) * 10
        current_page_tools = tool_set.tools[start : start + 10]
        if not current_page_tools:
            yield await event.send(
                MessageChain([Plain(f"第 {index} 页没有更多工具了。")])
            )
            return
        nodes = []
        for tool in current_page_tools:
            nodes.append(
                Node(
                    uin=event.get_sender_id(),
                    name=event.get_sender_name(),
                    content=[
                        Plain(f"工具名称: {tool.name}\n工具描述: {tool.description}")
                    ],
                )
            )
        nodes.append(
            Node(
                uin=event.get_sender_id(),
                name=event.get_sender_name(),
                content=[
                    Plain(
                        f"第 {index} 页，{len(current_page_tools)} 个工具；共 {total_pages} 页，{len(tool_set)} 个工具"
                    )
                ],
            )
        )
        if index < total_pages:
            nodes.append(
                Node(
                    uin=event.get_sender_id(),
                    name=event.get_sender_name(),
                    content=[Plain(f"/工具列表 {index + 1} 查看下一页")],
                )
            )
        yield await event.send(MessageChain([Nodes(nodes)]))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("工具解析")
    async def tool_xml(self, event: AstrMessageEvent, name: str):
        """将函数调用工具解析成xml格式"""
        tool = self.context.get_llm_tool_manager().get_full_tool_set().get_tool(name)
        if not tool:
            yield await event.send(MessageChain([Plain(f"未找到工具: {name}")]))
            return
        # 解析成xml
        xml = f'<tool_call name="{tool.name}" description="{tool.description}">{json.dumps(tool.parameters)}</tool_call>'
        node = Node(
            uin=event.get_sender_id(),
            name=event.get_sender_name(),
            content=[Plain(xml)],
        )
        yield await event.send(MessageChain([Nodes([node])]))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("打印embedding模型")
    async def get_embedding_models(self, event: AstrMessageEvent):
        """打印所有支持的模型信息"""
        if not self.embedding_conf.get("enabled", False):
            logger.error("未启用embedding功能")
            return
        models = self.ltm.get_all_models()
        logger.info(models)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("打印rerank模型")
    async def get_rerank_models(self, event: AstrMessageEvent):
        """打印所有支持的模型信息"""
        if not self.rerank_conf.get("enabled", False):
            logger.error("未启用rerank功能")
            return
        models = self.ltm.get_all_rerank_models()
        logger.info(models)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("读取记忆")
    async def get_memory(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        group_or_user_id: str,
        rag_queries: str,
    ):
        """根据ID获取记忆"""
        if not self.embedding_conf.get("enabled", False):
            logger.error("未启用embedding功能")
            yield await event.send(MessageChain([Plain("未启用embedding功能")]))
            return
        embedding_memories = await self.ltm.search_memory(
            bot_name,
            group_or_user_id,
            rag_queries,
            limit=self.embedding_conf.get("limit", 5),
            threshold=self.embedding_conf.get("threshold", 0.7),
        )
        if self.rerank_conf.get("enabled", False):
            rerank_memories = await self.ltm.rerank_memories(
                rag_queries,
                embedding_memories,
                top_k=self.rerank_conf.get("top_k", 5),
                threshold=self.rerank_conf.get("threshold", 0.45),
            )
        else:
            rerank_memories = embedding_memories
        nodes = []
        for mem in rerank_memories:
            data = {
                "id": mem["id"],
                "bot_name": mem["bot_name"],
                "text": mem["text"],
                "created_at": mem["created_at"],
                "_distance": mem["_distance"],
                "_rerank_score": mem.get("score"),
            }
            nodes.append(
                Node(
                    uin=event.get_self_id(),
                    name="Firefly",
                    content=[Plain(json.dumps(data, indent=4, ensure_ascii=False))],
                )
            )
        if not nodes:
            yield await event.send(MessageChain([Plain("未找到相关记忆")]))
            return
        yield await event.send(MessageChain([Nodes(nodes)]))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("读取近期记忆")
    async def get_recent_memory(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        group_or_user_id: str,
        limit: int = 10,
    ):
        """根据ID获取记忆"""
        if not self.embedding_conf.get("enabled", False):
            logger.error("未启用embedding功能")
            yield await event.send(MessageChain([Plain("未启用embedding功能")]))
            return

        embedding_memories = await self.ltm.get_all_memories(
            bot_name,
            group_or_user_id,
            limit=limit,
        )
        nodes = []
        for mem in embedding_memories:
            data = {
                "id": mem["id"],
                "bot_name": mem["bot_name"],
                "text": mem["text"],
                "created_at": mem["created_at"],
            }
            nodes.append(
                Node(
                    uin=event.get_self_id(),
                    name="Firefly",
                    content=[Plain(json.dumps(data, indent=4, ensure_ascii=False))],
                )
            )
        if not nodes:
            yield await event.send(MessageChain([Plain("未找到相关记忆")]))
            return
        yield await event.send(MessageChain([Nodes(nodes)]))

    # 获取全部定时任务
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("定时任务列表")
    async def task_list(self, event: AstrMessageEvent, index: int = 1):
        """获取全部定时任务"""
        tasks = self.task_manager.get_all_jobs()
        # 分页
        total_pages = (len(tasks) + 10 - 1) // 10
        # 获取当前页任务
        start = (index - 1) * 10
        current_page_tasks = tasks[start : start + 10]
        if not current_page_tasks:
            yield await event.send(
                MessageChain([Plain(f"第 {index} 页没有更多任务了。")])
            )
            return
        nodes = []
        for task in current_page_tasks:
            nodes.append(
                Node(
                    uin=event.get_sender_id(),
                    name=event.get_sender_name(),
                    content=[Plain(task)],
                )
            )
        nodes.extend([
            Node(
                uin=event.get_sender_id(),
                name=event.get_sender_name(),
                content=[Plain(f"共 {len(tasks)} 个任务，当前为第 {index} 页")],
            ),
            Node(
                uin=event.get_sender_id(),
                name=event.get_sender_name(),
                content=[Plain("/删除定时任务 <task_id> 删除定时任务")],
            ),
        ])
        if index < total_pages:
            nodes.append(
                Node(
                    uin=event.get_sender_id(),
                    name=event.get_sender_name(),
                    content=[Plain(f"/定时任务列表 {index + 1} 查看下一页")],
                )
            )
        yield await event.send(MessageChain([Nodes(nodes)]))

    # 删除定时任务
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删除定时任务")
    async def delete_task(self, event: AstrMessageEvent, task_id: str):
        """删除定时任务"""
        result = self.task_manager.remove_job(task_id)
        yield await event.send(MessageChain([Plain(result)]))

    # 删除数据表
    # @filter.permission_type(filter.PermissionType.ADMIN)
    # @filter.command("删除数据表")
    # async def delete_table(self, event: AstrMessageEvent, table_name: str):
    #     """删除数据表"""
    #     result = await self.db.drop_table(table_name)
    #     if result:
    #         yield await event.send(
    #             MessageChain([Plain(f"数据表 {table_name} 删除成功")])
    #         )
    #     else:
    #         yield await event.send(MessageChain([Plain(f"数据表 {table_name} 不存在")]))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=-1000)
    async def on_message(self, event: AstrMessageEvent):
        """接收消息"""
        # 群白名单判断
        if (
            self.group_whitelist_enabled
            and event.unified_msg_origin not in self.group_whitelist
        ):
            logger.debug(f"群 {event.unified_msg_origin} 不在白名单内，跳过处理")
            return

        # 用户白名单判断
        if (
            self.user_whitelist_enabled
            and event.get_sender_id() not in self.user_whitelist
        ):
            logger.debug(f"用户 {event.get_sender_id()} 不在白名单内，跳过处理")
            return

        # 判断是否为本插件管理的机器人收到的消息
        if event.platform_meta.id not in self.adapter_id_map:
            logger.debug(
                f"{event.platform_meta.id} 消息不是本插件管理的机器人收到的消息，跳过处理"
            )
            return

        # 跳过机器人自己的消息
        if event.get_sender_id() == event.get_self_id():
            logger.debug(f"{event.platform_meta.id} 消息为机器人自己的消息，跳过处理")
            return

        # 获取机器人名称
        bot_name = self.adapter_id_map[event.platform_meta.id]
        # 获取机器人配置
        bot_conf = self.bot_map[bot_name]

        # 读取群ID，兼容私聊
        group_or_user_id = event.get_group_id() or event.get_sender_id()

        # 处理当前消息同时进行了缓存
        (
            current_message,
            image_urls,
            audio_urls,
        ) = await self.message_parser.parse_user_message(event, bot_name)

        # 如果没有at机器人且未开启决策，直接返回
        if not event.is_at_or_wake_command and (
            not bot_conf.get("decision_conf", {}).get("enabled", True)
            or not bot_conf.get("decision_conf", {}).get("provider_id")
        ):
            logger.debug("没有at机器人且未开启决策，跳过处理")
            return

        # 跳过没有文本也没有图片的消息
        if not current_message.content and not image_urls and not audio_urls:
            logger.debug("消息为空，跳过处理")
            return

        # 跳过已唤醒的消息
        if event._has_send_oper:
            logger.debug(f"{bot_name} 跳过已唤醒的消息: {current_message.content}")
            return

        # 并发锁key
        fmt_lock = f"{bot_name}:{group_or_user_id}"
        lock = self.group_locks[fmt_lock]
        if self.concurrent_strategy == "discard" and lock.locked():
            logger.debug(f"{bot_name} 消息群组{fmt_lock}并发数已达上限，跳过处理")
            return

        # 节流
        user_throttle_key = f"{bot_name}:{event.get_sender_id()}"
        if self.user_throttle_time > 0 and not self.can_execute(
            user_throttle_key, self.user_throttle_time
        ):
            logger.debug(f"{bot_name} 消息用户{user_throttle_key}节流中，跳过处理")
            return
        group_throttle_key = f"{bot_name}:{event.get_group_id()}"
        if self.group_throttle_time > 0 and not self.can_execute(
            group_throttle_key, self.group_throttle_time
        ):
            logger.debug(f"{bot_name} 消息群组{group_throttle_key}节流中，跳过处理")
            return

        async with lock:
            task = asyncio.create_task(
                self.job(
                    event,
                    bot_name=bot_name,
                    current_message=current_message,
                    bot_conf=bot_conf,
                    image_urls=image_urls,
                    audio_urls=audio_urls,
                )
            )
            task_id = str(id(task))
            self.running_tasks[task_id] = task
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"{task_id} 任务被取消")
            except Exception as e:
                logger.error(f"{task_id} 任务执行失败: {e}", exc_info=True)
            finally:
                self.running_tasks.pop(task_id, None)

    async def job(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        current_message: MessageData,
        bot_conf: dict,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
    ):
        logger.debug(f"{bot_name} 处理消息: {current_message.content}")
        rag_memories = None
        group_or_user_id = event.get_group_id() or event.get_sender_id()
        # 如果没有@机器人，需先进行决策
        if (
            not event.is_at_or_wake_command
            and bot_conf.get("decision_conf", {}).get("enabled", True)
            and bot_conf.get("decision_conf", {}).get("provider_id")
        ):
            # 获取近期聊天记录
            recent_messages = await self.data_cache.get_recent_message(
                bot_name=bot_name,
                group_id=group_or_user_id,
                limit=self.msg_number,
            )
            # 获取机器人状态
            bot_status = await self.data_cache.get_bot_status(
                bot_name=bot_name,
                group_id=group_or_user_id,
            )
            # 读取群画像
            group_profile = await self.data_cache.get_group_profile(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
            )
            # 读取用户画像
            user_profile = await self.data_cache.get_user_profile(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                user_id=event.get_sender_id(),
            )
            user_prompt = build_decision_prompt(
                user_id=event.get_sender_id(),
                recent_messages=recent_messages,
                current_message=current_message,
                bot_status=bot_status,
                group_profile=group_profile,
                user_profile=user_profile,
            )
            decision_conf = bot_conf.get("decision_conf", {})
            provider_id = decision_conf.get("provider_id")
            if not provider_id:
                logger.error(f"{bot_name} 未配置决策模型ID")
                return None
            provider_ids = [provider_id] + decision_conf.get(
                "fallback_provider_ids", []
            )
            result = await self.call_llm.call_llm_decision(
                provider_ids=provider_ids,
                system_prompt=decision_conf.get("decision_prompt"),
                user_prompt=user_prompt,
            )
            if result is None:
                logger.error(f"{bot_name} LLM决策失败，默认判定为不回复")
                return None
            # 更新数据库消息的决策标注
            if result.reply_decision != 2 or result.use_rag != 2:
                await self.db.update_message_decision(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    message_id=current_message.message_id,
                    reply_decision=result.reply_decision,
                    use_rag=result.use_rag,
                )
            if result.reply_decision == 0 or result.reply_decision == 2:
                logger.info(f"{bot_name} LLM决策判定：不回复")
                return None
            logger.info(f"{bot_name} LLM决策判定：回复")
            if result.use_rag == 1 and self.embedding_conf.get("enabled", False):
                # 使用RAG
                embedding_memories = await self.ltm.search_memory(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    query=result.rag_query,
                    limit=self.embedding_conf.get("limit", 5),
                    threshold=self.embedding_conf.get("threshold", 0.7),
                )
                if len(embedding_memories) > 0 and self.rerank_conf.get(
                    "enabled", False
                ):
                    rerank_memories = await self.ltm.rerank_memories(
                        query=result.rag_query,
                        memories=embedding_memories,
                        top_k=self.rerank_conf.get("top_k", 5),
                        threshold=self.rerank_conf.get("threshold", 0.45),
                    )
                    rag_memories = []
                    for memory in rerank_memories:
                        rag_memories.append(memory["text"])
                else:
                    rag_memories = []
                    for memory in embedding_memories:
                        rag_memories.append(memory["text"])
        # 调用LLM进行回复
        async for chunk in self.dispatch_llm_reply(
            event=event,
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            current_message=current_message,
            image_urls=image_urls,
            audio_urls=audio_urls,
            rag_memories=rag_memories,
        ):
            await self.dispatch_message(
                event=event,
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                llm_result=chunk,
            )

    async def dispatch_llm_reply(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        group_or_user_id: str,
        current_message: MessageData | None = None,
        remind_message: str | None = None,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        rag_memories: list[str] | None = None,
        tool_results: list[dict[str, str]] | None = None,
        times=0,
    ):
        """集成用户提示词构建、LLM调用、发送消息、更新数据库、循环函数工具调用等流程"""
        if times >= self.tools_config.get("max_loop", 10):
            logger.warning(
                f"{bot_name} 达到最大工具调用次数 ({self.tools_config.get('max_loop', 10)})，强制退出循环"
            )
            return
        bot_conf = self.bot_map[bot_name]
        # 近期消息可能比当前消息新，方便AI拿到决策期间以及函数调用工具执行期间的消息补充
        recent_messages = await self.data_cache.get_recent_message(
            bot_name, group_or_user_id, self.msg_number
        )
        # 先取所有消息的media_id，去重后获取caption xml string
        hash_vals = list({
            media_id for msg in recent_messages for media_id in msg.media_id_list
        })
        media_captions: list[MediaCaption] = []
        for hash_val in hash_vals:
            media_caption = await self.data_cache.get_caption_by_hash(hash_val)
            if media_caption:
                media_captions.append(media_caption)
        # 获取机器人状态
        bot_status = await self.data_cache.get_bot_status(
            bot_name=bot_name,
            group_id=group_or_user_id,
        )
        # 读取用户画像
        user_profile = await self.data_cache.get_user_profile(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            user_id=event.get_sender_id(),
        )
        # 读取群画像
        group_profile = await self.data_cache.get_group_profile(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
        )
        # 这里需要重新构建用户提示词，以补充新的聊天记录以及更新状态
        user_prompt = build_reply_prompt(
            recent_messages=recent_messages,
            media_captions=media_captions,
            current_message=current_message,
            remind_message=remind_message,
            group_data=str(
                await event.get_group(event.get_group_id())
                if event.get_group_id()
                else ""
            ),
            user_id=event.get_sender_id(),
            bot_status=bot_status,
            tool_results=tool_results,
            rag_memories=rag_memories,
            user_profile=user_profile,
            group_profile=group_profile,
        )
        llm_reply_conf = bot_conf.get("llm_reply_conf", {})
        provider_id = llm_reply_conf.get("provider_id")
        if not provider_id:
            logger.error(f"{bot_name} 未配置回复模型ID")
            return
        provider_ids = [provider_id] + llm_reply_conf.get("fallback_provider_ids", [])
        # 调用LLM进行回复
        llm_result = await self.call_llm.call_llm_reply(
            event=event,
            group_or_user_id=group_or_user_id,
            provider_ids=provider_ids,
            system_prompt=llm_reply_conf.get("llm_reply_prompt"),
            user_prompt=user_prompt,
            use_source_tools=self.tools_config.get("use_source_tools", False),
            image_urls=image_urls,
            audio_urls=audio_urls,
            timeout=self.tools_config.get("timeout", 120),
        )
        # 如果没有调用成功，直接返回
        if not llm_result:
            logger.error(f"{bot_name} LLM回复失败")
            return
        # 处理LLM返回结果
        if (
            not remind_message and times == 0 and not llm_result.msg_chains
        ):  # 如果没有消息链，标注为不需要回复
            await self.db.update_message_reply_decision(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                message_id=current_message.message_id if current_message else "",
                reply_decision=0,
            )
        # 更新状态
        if llm_result.status:
            await self.data_cache.set_bot_status(
                bot_name=bot_name,
                group_id=group_or_user_id,
                status=llm_result.status,
            )
        # 处理客户端互动
        yield llm_result
        # 处理长期记忆
        if llm_result.save_memories and self.embedding_conf.get("enabled", False):
            for group_or_user_id, memory in llm_result.save_memories:
                await self.ltm.add_memory(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    text=memory,
                    metadata=json.dumps({"user_id": event.get_sender_id()}),
                )
        # 处理用户画像
        if llm_result.summary_user_profiles:
            for group_id, user_id, profile_content in llm_result.summary_user_profiles:
                await self.data_cache.set_user_profile(
                    bot_name=bot_name,
                    group_or_user_id=group_id,
                    user_id=user_id,
                    profile=profile_content,
                )
        # 处理群画像
        if llm_result.summary_group_profiles:
            for group_id, profile_content in llm_result.summary_group_profiles:
                await self.data_cache.set_group_profile(
                    bot_name=bot_name,
                    group_or_user_id=group_id,
                    profile=profile_content,
                )
        # 处理RAG检索
        if rag_memories is None:
            rag_memories = []
        if llm_result.search_memories and self.embedding_conf.get("enabled", False):
            # 这个群号由后端填写，可以要求AI在记忆的时候标注群号等信息，以后实现跨群记忆
            for group_or_user_id, query in llm_result.search_memories:
                embedding_memories = await self.ltm.search_memory(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    query=query,
                    limit=self.embedding_conf.get("limit", 5),
                    threshold=self.embedding_conf.get("threshold", 0.7),
                )
                if embedding_memories and self.rerank_conf.get("enabled", False):
                    rerank_memories = await self.ltm.rerank_memories(
                        query=query,
                        memories=embedding_memories,
                        top_k=self.rerank_conf.get("top_k", 5),
                        threshold=self.rerank_conf.get("threshold", 0.45),
                    )
                    for memory in rerank_memories:
                        rag_memories.append(memory["text"])
                else:
                    for memory in embedding_memories:
                        rag_memories.append(memory["text"])
            if len(rag_memories) == 0:
                rag_memories.append("没有找到相关记忆")
        # 如果有函数调用工具，继续调用LLM
        if tool_results is None:
            tool_results = []
        # 收集工具返回的图片
        image_base64 = []
        if len(llm_result.tools_to_call) > 0:
            for tool_name, tool_args in llm_result.tools_to_call:
                tool = self.context.get_llm_tool_manager().get_func(tool_name)
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
                    context=AstrAgentContext(context=self.context, event=event),
                    tool_call_timeout=self.tools_config.get("timeout", 120),
                )
                tool_result = await tool.call(run_context, **tool_args)
                # 普通字符串返回
                result = []
                if isinstance(tool_result, str):
                    result.append(tool_result)
                # MCP工具返回结果(看着类型写的，没测试过)
                elif isinstance(tool_result, mcp.types.CallToolResult):
                    for content in tool_result.content:
                        if isinstance(content, mcp.types.TextContent):
                            result.append(content.text)
                        elif isinstance(content, mcp.types.ImageContent):
                            image_base64.append("base64://" + content.data)
                result = {
                    "name": tool_name,
                    "results": "\n".join(result),
                }
                tool_results.append(result)
        if len(llm_result.tools_to_call) > 0 or len(llm_result.search_memories) > 0:
            # 这里将仅传递MCP工具的图片，不再传递消息图片（没写错的话）
            logger.debug(f"{bot_name} llm step {times + 1} ...")
            async for chunk in self.dispatch_llm_reply(
                event=event,
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                current_message=current_message,
                rag_memories=rag_memories,
                tool_results=tool_results,
                remind_message=remind_message,
                image_urls=image_base64,
                times=times + 1,
            ):
                yield chunk

    async def dispatch_message(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        group_or_user_id: str,
        llm_result: XmlLlmResult,
    ):
        if event.get_platform_name() == "aiocqhttp" and isinstance(
            event, AiocqhttpMessageEvent
        ):
            # 统一记录操作，防止多条消息抢占上下文窗口
            success_logs = []
            iso_string = datetime.now().isoformat()
            # 撤回消息
            if llm_result.delete_message_ids:
                # str -> int
                try:
                    ids = [int(msg_id) for msg_id in llm_result.delete_message_ids]
                    err_msg = await self.aiocqhttp.delete_messages(
                        event=event, message_ids=ids
                    )
                    await self.data_cache.set_message_recalled(
                        bot_name, group_or_user_id, llm_result.delete_message_ids
                    )
                    success_logs.append(
                        f"<recall_message message_ids={llm_result.delete_message_ids} result={err_msg or 'success'}/>"
                    )
                except ValueError:
                    logger.error(
                        f"{bot_name} 撤回消息数据格式错误: {llm_result.delete_message_ids}"
                    )
            # 贴表情
            if llm_result.emoji_ids:
                for message_id, emoji_id in llm_result.emoji_ids:
                    try:
                        message_id_int = int(message_id)
                        emoji_id_int = int(emoji_id)
                        err_msg = await self.aiocqhttp.msg_emoji_like(
                            event=event,
                            message_id=message_id_int,
                            emoji_id=emoji_id_int,
                        )
                        success_logs.append(
                            f"<emoji_like message_id={message_id} emoji_id={emoji_id} result={err_msg or 'success'}/>"
                        )
                    except ValueError:
                        logger.error(
                            f"{bot_name} 贴表情数据格式错误: {message_id}, {emoji_id}"
                        )
            # 点赞
            if llm_result.likes:
                for user_id, count in llm_result.likes:
                    try:
                        user_id_int = int(user_id)
                        count_int = int(count)
                        err_msg = await self.aiocqhttp.like(
                            event=event,
                            user_id=user_id_int,
                            count=count_int,
                        )
                        success_logs.append(
                            f"<like user_id={user_id} result={err_msg or 'success'}/>"
                        )
                    except ValueError:
                        logger.error(f"{bot_name} 点赞数据格式错误: {user_id}, {count}")
            # 戳一戳
            if llm_result.poke:
                for group_id, user_id in llm_result.poke:
                    try:
                        group_id_int = int(group_id)
                        user_id_int = int(user_id)
                        err_msg = await self.aiocqhttp.group_poke(
                            event=event,
                            group_id=group_id_int,
                            user_id=user_id_int,
                        )
                        success_logs.append(
                            f"<poke user_id={user_id} result={err_msg or 'success'}/>"
                        )
                    except ValueError:
                        logger.error(
                            f"{bot_name} 戳一戳数据格式错误: {group_id}, {user_id}"
                        )
            # 禁言
            if llm_result.ban:
                for group_id, user_id, duration in llm_result.ban:
                    try:
                        group_id_int = int(group_id)
                        user_id_int = int(user_id)
                        duration_int = int(duration)
                        err_msg = await self.aiocqhttp.group_ban(
                            event=event,
                            group_id=group_id_int,
                            user_id=user_id_int,
                            duration=duration_int,
                        )
                        success_logs.append(
                            f"<ban user_id={user_id} duration={duration} result={err_msg or 'success'}/>"
                        )
                    except ValueError:
                        logger.error(
                            f"{bot_name} 禁言数据格式错误: {group_id}, {user_id}, {duration}"
                        )
            # 定时任务
            if llm_result.schedule_tasks:
                for group_id, time_expr, remind_content in llm_result.schedule_tasks:
                    task_id = f"{bot_name}_{group_or_user_id}_{uuid.uuid4().hex[:6]}"
                    kwargs = {
                        "unified_msg_origin": event.unified_msg_origin,
                        "bot_name": bot_name,
                        "self_id": event.get_self_id(),
                        "platform_name": event.get_platform_name(),
                        "user_id": event.get_sender_id(),
                        "user_name": event.get_sender_name(),
                        "group_id": event.get_group_id(),
                        "group_or_user_id": group_or_user_id,
                        "remind_message": remind_content,
                    }
                    err_msg = self.task_manager.add_job(
                        task_id,
                        "remind",
                        time_expr,
                        kwargs=kwargs,
                    )
                    success_logs.append(
                        f"<schedule_task task_id={task_id} time_expr={time_expr} result={err_msg or 'success'}/>"
                    )
            # 删除定时任务
            if llm_result.delete_schedule_tasks:
                for task_id in llm_result.delete_schedule_tasks:
                    err_msg = self.task_manager.remove_job(task_id)
                    success_logs.append(
                        f"<delete_task task_id={task_id} result={err_msg or 'success'}/>"
                    )

            # 记录成功操作
            if len(success_logs) > 0:
                await self.data_cache.add_message(
                    bot_name,
                    group_or_user_id,
                    MessageData(
                        nickname=bot_name,
                        user_id=event.get_self_id(),
                        group_or_user_id=group_or_user_id,
                        time=iso_string,
                        message_id="",
                        content="操作日志:\n" + "\n".join(success_logs),
                        is_recalled=False,
                        media_id_list=[],
                    ),
                )
            # 其他行为通过消息链统一处理
            for index, msg_chain in enumerate(llm_result.msg_chains):
                if not msg_chain:
                    continue
                # 随机延迟发送
                if index > 0:
                    interval = random.randint(
                        self.min_reply_interval, self.max_reply_interval
                    )
                    await asyncio.sleep(interval)
                success, message_id = await self.aiocqhttp.send_message(
                    event,
                    msg_chain,
                )
                if success and message_id:
                    # 发送成功后再将消息写入缓存
                    iso_string = datetime.now().isoformat()
                    msg_str, media_id_list = await self.message_parser.chain_to_str(
                        msg_chain
                    )
                    msg_data = MessageData(
                        nickname=bot_name,
                        user_id=event.get_self_id(),
                        group_or_user_id=group_or_user_id,
                        time=iso_string,
                        message_id=str(message_id),
                        content=msg_str,
                        is_recalled=False,
                        media_id_list=media_id_list,
                    )
                    # add_message完成了消息的缓存和数据库写入
                    await self.data_cache.add_message(
                        bot_name, group_or_user_id, msg_data
                    )
            # 无论这里是否发送成功都返回，防止重复发送
            return
        # 其他平台使用通用发送方法
        if llm_result.msg_chains:
            for index, msg_chain in enumerate(llm_result.msg_chains):
                # 随机延迟发送
                if index > 0:
                    interval = random.randint(
                        self.min_reply_interval, self.max_reply_interval
                    )
                    await asyncio.sleep(interval)
                try:
                    await event.send(MessageChain(msg_chain))
                    # 发送成功后再将消息写入缓存
                    iso_string = datetime.now().isoformat()
                    msg_str, media_id_list = await self.message_parser.chain_to_str(
                        msg_chain
                    )
                    msg_data = MessageData(
                        nickname=bot_name,
                        user_id=event.get_self_id(),
                        group_or_user_id=group_or_user_id,
                        time=iso_string,
                        message_id="",
                        content=msg_str,
                        is_recalled=False,
                        media_id_list=media_id_list,
                    )
                    # add_message完成了消息的缓存和数据库写入
                    await self.data_cache.add_message(
                        bot_name, group_or_user_id, msg_data
                    )
                except Exception as e:
                    logger.error(f"{bot_name} 通用发送方法失败: {e}")

    def can_execute(self, key: str, throttle_time: float):
        """
        节流
        """
        if len(self.throttle_map) > 5000:
            now = time.time()
            self.throttle_map = {
                k: v
                for k, v in self.throttle_map.items()
                if now - v < max(self.user_throttle_time, self.group_throttle_time)
            }
        now = time.time()
        last_time = self.throttle_map.get(key, 0)

        if now - last_time >= throttle_time:
            self.throttle_map[key] = now
            return True
        return False

    def get_platform_adapter(self) -> tuple[CQHttp, PlatformMetadata] | None:
        """获取平台适配器实例，目前仅支持aiocqhttp"""
        platforms = self.context.platform_manager.get_insts()  # type: ignore
        for p in platforms:
            if isinstance(p, AiocqhttpAdapter):
                return p.bot, p.metadata
        return None

    async def remind_task(
        self,
        unified_msg_origin: str,
        bot_name: str,
        self_id: str,
        platform_name: str,
        user_id: str,
        user_name: str,
        group_id: str,
        group_or_user_id: str,
        remind_message: str,
    ):
        mock_event = self.fake_event(
            self_id, user_id, user_name, group_id, unified_msg_origin
        )
        async for chunk in self.dispatch_llm_reply(
            event=mock_event,
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            remind_message=f"[定时任务唤醒] {user_name}({user_id}): {remind_message}",
        ):
            if platform_name == "aiocqhttp":
                if mock_event:
                    await self.dispatch_message(
                        mock_event, bot_name, group_or_user_id, chunk
                    )
                    continue
            # 降级到普通消息发送
            if not chunk.msg_chains:
                continue
            for msg_chain in chunk.msg_chains:
                await self.context.send_message(
                    unified_msg_origin, MessageChain(msg_chain)
                )

    def fake_event(
        self,
        self_id: str,
        sender_id: str,
        sender_name: str,
        group_id: str,
        unified_msg_origin: str,
    ) -> AstrMessageEvent:
        """伪造一个aiocqhttp的event，用于主动消息复用被动消息函数"""
        mock_event = MagicMock(spec=AiocqhttpMessageEvent)
        adapter = self.get_platform_adapter()
        if adapter:
            bot, metadata = adapter
            mock_event.bot = bot
            mock_event.platform_meta = metadata
        mock_event.get_platform_name = MagicMock(return_value="aiocqhttp")
        mock_event.get_group = AsyncMock(return_value="")  # 不想写了
        mock_event.get_self_id = MagicMock(return_value=self_id)
        mock_event.get_group_id = MagicMock(return_value=group_id)
        mock_event.get_sender_id = MagicMock(return_value=sender_id)
        mock_event.get_sender_name = MagicMock(return_value=sender_name)
        mock_event.unified_msg_origin = unified_msg_origin
        return mock_event

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        # 取消所有生成任务
        for task in list(self.running_tasks.values()):
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.running_tasks.values(), return_exceptions=True)
        self.running_tasks.clear()
        # 关闭http连接池
        await self.http_manager.close_session()
        # 关闭数据库连接
        await self.db.close()
        # 关闭定时任务
        self.task_manager.shutdown()
        # 关闭LTM，释放模型内存
        self.ltm.close()
