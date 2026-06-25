import asyncio
import copy
import json
import random
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import mcp
from aiocqhttp import CQHttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import (
    At,
    File,
    Image,
    Node,
    Nodes,
    Plain,
    Record,
    Reply,
)
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
from .core.emoji_manager import EmojiManager
from .core.http_manager import HttpManager
from .core.llm_tools import GetMessageContextTool, SearchChatHistoryTool, remove_tools
from .core.memory import LTM
from .core.message_parse import (
    MessageData,
    MessageParser,
)
from .core.prompt import (
    build_decision_prompt,
    build_reply_prompt,
)
from .core.scheduler import Scheduler
from .core.schemas import Status
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
        self.msg_number = msg_history.get("msg_number", 300)

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
        self.private_chat_bypass = self.whitelist_config.get(
            "private_chat_bypass_decision_and_whitelist", True
        )
        self.private_user_whitelist_enabled = self.whitelist_config.get(
            "private_user_whitelist_enabled", False
        )
        self.private_user_whitelist = self.whitelist_config.get(
            "private_user_whitelist", []
        )

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
        # 接话分析窗口计数器 bot_name:group_or_user_id -> remaining_messages
        self.active_reply_counters: dict[str, int] = {}
        # 防抖字典
        self.user_debounce_time = self.concurrent_config.get("user_debounce_time", 3)
        self.user_max_debounce_time = self.concurrent_config.get(
            "user_max_debounce_time", 12
        )
        self.debounce_map: dict[str, float] = {}
        self.debounce_start_map: dict[str, float] = {}
        self.debounce_at_map: dict[str, bool] = {}
        # 表情包配置
        self.sticker_config = self.conf.get("sticker_config", {})
        self.random_sticker_count = self.sticker_config.get("random_sticker_count", 20)
        self.sticker_analysis_prompt = self.sticker_config.get(
            "sticker_analysis_prompt", ""
        )
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
        self.passive_memory_enabled = memory_config.get("passive_memory_enabled", False)
        self.passive_memory_provider_ids = memory_config.get("passive_memory_provider_ids", [])
        self.passive_memory_silence_threshold = memory_config.get("passive_memory_silence_threshold", 10)
        self.passive_memory_overflow_threshold = memory_config.get("passive_memory_overflow_threshold", 100)
        self.passive_memory_summary_prompt = memory_config.get("passive_memory_summary_prompt", "")

        # LLM工具配置
        self.tools_config = self.conf.get("tools_config", {})
        # 并发锁
        self.group_locks = defaultdict(lambda: asyncio.Semaphore(self.concurrent_limit))
        # 用户并发锁
        self.user_locks = defaultdict(asyncio.Lock)
        # 消息解析锁
        self.parse_locks = defaultdict(asyncio.Lock)
        # 表情包并发锁
        self.sticker_locks = defaultdict(asyncio.Lock)

        # 实例化
        self.http_manager = HttpManager(self.conf)
        sticker_summaries = self.sticker_config.get(
            "sticker_summaries", ["这是一张表情包"]
        )
        self.aiocqhttp = AIoCQHTTPAction(sticker_summaries=sticker_summaries)

        # 缓存
        self._recall_tasks = set()

        # 正在运行的任务映射
        self.running_tasks: dict[str, asyncio.Task] = {}

        # 正在回复的状态映射
        self.replying_status: dict[str, int] = {}

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        # 实例化
        self.ltm = LTM(self.context, self.embedding_conf, self.rerank_conf)
        self.db = await Database.connect()
        self.data_cache = DataCache(
            db=self.db,
            http_manager=self.http_manager,
            ltm=self.ltm,
            msg_number=self.msg_number,
            energy_recovery_interval=self.energy_recovery_interval,
        )
        self.emoji_manager = EmojiManager(
            self.db, random_sticker_count=self.random_sticker_count
        )
        sticker_summaries = self.conf.get("sticker_config", {}).get(
            "sticker_summaries", ["这是一张表情包"]
        )
        self.xml_parse = XmlParse(
            self.data_cache, self.emoji_manager, sticker_summaries
        )
        self.call_llm = CallLLM(
            context=self.context,
            xml_parse=self.xml_parse,
            caption_config=self.conf.get("caption_config", {}),
            network_config=self.conf.get("network_config", {}),
            sticker_analysis_prompt=self.sticker_analysis_prompt,
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
        # 定时任务
        self.task_manager = Scheduler()
        self.tools_func = ToolsFunc(
            self.conf, self.task_manager, self.db, self.http_manager
        )
        # 注册函数
        self.task_manager.register_func("remind", self.remind_task)

        # 注册函数调用工具
        if self.conf.get("tools_config", {}).get("search_chat_history_enabled", True):
            self.context.add_llm_tools(SearchChatHistoryTool(plugin=self))
            logger.info("已注册函数调用工具: search_chat_history")
        if self.conf.get("tools_config", {}).get("get_message_context_enabled", True):
            self.context.add_llm_tools(GetMessageContextTool(plugin=self))
            logger.info("已注册函数调用工具: get_message_context")

        # 注册 Web API
        from .core.web_api import GiftiaWebApi

        self.web_api = GiftiaWebApi(self)
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/media",
            view_handler=self.web_api.get_media,
            methods=["GET"],
            desc="Get media captions list",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/media/update",
            view_handler=self.web_api.update_media,
            methods=["POST"],
            desc="Update media caption text",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/media/delete",
            view_handler=self.web_api.delete_media,
            methods=["POST"],
            desc="Delete media caption",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/media/file/<hash_val>",
            view_handler=self.web_api.get_media_file,
            methods=["GET"],
            desc="Get cached media file by hash",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/media/file/b64/<hash_val>",
            view_handler=self.web_api.get_media_file_b64,
            methods=["GET"],
            desc="Get cached media file as base64 by hash",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/media/file/thumbnail/b64/<hash_val>",
            view_handler=self.web_api.get_media_file_thumbnail_b64,
            methods=["GET"],
            desc="Get cached media thumbnail as base64 by hash",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/media/genres",
            view_handler=self.web_api.get_media_genres,
            methods=["GET"],
            desc="Get all distinct media genres",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/media/cache/clean",
            view_handler=self.web_api.clean_media_cache,
            methods=["POST"],
            desc="Clean media files cache by criteria",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/memories",
            view_handler=self.web_api.get_memories,
            methods=["GET"],
            desc="Get memories list",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/memories/add",
            view_handler=self.web_api.add_memory,
            methods=["POST"],
            desc="Add new memory",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/memories/update",
            view_handler=self.web_api.update_memory,
            methods=["POST"],
            desc="Update memory text",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/memories/delete",
            view_handler=self.web_api.delete_memory,
            methods=["POST"],
            desc="Delete memory",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/status",
            view_handler=self.web_api.get_bot_status,
            methods=["GET"],
            desc="Get bot status list",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/status/fill_energy",
            view_handler=self.web_api.fill_energy,
            methods=["POST"],
            desc="Fill bot energy",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/status/update",
            view_handler=self.web_api.update_bot_status,
            methods=["POST"],
            desc="Update bot mood/state",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/chat_history",
            view_handler=self.web_api.get_chat_history,
            methods=["GET"],
            desc="Get chat history list",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/profiles/user",
            view_handler=self.web_api.get_user_profiles,
            methods=["GET"],
            desc="Get user profiles list",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/profiles/user/update",
            view_handler=self.web_api.update_user_profile,
            methods=["POST"],
            desc="Update user profile",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/profiles/user/delete",
            view_handler=self.web_api.delete_user_profile,
            methods=["POST"],
            desc="Delete user profile",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/profiles/group",
            view_handler=self.web_api.get_group_profiles,
            methods=["GET"],
            desc="Get group profiles list",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/profiles/group/update",
            view_handler=self.web_api.update_group_profile,
            methods=["POST"],
            desc="Update group profile",
        )
        self.context.register_web_api(
            route="/astrbot_plugin_giftia/profiles/group/delete",
            view_handler=self.web_api.delete_group_profile,
            methods=["POST"],
            desc="Delete group profile",
        )

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
        xml = f'<tool_call name="{tool.name}" description="{tool.description}">{json.dumps(tool.parameters, ensure_ascii=False)}</tool_call>'
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
    async def get_early_memory(
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

        long_memories = await self.data_cache.get_memories(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            limit=limit,
        )
        nodes = []
        for mem in long_memories:
            data = {
                "memory_id": mem.memory_id,
                "text": mem.text,
                "created_at": mem.created_at,
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

    # 删除消息
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删除消息")
    async def delete_message(self, event: AstrMessageEvent):
        """根据ID删除消息"""
        # 查找引用消息
        message_id = None
        for comp in event.get_messages():
            if isinstance(comp, Reply):
                message_id = comp.id
                break
        if not message_id:
            yield await event.send(MessageChain([Plain("未找到引用消息的消息ID")]))
            return
        # 获取机器人名称
        bot_name = self.adapter_id_map[event.platform_meta.id]
        group_or_user_id = event.get_group_id() or event.get_sender_id()
        await self.data_cache.delete_message(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            message_id=str(message_id),
        )
        yield await event.send(MessageChain([Plain("删除消息成功")]))

    # 删除记忆
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删除记忆")
    async def delete_memory(self, event: AstrMessageEvent, memory_id: str):
        """根据ID删除记忆"""
        if not self.embedding_conf.get("enabled", False):
            logger.error("未启用embedding功能")
            yield await event.send(MessageChain([Plain("未启用embedding功能")]))
            return
        await self.data_cache.delete_memory(memory_id)
        yield await event.send(MessageChain([Plain("删除记忆成功")]))

    # 删除全部记忆
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("清空记忆")
    async def delete_all_memories(
        self, event: AstrMessageEvent, bot_name: str, group_or_user_id: str
    ):
        """删除全部记忆"""
        if not self.embedding_conf.get("enabled", False):
            logger.error("未启用embedding功能")
            yield await event.send(MessageChain([Plain("未启用embedding功能")]))
            return
        try:
            await self.data_cache.delete_all_memories(
                bot_name=bot_name, group_or_user_id=group_or_user_id
            )
        except Exception:
            logger.error("删除全部记忆失败")
        yield await event.send(MessageChain([Plain("删除全部记忆成功")]))

    # 加满能量
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("加满能量")
    async def fill_energy(self, event: AstrMessageEvent, bot_name: str):
        """给当前群的指定机器人加满能量"""
        group_or_user_id = event.get_group_id() or event.get_sender_id()
        if not bot_name:
            yield await event.send(MessageChain([Plain("请输入机器人名称")]))
            return

        status = Status(energy="100.0")
        await self.data_cache.set_bot_status(
            bot_name=bot_name, group_id=group_or_user_id, status=status
        )
        yield await event.send(MessageChain([Plain(f"已为机器人 {bot_name} 加满能量")]))

    # 清空媒体缓存
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("清空媒体缓存")
    async def delete_all_media_cache(self, event: AstrMessageEvent):
        """清空全部媒体缓存"""
        try:
            await self.data_cache.clear_caption()
            yield await event.send(MessageChain([Plain("清空媒体缓存成功")]))
        except Exception as e:
            logger.error(f"清空媒体缓存失败，报错：{e}")
            yield await event.send(MessageChain([Plain("清空媒体缓存失败")]))

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
        nodes.extend(
            [
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
            ]
        )
        if index < total_pages:
            nodes.append(
                Node(
                    uin=event.get_sender_id(),
                    name=event.get_sender_name(),
                    content=[Plain(f"/定时任务列表 {index + 1} 查看下一页")],
                )
            )
        yield await event.send(MessageChain([Nodes(nodes)]))

    # 根据botname+group_or_user_id获取定时任务
    @filter.command("获取定时任务")
    async def get_task_by_group(self, event: AstrMessageEvent, prefix: str):
        """根据botname+group_or_user_id获取定时任务"""
        tasks = self.task_manager.get_prefix_jobs(prefix)
        if not tasks:
            yield await event.send(MessageChain([Plain("没有找到相关定时任务")]))
            return
        nodes = []
        for task in tasks:
            nodes.append(
                Node(
                    uin=event.get_sender_id(),
                    name=event.get_sender_name(),
                    content=[Plain(task)],
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

    # 读取媒体转述
    @filter.command("读取媒体转述", alias={"媒体转述"})
    async def get_media_caption(self, event: AstrMessageEvent):
        """读取媒体转述"""
        bot_name = self.adapter_id_map.get(event.platform_meta.id, "")
        group_or_user_id = event.get_group_id() or event.get_sender_id()

        file_name = ""
        media_hash = ""

        for comp in event.get_messages():
            if isinstance(comp, Reply):
                # 优先通过引用消息的ID查找缓存的media_id，这对于引用机器人自己的消息非常有效
                if bot_name:
                    msg_data = await self.data_cache.get_message_by_id(
                        bot_name, group_or_user_id, str(comp.id)
                    )
                    if msg_data and msg_data.media_id_list:
                        media_hash = msg_data.media_id_list[0]

                if comp.chain:
                    for quote in comp.chain:
                        if isinstance(quote, Image) and quote.file:
                            file_name = quote.file
                            break
                        elif isinstance(quote, Record) and quote.file:
                            file_name = quote.file
                            break
                        elif isinstance(quote, File) and quote.file:
                            file_name = quote.file
                            break
            elif isinstance(comp, Image) and comp.file:
                file_name = comp.file
                break
            elif isinstance(comp, Record) and comp.file:
                file_name = comp.file
                break
            elif isinstance(comp, File) and comp.file:
                file_name = comp.file
                break

        media_caption = None
        if media_hash:
            media_caption = await self.data_cache.get_caption_by_hash(media_hash)

        if not media_caption and file_name:
            _, media_caption = await self.data_cache.get_caption_by_filename(file_name)

        if media_caption:
            msg = f"""hash_val: {media_caption.hash_val}
media_type: {media_caption.media_type}
file_name: {media_caption.file_name}
genre: {media_caption.genre}
character: {media_caption.character}
source: {media_caption.source}
text: {media_caption.text}
caption: {media_caption.caption}"""
            yield await event.send(MessageChain([Plain(msg)]))
        else:
            if not media_hash and not file_name:
                yield await event.send(
                    MessageChain([Plain("没有获取到文件或引用消息")])
                )
            else:
                yield await event.send(MessageChain([Plain("未找到媒体转述缓存")]))

    # 删除数据表
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删除数据表")
    async def delete_table(self, event: AstrMessageEvent, table_name: str):
        """删除数据表"""
        result = await self.db.drop_table(table_name)
        if result:
            yield await event.send(
                MessageChain([Plain(f"数据表 {table_name} 删除成功")])
            )
        else:
            yield await event.send(MessageChain([Plain(f"数据表 {table_name} 不存在")]))

    async def search_and_filter_memories(
        self,
        bot_name: str,
        group_or_user_id: str,
        query: str,
        recent_messages: list = None,
        limit: int = 5,
        threshold: float = 0.7,
    ) -> list[dict]:
        """语义搜索并根据当前上下文窗口的活跃用户过滤记忆"""
        embedding_memories = await self.ltm.search_memory(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            query=query,
            limit=limit,
            threshold=threshold,
        )
        if not embedding_memories:
            return []

        if recent_messages is None:
            recent_messages = await self.data_cache.get_recent_message(
                bot_name=bot_name,
                group_id=group_or_user_id,
                limit=self.msg_number,
            )

        active_users = {str(msg.user_id) for msg in recent_messages if msg.user_id}

        filtered_memories = []
        for memory in embedding_memories:
            metadata_str = memory.get("metadata", "{}")
            try:
                meta = json.loads(metadata_str) if metadata_str else {}
            except Exception:
                meta = {}
            
            associated_ids = meta.get("associated_user_ids", [])
            if not associated_ids:
                filtered_memories.append(memory)
                continue
            
            associated_ids_str = {str(uid) for uid in associated_ids}
            if associated_ids_str & active_users:
                filtered_memories.append(memory)
                
        return filtered_memories

    async def check_and_trigger_passive_memory(
        self,
        bot_name: str,
        group_or_user_id: str,
        self_id: str,
    ):
        """检查并触发被动记忆/状态更新总结"""
        if not self.passive_memory_enabled:
            return

        fmt_key = f"{bot_name}:{group_or_user_id}"
        
        if not hasattr(self, "passive_memory_locks"):
            self.passive_memory_locks = {}
        if fmt_key not in self.passive_memory_locks:
            self.passive_memory_locks[fmt_key] = asyncio.Lock()
            
        async with self.passive_memory_locks[fmt_key]:
            max_id = await self.db.get_max_message_id(bot_name, group_or_user_id)
            if max_id == 0:
                return
                
            last_summarized_id = await self.db.get_kv_data(
                f"passive_memory:last_summarized_id:{fmt_key}", 0
            )
            
            if last_summarized_id == 0:
                await self.db.upsert_kv_data(
                    f"passive_memory:last_summarized_id:{fmt_key}", max_id
                )
                await self.db.upsert_kv_data(
                    f"passive_memory:silent_count:{fmt_key}", 0
                )
                return

            if max_id <= last_summarized_id:
                return

            active_counter = self.active_reply_counters.get(fmt_key, 0)
            
            trigger_type = None
            start_id = last_summarized_id + 1
            end_id = max_id
            
            boundary_id = await self.db.get_boundary_message_id(
                bot_name, group_or_user_id, self.msg_number
            )
            
            if boundary_id > last_summarized_id:
                overflow_count = await self.db.get_message_count_by_id_range(
                    bot_name, group_or_user_id, last_summarized_id + 1, boundary_id
                )
                if overflow_count >= self.passive_memory_overflow_threshold:
                    trigger_type = "overflow"
                    end_id = boundary_id
            
            if trigger_type is None and active_counter == 0:
                silent_count = await self.db.get_kv_data(
                    f"passive_memory:silent_count:{fmt_key}", 0
                )
                silent_count += 1
                await self.db.upsert_kv_data(
                    f"passive_memory:silent_count:{fmt_key}", silent_count
                )
                
                if silent_count >= self.passive_memory_silence_threshold:
                    trigger_type = "silence"
                    end_id = max_id
            elif active_counter > 0:
                await self.db.upsert_kv_data(
                    f"passive_memory:silent_count:{fmt_key}", 0
                )
                
            if trigger_type:
                logger.info(
                    f"[Giftia Passive Memory] 触发被动总结 ({trigger_type}). "
                    f"范围: {start_id} 到 {end_id}"
                )
                await self.db.upsert_kv_data(
                    f"passive_memory:silent_count:{fmt_key}", 0
                )
                await self.db.upsert_kv_data(
                    f"passive_memory:last_summarized_id:{fmt_key}", end_id
                )
                
                asyncio.create_task(
                    self._run_background_summarize(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        self_id=self_id,
                        start_id=start_id,
                        end_id=end_id,
                    )
                )

    async def _run_background_summarize(
        self,
        bot_name: str,
        group_or_user_id: str,
        self_id: str,
        start_id: int,
        end_id: int,
    ):
        """后台异步总结历史消息段，提炼记忆与状态"""
        try:
            db_messages = await self.db.get_messages_by_id_range(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                start_id=start_id,
                end_id=end_id,
            )
            if not db_messages:
                return

            bot_participated = any(str(msg.user_id) == str(self_id) for msg in db_messages)
            if not bot_participated:
                logger.debug(
                    f"[Giftia Passive Memory] {bot_name}:{group_or_user_id} 消息范围 {start_id}-{end_id} 内机器人没有直接参与，跳过 LLM 总结。"
                )
                return

            # 获取活跃的参与者
            active_users_in_range = {
                msg.user_id for msg in db_messages 
                if msg.user_id and str(msg.user_id) != str(self_id)
            }

            # 建立昵称与 ID 映射，处理 LLM 可能会直接使用昵称的情况
            nickname_to_user_id = {}
            for msg in db_messages:
                if msg.user_id:
                    nickname_to_user_id[msg.user_id] = msg.user_id
                    if msg.nickname:
                        nickname_to_user_id[msg.nickname] = msg.user_id

            # 读取现有画像与好感度/关系
            user_profiles_str = []
            user_relations_str = []
            for uid in active_users_in_range:
                profile = await self.data_cache.get_user_profile(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    user_id=uid,
                )
                if profile:
                    user_profiles_str.append(f"用户 {uid} ({nickname_to_user_id.get(uid, '')}) 现有画像:\n{profile}")
                
                relation_score, relation_title = await self.data_cache.get_user_relation(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    user_id=uid,
                )
                user_relations_str.append(
                    f"用户 {uid} ({nickname_to_user_id.get(uid, '')}) 的好感度得分: {relation_score}, 头衔: {relation_title or '无'}"
                )

            group_profile = await self.data_cache.get_group_profile(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
            )

            # 格式化聊天记录
            chat_history_lines = []
            for msg in db_messages:
                chat_history_lines.append(f"[{msg.time}] {msg.nickname}({msg.user_id}): {msg.content}")
            chat_history_text = "\n".join(chat_history_lines)

            # 构建 User Prompt
            user_prompt = f"""以下是一段历史聊天记录，你需要根据这段记录，提取/更新长期记忆、用户画像、群聊画像和好感度/关系头衔。

【当前群聊ID/会话ID】: {group_or_user_id}

【现有状态信息】:
1. 用户现有画像:
{"\n---\n".join(user_profiles_str) if user_profiles_str else "无"}

2. 用户现有好感度/关系:
{"\n".join(user_relations_str) if user_relations_str else "无"}

3. 当前群聊的现有画像:
{group_profile or "无"}

【待分析的聊天记录】:
{chat_history_text}
"""

            bot_conf = self.bot_map.get(bot_name, {})
            nickname = bot_conf.get("nickname", bot_name)

            sys_prompt = self.passive_memory_summary_prompt.format(
                nickname=nickname, self_id=self_id
            )

            provider_ids = self.passive_memory_provider_ids
            if not provider_ids:
                logger.warning("[Giftia Passive Memory] 未配置被动总结提供商(passive_memory_provider_ids)，跳过后台总结。")
                return

            completion_text = None
            for provider_id in provider_ids:
                for attempt in range(2):
                    try:
                        logger.info(
                            f"[Giftia Passive Memory] 尝试使用提供商 {provider_id} (第 {attempt+1} 次) 进行后台总结"
                        )
                        llm_resp = await self.context.llm_generate(
                            chat_provider_id=provider_id,
                            system_prompt=sys_prompt,
                            prompt=user_prompt,
                        )
                        if llm_resp and llm_resp.completion_text:
                            completion_text = llm_resp.completion_text
                            break
                    except Exception as e:
                        logger.error(f"[Giftia Passive Memory] 提供商 {provider_id} 调用报错: {e}")
                if completion_text:
                    break

            if not completion_text:
                logger.error("[Giftia Passive Memory] 所有配置的总结提供商均调用失败，本次总结任务终止。")
                await self.db.upsert_kv_data(
                    f"passive_memory:last_summarized_id:{bot_name}:{group_or_user_id}", start_id - 1
                )
                return

            logger.info(f"[Giftia Passive Memory] 大模型总结返回内容:\n{completion_text}")

            # 解析 XML 并写入数据库/缓存
            memory_matches = re.finditer(
                r'<memory(?:\s+users=["\']([^"\']*)["\'])?>(.*?)</memory>',
                completion_text,
                re.DOTALL,
            )
            for match in memory_matches:
                users_attr = match.group(1) or ""
                text = match.group(2).strip()

                if not text or text == "无":
                    continue

                associated_ids = []
                if users_attr:
                    for u in re.split(r'[,，]', users_attr):
                        u = u.strip()
                        resolved_uid = nickname_to_user_id.get(u, u)
                        if resolved_uid:
                            associated_ids.append(resolved_uid)

                primary_user = associated_ids[0] if associated_ids else self_id
                
                await self.data_cache.add_memory(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    text=text,
                    user_id=primary_user,
                    associated_user_ids=associated_ids,
                )
                logger.info(f"[Giftia Passive Memory] 已成功记录长期记忆: {text} (关联用户: {associated_ids})")

            user_profile_matches = re.finditer(
                r'<summary_user_profile\s+user_id=["\']([^"\']*)["\']>(.*?)</summary_user_profile>',
                completion_text,
                re.DOTALL,
            )
            for match in user_profile_matches:
                target_user = match.group(1).strip()
                profile_content = match.group(2).strip()
                resolved_user_id = nickname_to_user_id.get(target_user, target_user)
                if resolved_user_id and profile_content:
                    await self.data_cache.set_user_profile(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        user_id=resolved_user_id,
                        profile=profile_content,
                    )
                    logger.info(f"[Giftia Passive Memory] 已更新用户 {resolved_user_id} 画像")

            group_profile_matches = re.finditer(
                r'<summary_group_profile>(.*?)</summary_group_profile>',
                completion_text,
                re.DOTALL,
            )
            for match in group_profile_matches:
                group_profile_content = match.group(1).strip()
                if group_profile_content:
                    await self.data_cache.set_group_profile(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        profile=group_profile_content,
                    )
                    logger.info(f"[Giftia Passive Memory] 已更新群画像")

            relation_matches = re.finditer(
                r'<update_relation\s+([^>]*)>(.*?)</update_relation>',
                completion_text,
                re.DOTALL,
            )
            for match in relation_matches:
                attr_str = match.group(1)
                reason = match.group(2).strip()
                attrs = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', attr_str))
                target_user = attrs.get("user_id", "").strip()
                score_change_str = attrs.get("score_change", "0").strip()

                resolved_user_id = nickname_to_user_id.get(target_user, target_user)
                try:
                    score_change = int(score_change_str)
                except ValueError:
                    score_change = 0

                if resolved_user_id and score_change != 0:
                    await self.data_cache.update_relation(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        user_id=resolved_user_id,
                        relation=score_change,
                    )
                    logger.info(
                        f"[Giftia Passive Memory] 用户 {resolved_user_id} 好感度变动 {score_change}，原因: {reason}"
                    )

            title_matches = re.finditer(
                r'<set_relation_title\s+([^>]*)>(.*?)</set_relation_title>',
                completion_text,
                re.DOTALL,
            )
            for match in title_matches:
                attr_str = match.group(1)
                title = match.group(2).strip()
                attrs = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', attr_str))
                target_user = attrs.get("user_id", "").strip()

                resolved_user_id = nickname_to_user_id.get(target_user, target_user)
                if resolved_user_id and title:
                    await self.data_cache.set_relation_title(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        user_id=resolved_user_id,
                        title=title,
                    )
                    logger.info(f"[Giftia Passive Memory] 用户 {resolved_user_id} 关系头衔已设置为: {title}")

        except Exception as e:
            logger.error(f"[Giftia Passive Memory] 后台总结执行异常: {e}", exc_info=True)
            await self.db.upsert_kv_data(
                f"passive_memory:last_summarized_id:{bot_name}:{group_or_user_id}", start_id - 1
            )

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-1000)
    async def on_message(self, event: AstrMessageEvent):
        """接收消息"""
        is_private = not event.get_group_id()
        bypass_whitelist = is_private and self.private_chat_bypass

        # 群白名单判断
        if (
            not bypass_whitelist
            and self.group_whitelist_enabled
            and event.unified_msg_origin not in self.group_whitelist
        ):
            logger.debug(f"群 {event.unified_msg_origin} 不在白名单内，跳过处理")
            return

        # 用户白名单判断
        if (
            not bypass_whitelist
            and self.user_whitelist_enabled
            and event.get_sender_id() not in self.user_whitelist
        ):
            logger.debug(f"用户 {event.get_sender_id()} 不在白名单内，跳过处理")
            return

        # 私聊用户白名单判断
        if (
            is_private
            and self.private_user_whitelist_enabled
            and event.get_sender_id() not in self.private_user_whitelist
        ):
            logger.debug(f"私聊用户 {event.get_sender_id()} 不在私聊白名单内，跳过处理")
            return

        # 判断是否为本插件管理的机器人收到的消息
        if event.platform_meta.id not in self.adapter_id_map:
            logger.debug(
                f"{event.platform_meta.id} 消息不是本插件管理的机器人收到的消息，跳过处理"
            )
            return

        # 处理撤回消息
        if hasattr(event.message_obj, "raw_message") and event.message_obj.raw_message:
            raw_message = event.message_obj.raw_message
            message_name = getattr(raw_message, "name", "")
            if message_name in ["notice.group_recall", "notice.friend_recall"]:
                recalled_message_id = str(getattr(raw_message, "message_id", ""))
                bot_name = self.adapter_id_map.get(event.platform_meta.id)
                if bot_name and recalled_message_id:
                    group_or_user_id = event.get_group_id() or event.get_sender_id()
                    try:
                        await self.data_cache.set_message_recalled(
                            bot_name, group_or_user_id, [recalled_message_id]
                        )
                        logger.debug(
                            f"{bot_name} 收到撤回消息事件，已标注消息 {recalled_message_id} 为撤回"
                        )
                    except Exception as e:
                        logger.error(f"处理撤回消息失败: {e}")
                return

        # 跳过机器人自己的消息
        if event.get_sender_id() == event.get_self_id():
            logger.debug(f"{event.platform_meta.id} 消息为机器人自己的消息，跳过处理")
            return

        task = asyncio.create_task(self.job(event))
        task_id = str(id(task))
        self.running_tasks[task_id] = task
        try:
            await task
            if self.passive_memory_enabled and self.embedding_conf.get("enabled", False):
                bot_name = self.adapter_id_map.get(event.platform_meta.id)
                group_or_user_id = event.get_group_id() or event.get_sender_id()
                if bot_name:
                    asyncio.create_task(
                        self.check_and_trigger_passive_memory(
                            bot_name=bot_name,
                            group_or_user_id=group_or_user_id,
                            self_id=event.get_self_id(),
                        )
                    )
        except asyncio.CancelledError:
            logger.info(f"{task_id} 任务被取消")
        except Exception as e:
            logger.error(f"{task_id} 任务执行失败: {e}", exc_info=True)
        finally:
            self.running_tasks.pop(task_id, None)

    async def job(self, event: AstrMessageEvent):
        # 获取机器人名称
        bot_name = self.adapter_id_map[event.platform_meta.id]
        # 获取机器人配置
        bot_conf = self.bot_map[bot_name]
        nickname = bot_conf.get("nickname", bot_name)

        group_or_user_id = event.get_group_id() or event.get_sender_id()

        # Check if deferred transcription is enabled
        caption_config = bot_conf.get("caption_config", {})
        defer_enabled = caption_config.get("defer_caption_enabled", True)

        should_defer = False
        if defer_enabled:
            # Pre-calculate at-mention and active reply counters
            is_just_at = any(
                isinstance(c, At) and str(c.qq) == event.get_self_id()
                for c in event.get_messages()
            )
            is_private = not event.get_group_id()
            if is_private and self.private_chat_bypass:
                is_just_at = True

            fmt_key = f"{bot_name}:{group_or_user_id}"
            active_counter = self.active_reply_counters.get(fmt_key, 0)
            is_active_window = active_counter > 0

            # Defer only if the bot is NOT actively replying or directly mentioned
            if not is_just_at and not is_active_window:
                should_defer = True

        # 处理当前消息同时进行了缓存
        async with self.parse_locks[f"{bot_name}:{group_or_user_id}"]:
            (
                current_message,
                image_urls,
                audio_urls,
            ) = await self.message_parser.parse_user_message(
                event, bot_name, defer_caption=should_defer
            )

        decision_conf = bot_conf.get("decision_conf", {})
        # 如果没有at机器人且未开启决策，直接返回
        # 判断是不是@唤醒
        is_just_at = any(
            isinstance(c, At) and str(c.qq) == event.get_self_id()
            for c in event.get_messages()
        )

        is_private = not event.get_group_id()
        if is_private and self.private_chat_bypass:
            is_just_at = True

        debounce_key = f"{bot_name}:{group_or_user_id}:{event.get_sender_id()}"
        if self.user_debounce_time > 0:
            if debounce_key in self.debounce_start_map:
                self.debounce_at_map[debounce_key] = (
                    self.debounce_at_map.get(debounce_key, False) or is_just_at
                )
                is_just_at = self.debounce_at_map[debounce_key]
            else:
                self.debounce_at_map[debounce_key] = is_just_at

        decrement_counter = False
        if not is_just_at:
            if not decision_conf.get("enabled", True) or not (
                decision_conf.get("provider_ids") or decision_conf.get("provider_id")
            ):
                logger.debug("没有at机器人且未开启决策，跳过处理")
                return
            if decision_conf.get(
                "group_whitelist"
            ) and group_or_user_id not in decision_conf.get("group_whitelist"):
                logger.debug("没有at机器人且当前群组不在决策白名单内，跳过处理")
                return

            # Active window & proactive probability check
            fmt_key = f"{bot_name}:{group_or_user_id}"
            active_counter = self.active_reply_counters.get(fmt_key, 0)
            proactive_prob = decision_conf.get("proactive_probability", 0)

            is_active_window = active_counter > 0
            is_proactive_hit = False
            is_keyword_hit = False

            if is_active_window:
                decrement_counter = True
            else:
                is_proactive_hit = (
                    proactive_prob > 0 and random.randint(1, 100) <= proactive_prob
                )

                # Keyword trigger check
                if (
                    not is_proactive_hit
                    and decision_conf.get("keyword_trigger_enabled", False)
                    and current_message.content
                ):
                    content_lower = current_message.content.lower()
                    keyword_rules = decision_conf.get("keyword_rules", [])
                    default_prob = decision_conf.get("keyword_default_probability", 100)

                    for rule_str in keyword_rules:
                        if not rule_str or not isinstance(rule_str, str):
                            continue
                        if ":" in rule_str:
                            keywords_str, prob_str = rule_str.split(":", 1)
                            prob = prob_str.strip()
                        else:
                            keywords_str = rule_str
                            prob = default_prob

                        kw_list = [
                            k.strip()
                            for k in re.split(r"[,，]", keywords_str)
                            if k.strip()
                        ]
                        for kw in kw_list:
                            if kw.lower() in content_lower:
                                try:
                                    prob_val = int(prob)
                                except (ValueError, TypeError):
                                    prob_val = default_prob

                                if random.randint(1, 100) <= prob_val:
                                    is_keyword_hit = True
                                    logger.info(
                                        f"{bot_name} 匹配到兴趣关键词 '{kw}'，触发接话决策"
                                    )
                                break
                        if is_keyword_hit:
                            break

            if not is_active_window and not is_proactive_hit and not is_keyword_hit:
                logger.debug(
                    "没有at机器人且不满足接话分析窗口、主动概率或关键词触发，跳过处理"
                )
                return

        # 跳过没有文本也没有图片的消息
        if not current_message.content and not image_urls and not audio_urls:
            logger.debug("消息为空，跳过处理")
            return

        # 跳过已唤醒的消息
        if event._has_send_oper:
            logger.debug(f"{bot_name} 跳过已唤醒的消息: {current_message.content}")
            return

        # Debounce logic
        if self.user_debounce_time > 0:
            current_time = time.time()

            if debounce_key not in self.debounce_start_map:
                self.debounce_start_map[debounce_key] = current_time

            time_since_start = current_time - self.debounce_start_map[debounce_key]

            if time_since_start >= self.user_max_debounce_time:
                logger.debug(
                    f"{bot_name} 消息 {debounce_key} 达到最大防抖时间，强制执行"
                )
                self.debounce_start_map.pop(debounce_key, None)
                self.debounce_at_map.pop(debounce_key, None)
                self.debounce_map[debounce_key] = current_time
            else:
                self.debounce_map[debounce_key] = current_time
                await asyncio.sleep(self.user_debounce_time)
                if self.debounce_map.get(debounce_key) != current_time:
                    logger.debug(f"{bot_name} 消息 {debounce_key} 触发防抖，跳过处理")
                    return
                else:
                    self.debounce_start_map.pop(debounce_key, None)
                    self.debounce_at_map.pop(debounce_key, None)

        logger.debug(f"{bot_name} 处理消息: {current_message.content}")
        relevant_memories = None

        decision_conf = bot_conf.get("decision_conf", {})
        reply_key = f"{bot_name}:{group_or_user_id}"

        # 即使是@消息，也更新节流时间，用于给后续的决策节流，但自身不受拦截
        if is_just_at:
            now = time.time()
            if self.user_throttle_time > 0:
                user_throttle_key = f"{bot_name}:{event.get_sender_id()}"
                self.throttle_map[user_throttle_key] = now
            if self.group_throttle_time > 0:
                group_throttle_key = f"{bot_name}:{event.get_group_id()}"
                self.throttle_map[group_throttle_key] = now

        # 如果没有@机器人，需先进行决策
        if not is_just_at:
            # 检查是否正在回复中，防止新的自动决策打断
            if self.replying_status.get(reply_key, 0) > 0:
                logger.debug(f"{bot_name} 消息 {reply_key} 正在回复中，跳过决策")
                return

            # 节流
            if not is_private:
                user_throttle_key = f"{bot_name}:{event.get_sender_id()}"
                if self.user_throttle_time > 0 and not self.can_execute(
                    user_throttle_key, self.user_throttle_time
                ):
                    logger.info(
                        f"{bot_name} 消息用户{user_throttle_key}节流中，跳过处理"
                    )
                    return
                group_throttle_key = f"{bot_name}:{event.get_group_id()}"
                if self.group_throttle_time > 0 and not self.can_execute(
                    group_throttle_key, self.group_throttle_time
                ):
                    logger.info(
                        f"{bot_name} 消息群组{group_throttle_key}节流中，跳过处理"
                    )
                    return

            # 用户并发锁key
            fmt_user_lock = f"{bot_name}:{group_or_user_id}:{event.get_sender_id()}"
            user_lock = self.user_locks[fmt_user_lock]
            if user_lock.locked():
                logger.info(f"{bot_name} 用户{fmt_user_lock}正在决策中，跳过处理")
                return

            # 并发锁key
            fmt_lock = f"{bot_name}:{group_or_user_id}"
            lock = self.group_locks[fmt_lock]
            if self.concurrent_strategy == "discard" and lock.locked():
                logger.info(f"{bot_name} 消息群组{fmt_lock}并发数已达上限，跳过处理")
                return

            async with user_lock:
                async with lock:
                    # 双重检查：防止在等待锁的过程中，上一条消息已经开始回复
                    if self.replying_status.get(reply_key, 0) > 0:
                        logger.debug(
                            f"{bot_name} 消息 {reply_key} 正在回复中，跳过决策 (队列拦截)"
                        )
                        return

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
                    # 读取用户关系
                    user_relation = await self.data_cache.get_user_relation(
                        bot_name=bot_name,
                        group_or_user_id=group_or_user_id,
                        user_id=event.get_sender_id(),
                    )
                    user_prompt = build_decision_prompt(
                        user_id=event.get_sender_id(),
                        group_data=str(
                            await event.get_group(event.get_group_id())
                            if event.get_group_id()
                            else ""
                        ),
                        recent_messages=recent_messages,
                        current_message=current_message,
                        bot_status=bot_status,
                        group_profile=group_profile,
                        user_profile=user_profile,
                        user_relation=user_relation,
                    )
                    decision_conf = bot_conf.get("decision_conf", {})
                    provider_ids = decision_conf.get("provider_ids")
                    if not provider_ids:
                        old_provider_id = decision_conf.get("provider_id")
                        if old_provider_id:
                            provider_ids = [old_provider_id] + decision_conf.get(
                                "fallback_provider_ids", []
                            )
                        else:
                            logger.error(f"{bot_name} 未配置决策模型ID")
                            return None
                    provider_ids = [p for p in provider_ids if p]
                    if not provider_ids:
                        logger.error(f"{bot_name} 未配置决策模型ID")
                        return None
                    if decrement_counter:
                        fmt_key = f"{bot_name}:{group_or_user_id}"
                        self.active_reply_counters[fmt_key] = max(
                            0, self.active_reply_counters.get(fmt_key, 0) - 1
                        )
                        logger.debug(
                            f"{bot_name} 消耗接话分析窗口次数，当前群组剩余分析次数: {self.active_reply_counters[fmt_key]}"
                        )
                    result = await self.call_llm.call_llm_decision(
                        provider_ids=provider_ids,
                        system_prompt=decision_conf.get("decision_prompt"),
                        user_prompt=user_prompt,
                        image_urls=image_urls,
                        audio_urls=audio_urls,
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
                    fmt_key = f"{bot_name}:{group_or_user_id}"
                    window_size = decision_conf.get("reply_active_window", 10)
                    self.active_reply_counters[fmt_key] = window_size
                    logger.info(
                        f"{bot_name} LLM决策判定回复，重置接话分析窗口计数为 {window_size}"
                    )
                    if result.use_rag == 1 and self.embedding_conf.get(
                        "enabled", False
                    ):
                        # 使用RAG
                        embedding_memories = await self.search_and_filter_memories(
                            bot_name=bot_name,
                            group_or_user_id=group_or_user_id,
                            query=result.rag_query,
                            recent_messages=recent_messages,
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
                            relevant_memories = []
                            for memory in rerank_memories:
                                relevant_memories.append(memory["text"])
                        else:
                            relevant_memories = []
                            for memory in embedding_memories:
                                relevant_memories.append(memory["text"])

                    # 决策完成并确定要回复，增加回复计数
                    self.replying_status[reply_key] = (
                        self.replying_status.get(reply_key, 0) + 1
                    )

        # 调用LLM进行回复
        if is_just_at:
            if is_private and self.replying_status.get(reply_key, 0) > 0:
                logger.debug(
                    f"{bot_name} 消息 {reply_key} 正在回复中，私聊防并发单线程拦截"
                )
                return

            # @的消息视为直接回复，更新数据库状态为3
            await self.db.update_message_decision(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                message_id=current_message.message_id,
                reply_decision=3,
                use_rag=2,
            )

            # @的消息直接增加回复计数开始回复
            self.replying_status[reply_key] = self.replying_status.get(reply_key, 0) + 1

        try:
            has_sent_reply = False
            async for chunk in self.dispatch_llm_reply(
                event=event,
                bot_name=bot_name,
                nickname=nickname,
                group_or_user_id=group_or_user_id,
                current_message=current_message,
                image_urls=image_urls,
                audio_urls=audio_urls,
                relevant_memories=relevant_memories,
            ):
                if chunk:
                    await self.dispatch_message(
                        event=event,
                        bot_name=bot_name,
                        nickname=nickname,
                        group_or_user_id=group_or_user_id,
                        llm_result=chunk,
                    )
                    if chunk.msg_chains:
                        has_sent_reply = True
                else:
                    logger.error(f"{bot_name} 生成消息失败，收到空消息块")

            if has_sent_reply:
                fmt_key = f"{bot_name}:{group_or_user_id}"
                window_size = decision_conf.get("reply_active_window", 10)
                self.active_reply_counters[fmt_key] = window_size
                logger.info(
                    f"{bot_name} 机器人发言，重置接话分析窗口计数为 {window_size}"
                )
        finally:
            self.replying_status[reply_key] = max(
                0, self.replying_status.get(reply_key, 0) - 1
            )

    async def dispatch_llm_reply(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        nickname: str,
        group_or_user_id: str,
        current_message: MessageData | None = None,
        remind_message: str | None = None,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        relevant_memories: list[str] | None = None,
        tool_results: list[dict[str, str]] | None = None,
        other_data: list[str] | None = None,
        times=0,
    ):
        """集成用户提示词构建、LLM调用、发送消息、更新数据库、循环函数工具调用等流程"""
        bot_conf = self.bot_map[bot_name]
        success_logs = []
        iso_string = datetime.now().isoformat()
        max_loop = self.tools_config.get("max_loop", 10)
        if times >= max_loop:
            logger.warning(
                f"{bot_name} 达到最大工具调用次数 ({max_loop})，强制退出循环"
            )
            success_logs.append(
                f"系统提示：当前已经达到最大工具调用次数 {max_loop}，请立即停止调用工具，并以现有信息作为最终结果进行回复。"
            )
        # 近期消息可能比当前消息新，方便AI拿到决策期间以及函数调用工具执行期间的消息补充
        recent_messages = await self.data_cache.get_recent_message(
            bot_name, group_or_user_id, self.msg_number
        )
        # 先取所有消息的media_id，按从新到旧的顺序去重获取，确保越新的媒体越优先转述
        hash_vals = []
        seen_media = set()
        for msg in reversed(recent_messages):
            for media_id in reversed(msg.media_id_list):
                if media_id not in seen_media:
                    seen_media.add(media_id)
                    hash_vals.append(media_id)

        caption_config = bot_conf.get("caption_config", {})
        max_deferred = caption_config.get("max_deferred_captions", 5)
        deferred_count = 0

        media_captions: list[MediaCaption] = []
        for hash_val in hash_vals:
            media_caption = await self.data_cache.get_caption_by_hash(hash_val)
            if media_caption:
                # If the media caption has not been transcribed yet, transcribe it now
                if not getattr(media_caption, "is_captioned", True):
                    if deferred_count < max_deferred:
                        deferred_count += 1
                        logger.info(
                            f"[Giftia] 延迟转述触发: hash={hash_val}, type={media_caption.media_type}"
                        )
                        try:
                            from astrbot.core.star.star_tools import StarTools

                            cache_file = (
                                StarTools.get_data_dir("astrbot_plugin_giftia")
                                / "media_cache"
                                / hash_val
                            )
                            if media_caption.media_type == "audio":
                                audio_urls = (
                                    [str(cache_file)]
                                    if cache_file.exists()
                                    else [media_caption.url]
                                )
                                if audio_urls and audio_urls[0]:
                                    transcribed = (
                                        await self.call_llm.call_llm_audio_caption(
                                            audio_urls
                                        )
                                    )
                                    if transcribed:
                                        media_caption.genre = transcribed.genre
                                        media_caption.character = transcribed.character
                                        media_caption.source = transcribed.source
                                        media_caption.text = transcribed.text
                                        media_caption.caption = transcribed.caption
                                        media_caption.is_captioned = True
                                        await self.data_cache.update_caption(
                                            media_caption
                                        )
                            else:  # image or other media
                                image_bytes = None
                                if cache_file.exists():
                                    try:
                                        image_bytes = cache_file.read_bytes()
                                    except Exception as e:
                                        logger.error(f"[Giftia] 读取图片缓存失败: {e}")
                                if not image_bytes and media_caption.url:
                                    image_bytes = (
                                        await self.http_manager.download_media(
                                            media_caption.url
                                        )
                                    )
                                if image_bytes:
                                    base64s, is_animated = await asyncio.to_thread(
                                        self.http_manager.handle_image, image_bytes
                                    )
                                    if base64s:
                                        transcribed = (
                                            await self.call_llm.call_llm_image_caption(
                                                base64s
                                            )
                                        )
                                        if transcribed:
                                            media_caption.genre = transcribed.genre
                                            media_caption.character = (
                                                transcribed.character
                                            )
                                            media_caption.source = transcribed.source
                                            media_caption.text = transcribed.text
                                            media_caption.caption = transcribed.caption
                                            media_caption.is_captioned = True
                                            await self.data_cache.update_caption(
                                                media_caption
                                            )
                        except Exception as e:
                            logger.error(
                                f"[Giftia] 延迟转述处理失败: {e}", exc_info=True
                            )

                if await self.emoji_manager.has_sticker(bot_name, hash_val):
                    media_caption = copy.copy(media_caption)
                    media_caption.caption += " (你已收藏此表情包)"
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
        # 读取用户关系
        user_relation = await self.data_cache.get_user_relation(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            user_id=event.get_sender_id(),
        )
        # 读取长期记忆
        long_memories = []
        if self.embedding_conf.get("enabled", False):
            long_memories = await self.data_cache.get_memories(
                bot_name=bot_name,
                group_or_user_id=group_or_user_id,
                limit=self.embedding_conf.get("inject_limit", 20),
            )
        # 获取机器人表情包并随机抽取50个
        bot_sticker_cache = await self.emoji_manager.get_random_stickers(bot_name)
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
            nickname=nickname,
            bot_status=bot_status,
            tool_results=tool_results,
            long_memories=long_memories,
            relevant_memories=relevant_memories,
            user_profile=user_profile,
            group_profile=group_profile,
            other_data=other_data,
            user_relation=user_relation,
            bot_sticker=bot_sticker_cache,
        )
        llm_reply_conf = bot_conf.get("llm_reply_conf", {})
        provider_ids = llm_reply_conf.get("provider_ids")
        if not provider_ids:
            old_provider_id = llm_reply_conf.get("provider_id")
            if old_provider_id:
                provider_ids = [old_provider_id] + llm_reply_conf.get(
                    "fallback_provider_ids", []
                )
            else:
                logger.error(f"{bot_name} 未配置回复模型ID")
                return
        provider_ids = [p for p in provider_ids if p]
        if not provider_ids:
            logger.error(f"{bot_name} 未配置回复模型ID")
            return
        provider_selection_mode = llm_reply_conf.get(
            "provider_selection_mode", "fallback"
        )
        if provider_selection_mode == "random":
            random.shuffle(provider_ids)
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
        # 添加表情包
        if llm_result.add_stickers:
            categories = await self.db.get_sticker_categories()
            for sticker_id in llm_result.add_stickers:
                async with self.sticker_locks[sticker_id]:
                    # 先检查有没有添加过，如果全局有过，就直接关联而无需再次消耗Token分析
                    if sticker_id in self.emoji_manager.stickers:
                        await self.emoji_manager.add_sticker(
                            bot_name=bot_name, media_id=sticker_id
                        )
                        continue

                    caption = await self.data_cache.get_caption_by_hash(sticker_id)
                    is_useful, sticker = False, None

                    target_url = None
                    for comp in event.get_messages():
                        if isinstance(comp, Reply) and comp.chain:
                            for quote in comp.chain:
                                if isinstance(quote, Image) and quote.url:
                                    if quote.file and sticker_id in quote.file.lower():
                                        target_url = quote.url
                                        break
                                    elif quote.file:
                                        (
                                            quote_hash,
                                            _,
                                        ) = await self.data_cache.get_caption_by_filename(
                                            quote.file
                                        )
                                        if quote_hash == sticker_id:
                                            target_url = quote.url
                                            break
                            if target_url:
                                break
                    if not target_url and caption and caption.url:
                        target_url = caption.url

                    if target_url:
                        # 先将图片下载并转为 base64，防止大模型无法访问本地/内网 URL
                        image_bytes = await self.http_manager.download_media(target_url)
                        if image_bytes:
                            base64s, _ = await asyncio.to_thread(
                                self.http_manager.handle_image, image_bytes
                            )
                            if base64s:
                                (
                                    is_useful,
                                    sticker,
                                ) = await self.call_llm.call_llm_sticker_analysis(
                                    image_urls=base64s,
                                    categories=categories,
                                    media_id=sticker_id,
                                )
                            # 如果判定为有用，则下载保存到本地
                            if is_useful and sticker:
                                local_path = (
                                    await self.emoji_manager.save_sticker_image(
                                        image_bytes, sticker_id
                                    )
                                )
                                sticker.filename = local_path.name

                    if is_useful and sticker:
                        await self.emoji_manager.add_sticker(
                            bot_name=bot_name, media_id=sticker_id, sticker=sticker
                        )
        # 处理客户端互动
        yield llm_result
        # 读取定时任务
        if other_data is None:
            other_data = []
        if llm_result.all_tasks:
            for group_id in llm_result.all_tasks:
                if not group_id:
                    group_id = group_or_user_id
                task_id_prefix = bot_name + "_" + group_id + "_"
                tasks = self.task_manager.get_prefix_jobs(task_id_prefix)
                if not tasks:
                    other_data.append("# 查询到的定时任务\n这个群没有设置定时任务")
                else:
                    other_data.append("# 查询到的定时任务\n" + "\n".join(tasks))
        # 处理RAG检索
        if relevant_memories is None:
            relevant_memories = []
        if llm_result.search_memories and self.embedding_conf.get("enabled", False):
            # 这个群号由后端填写，可以要求AI在记忆的时候标注群号等信息，以后实现跨群记忆
            for group_or_user_id, query in llm_result.search_memories:
                embedding_memories = await self.search_and_filter_memories(
                    bot_name=bot_name,
                    group_or_user_id=group_or_user_id,
                    query=query,
                    recent_messages=recent_messages,
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
                        relevant_memories.append(memory["text"])
                else:
                    for memory in embedding_memories:
                        relevant_memories.append(memory["text"])
            if len(relevant_memories) == 0:
                relevant_memories.append("没有找到相关记忆")

        # 搜索聊天记录
        if llm_result.search_histories:
            for item in llm_result.search_histories:
                limit = min(item.get("limit", 30), 50)
                msgs = await self.db.search_messages(
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

        # 获取上下文
        if llm_result.get_message_contexts:
            for item in llm_result.get_message_contexts:
                limit = min(item.get("limit", 30), 50)
                msgs = await self.db.get_message_context(
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

        # 如果有函数调用工具，继续调用LLM
        if tool_results is None:
            tool_results = []
        # 收集工具返回的图片
        image_base64 = []
        if len(llm_result.tools_to_call) > 0:
            for tool_name, tool_args in llm_result.tools_to_call:
                # 兼容处理带命名空间前缀的工具名（例如 default_api:send_meme -> send_meme）
                clean_tool_name = (
                    tool_name.split(":")[-1] if ":" in tool_name else tool_name
                )
                tool = self.context.get_llm_tool_manager().get_func(clean_tool_name)
                if tool is None:
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
                from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

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
        # 如果有图片，直接发送图片
        if image_base64:
            yield await event.send(
                MessageChain([Image.fromBase64(b64) for b64 in image_base64])
            )
            logger.info(
                f"{bot_name} 从MCP工具收到 {len(image_base64)} 张图片，直接发出去了"
            )
        # 记录操作日志
        if len(success_logs) > 0:
            await self.data_cache.add_message(
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
        if (
            len(llm_result.tools_to_call) > 0
            or len(llm_result.search_memories) > 0
            or len(llm_result.all_tasks) > 0
            or len(llm_result.search_histories) > 0
            or len(llm_result.get_message_contexts) > 0
        ):
            # 这里将仅传递MCP工具的图片，不再传递消息图片（没写错的话）
            logger.debug(f"{bot_name} llm step {times + 1} ...")
            async for chunk in self.dispatch_llm_reply(
                event=event,
                bot_name=bot_name,
                nickname=nickname,
                group_or_user_id=group_or_user_id,
                current_message=current_message,
                relevant_memories=relevant_memories,
                tool_results=tool_results,
                remind_message=remind_message,
                image_urls=image_base64,
                times=times + 1,
                other_data=other_data,
            ):
                yield chunk

    async def dispatch_message(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        nickname: str,
        group_or_user_id: str,
        llm_result: XmlLlmResult,
    ):
        if event.get_platform_name() == "aiocqhttp" and isinstance(
            event, AiocqhttpMessageEvent
        ):
            # 统一记录操作，防止多条消息抢占上下文窗口
            success_logs = []
            iso_string = datetime.now().isoformat()
            # 处理长期记忆
            if llm_result.delete_memories and self.embedding_conf.get("enabled", False):
                for memory_id in llm_result.delete_memories:
                    result = await self.data_cache.delete_memory(memory_id=memory_id)
                    if result:
                        success_logs.append(
                            f"<delete_memory memory_id={memory_id} result='success'/>"
                        )
                    else:
                        success_logs.append(
                            f"<delete_memory memory_id={memory_id} result='failed'/>"
                        )
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
                        f"<recall message_ids={llm_result.delete_message_ids} result={err_msg or 'success'}/>"
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
            # 记录工具的调用情况
            if llm_result.tools_to_call:
                for tool_name, tool_args in llm_result.tools_to_call:
                    success_logs.append(
                        f"<tool_call name={tool_name} args={tool_args} status='dispatched' info='The system has received the call and is processing it.'/>"
                    )
            # 定时任务
            if llm_result.schedule_tasks:
                for group_id, time_expr, remind_content in llm_result.schedule_tasks:
                    task_id = f"{bot_name}_{group_or_user_id}_{uuid.uuid4().hex[:6]}"
                    kwargs = {
                        "unified_msg_origin": event.unified_msg_origin,
                        "adapter_id": event.platform_meta.id,
                        "bot_name": bot_name,
                        "nickname": nickname,
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
            # 添加表情包日志
            if llm_result.add_stickers:
                for sticker_id in llm_result.add_stickers:
                    success_logs.append(
                        f"<add_sticker media_id={sticker_id} result='success'/>"
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
                    msg_str = (
                        llm_result.msg_logs[index]
                        if llm_result.msg_logs and index < len(llm_result.msg_logs)
                        else ""
                    )
                    media_id_list = re.findall(r"\[图片:(.*?)\]", msg_str)
                    msg_data = MessageData(
                        nickname=nickname,
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
            # 踢人
            if llm_result.kick:
                for group_id, user_id in llm_result.kick:
                    try:
                        group_id_int = int(group_id)
                        user_id_int = int(user_id)
                        err_msg = await self.aiocqhttp.group_kick(
                            event=event,
                            group_id=group_id_int,
                            user_id=user_id_int,
                        )
                        success_logs.append(
                            f"<kick user_id={user_id} result={err_msg or 'success'}/>"
                        )
                    except ValueError:
                        logger.error(
                            f"{bot_name} 踢人数据格式错误: {group_id}, {user_id}"
                        )
            # 退群
            if llm_result.leave:
                for group_id in llm_result.leave:
                    try:
                        group_id_int = int(group_id)
                        err_msg = await self.aiocqhttp.group_leave(
                            event=event,
                            group_id=group_id_int,
                        )
                        success_logs.append(
                            f"<leave user_id={event.get_self_id()} result={err_msg or 'success'}/>"
                        )
                    except ValueError:
                        logger.error(f"{bot_name} 退群数据格式错误: {group_id}")
            # 记录成功操作
            if len(success_logs) > 0:
                await self.data_cache.add_message(
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
                        nickname=nickname,
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
        now = time.time()
        # if len(self.throttle_map) > 5000:
        #     self.throttle_map = {
        #         k: v
        #         for k, v in self.throttle_map.items()
        #         if now - v < max(self.user_throttle_time, self.group_throttle_time)
        #     }
        last_time = self.throttle_map.get(key, 0)

        if now - last_time >= throttle_time:
            self.throttle_map[key] = now
            return True
        return False

    def get_platform_adapter(
        self, adapter_id: str
    ) -> tuple[CQHttp, PlatformMetadata] | None:
        """获取平台适配器实例，目前仅支持aiocqhttp"""
        platforms = self.context.platform_manager.get_insts()  # type: ignore
        for p in platforms:
            if isinstance(p, AiocqhttpAdapter) and p.metadata.id == adapter_id:
                return p.bot, p.metadata
        return None

    async def remind_task(
        self,
        unified_msg_origin: str,
        adapter_id: str,
        bot_name: str,
        nickname: str,
        self_id: str,
        platform_name: str,
        user_id: str,
        user_name: str,
        group_id: str,
        group_or_user_id: str,
        remind_message: str,
    ):
        mock_event = self.fake_event(
            self_id=self_id,
            sender_id=user_id,
            sender_name=user_name,
            group_id=group_id,
            unified_msg_origin=unified_msg_origin,
            adapter_id=adapter_id,
        )
        has_sent_reply = False
        async for chunk in self.dispatch_llm_reply(
            event=mock_event,
            bot_name=bot_name,
            nickname=nickname,
            group_or_user_id=group_or_user_id,
            remind_message=f"[定时任务唤醒] {user_name}({user_id}): {remind_message}",
        ):
            if chunk:
                if platform_name == "aiocqhttp":
                    if mock_event:
                        await self.dispatch_message(
                            event=mock_event,
                            bot_name=bot_name,
                            nickname=nickname,
                            group_or_user_id=group_or_user_id,
                            llm_result=chunk,
                        )
                        if chunk.msg_chains:
                            has_sent_reply = True
                        continue
                # 降级到普通消息发送
                if not chunk.msg_chains:
                    continue
                for msg_chain in chunk.msg_chains:
                    await self.context.send_message(
                        unified_msg_origin, MessageChain(msg_chain)
                    )
                    has_sent_reply = True
            else:
                logger.error(f"{bot_name} 定时任务调度失败，未获取到回复内容")

        if has_sent_reply:
            fmt_key = f"{bot_name}:{group_or_user_id}"
            bot_conf = self.bot_map.get(bot_name, {})
            decision_conf = bot_conf.get("decision_conf", {})
            window_size = decision_conf.get("reply_active_window", 10)
            self.active_reply_counters[fmt_key] = window_size
            logger.info(
                f"{bot_name} 定时任务发言，重置接话分析窗口计数为 {window_size}"
            )

    def fake_event(
        self,
        self_id: str,
        sender_id: str,
        sender_name: str,
        group_id: str,
        unified_msg_origin: str,
        adapter_id: str,
    ) -> AstrMessageEvent:
        """伪造一个aiocqhttp的event，用于主动消息复用被动消息函数"""
        mock_event = MagicMock(spec=AiocqhttpMessageEvent)
        adapter = self.get_platform_adapter(adapter_id)
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
        # 卸载函数调用工具
        remove_tools(self.context)
