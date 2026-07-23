import asyncio
import json
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import (
    File,
    Image,
    Node,
    Nodes,
    Plain,
    Record,
    Reply,
)

from ..utils.schemas import Status
from ..utils.anime_search import search_anime_by_image, search_anime_by_media_id
from ..utils.saucenao_search import search_illust_by_image, search_illust_by_media_id


class CommandHandler:
    def __init__(self, plugin):
        self.plugin = plugin
        self.anime_demand_users: dict[str, float] = {}
        self.illust_demand_users: dict[str, float] = {}

    async def tool_list(self, event: AstrMessageEvent, index: int = 1):
        """工具列表"""
        tool_set = (
            self.plugin.context.get_llm_tool_manager()
            .get_full_tool_set()
            .get_light_tool_set()
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

    async def tool_xml(self, event: AstrMessageEvent, name: str):
        """将函数调用工具解析成xml格式"""
        tool = (
            self.plugin.context.get_llm_tool_manager()
            .get_full_tool_set()
            .get_tool(name)
        )
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

    async def get_embedding_models(self, event: AstrMessageEvent):
        """打印所有支持的模型信息"""
        if not self.plugin.embedding_conf.get("enabled", False):
            logger.error("未启用embedding功能")
            return
        models = self.plugin.ltm.get_all_models()
        logger.info(models)

    async def get_rerank_models(self, event: AstrMessageEvent):
        """打印所有支持的模型信息"""
        if not self.plugin.rerank_conf.get("enabled", False):
            logger.error("未启用rerank功能")
            return
        models = self.plugin.ltm.get_all_rerank_models()
        logger.info(models)

    async def get_memory(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        group_or_user_id: str,
        rag_queries: str,
    ):
        """根据ID获取记忆"""
        if not self.plugin.embedding_conf.get("enabled", False):
            logger.error("未启用embedding功能")
            yield await event.send(MessageChain([Plain("未启用embedding功能")]))
            return
        embedding_memories = await self.plugin.ltm.search_memory(
            bot_name,
            group_or_user_id,
            rag_queries,
            limit=self.plugin.embedding_conf.get(
                "limit", self.plugin.embedding_conf.get("top_k", 5)
            ),
            threshold=self.plugin.embedding_conf.get("threshold", 0.7),
        )
        if self.plugin.rerank_conf.get("enabled", False):
            rerank_memories = await self.plugin.ltm.rerank_memories(
                rag_queries,
                embedding_memories,
                top_k=self.plugin.rerank_conf.get("top_k", 5),
                threshold=self.plugin.rerank_conf.get("threshold", 0.45),
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

    async def get_early_memory(
        self,
        event: AstrMessageEvent,
        bot_name: str,
        group_or_user_id: str,
        limit: int = 10,
    ):
        """根据ID获取记忆"""
        if not self.plugin.embedding_conf.get("enabled", False):
            logger.error("未启用embedding功能")
            yield await event.send(MessageChain([Plain("未启用embedding功能")]))
            return

        long_memories = await self.plugin.data_cache.get_memories(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            limit=limit,
        )
        nodes = []
        for mem in long_memories:
            data = {
                "memory_id": mem.memory_id,
                "text": mem.text,
                "importance": mem.importance,
                "hit_count": mem.hit_count,
                "last_hit_at": mem.last_hit_at,
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

    async def delete_message(self, event: AstrMessageEvent):
        """根据ID删除消息"""
        message_id = None
        for comp in event.get_messages():
            if isinstance(comp, Reply):
                message_id = comp.id
                break
        if not message_id:
            yield await event.send(MessageChain([Plain("未找到引用消息的消息ID")]))
            return
        bot_name = self.plugin.adapter_id_map.get(event.platform_meta.id)
        if not bot_name:
            return
        group_or_user_id = event.get_group_id() or event.get_sender_id()
        await self.plugin.data_cache.delete_message(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            message_id=str(message_id),
        )
        yield await event.send(MessageChain([Plain("删除消息成功")]))

    async def delete_memory(self, event: AstrMessageEvent, memory_id: str):
        """根据ID删除记忆"""
        if not self.plugin.embedding_conf.get("enabled", False):
            logger.error("未启用embedding功能")
            yield await event.send(MessageChain([Plain("未启用embedding功能")]))
            return
        await self.plugin.data_cache.delete_memory(memory_id)
        yield await event.send(MessageChain([Plain("删除记忆成功")]))

    async def delete_all_memories(
        self, event: AstrMessageEvent, bot_name: str, group_or_user_id: str
    ):
        """删除全部记忆"""
        if not self.plugin.embedding_conf.get("enabled", False):
            logger.error("未启用embedding功能")
            yield await event.send(MessageChain([Plain("未启用embedding功能")]))
            return
        try:
            await self.plugin.data_cache.delete_all_memories(
                bot_name=bot_name, group_or_user_id=group_or_user_id
            )
        except Exception:
            logger.error("删除全部记忆失败")
        yield await event.send(MessageChain([Plain("删除全部记忆成功")]))

    async def fill_energy(self, event: AstrMessageEvent, bot_name: str):
        """给当前群的指定机器人加满能量"""
        group_or_user_id = event.get_group_id() or event.get_sender_id()
        if not bot_name:
            yield await event.send(MessageChain([Plain("请输入机器人名称")]))
            return

        status = Status(energy="100.0")
        await self.plugin.data_cache.set_bot_status(
            bot_name=bot_name, group_id=group_or_user_id, status=status
        )
        yield await event.send(MessageChain([Plain(f"已为机器人 {bot_name} 加满能量")]))

    async def delete_all_media_cache(self, event: AstrMessageEvent):
        """清空全部媒体缓存"""
        try:
            await self.plugin.data_cache.clear_caption()
            yield await event.send(MessageChain([Plain("清空媒体缓存成功")]))
        except Exception as e:
            logger.error(f"清空媒体缓存失败，报错：{e}")
            yield await event.send(MessageChain([Plain("清空媒体缓存失败")]))

    async def task_list(self, event: AstrMessageEvent, index: int = 1):
        """获取全部定时任务"""
        tasks = self.plugin.task_manager.get_all_jobs()
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

    async def get_task_by_group(self, event: AstrMessageEvent, prefix: str):
        """根据botname+group_or_user_id获取定时任务"""
        tasks = self.plugin.task_manager.get_prefix_jobs(prefix)
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

    async def delete_task(self, event: AstrMessageEvent, task_id: str):
        """删除定时任务"""
        result = self.plugin.task_manager.remove_job(task_id)
        yield await event.send(MessageChain([Plain(result)]))

    async def get_media_caption(self, event: AstrMessageEvent):
        """读取媒体转述"""
        bot_name = self.plugin.adapter_id_map.get(event.platform_meta.id, "")
        group_or_user_id = event.get_group_id() or event.get_sender_id()

        file_name = ""
        media_hash = ""

        for comp in event.get_messages():
            if isinstance(comp, Reply):
                if bot_name:
                    msg_data = await self.plugin.data_cache.get_message_by_id(
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
            media_caption = await self.plugin.data_cache.get_caption_by_hash(media_hash)

        if not media_caption and file_name:
            _, media_caption = await self.plugin.data_cache.get_caption_by_filename(
                file_name
            )

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

    async def delete_table(self, event: AstrMessageEvent, table_name: str):
        """删除数据表"""
        result = await self.plugin.db.drop_table(table_name)
        if result:
            yield await event.send(
                MessageChain([Plain(f"数据表 {table_name} 删除成功")])
            )
        else:
            yield await event.send(MessageChain([Plain(f"数据表 {table_name} 不存在")]))

    async def force_summarize(
        self, event: AstrMessageEvent, bot_name: str, group_or_user_id: str
    ):
        """手动强制总结当前会话的未处理消息记录"""
        yield await event.send(
            MessageChain([Plain("开始分析并提炼当前会话记忆，请稍候...（同步执行中）")])
        )

        result = await self.plugin.passive_memory_manager.force_trigger_passive_memory(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            self_id=event.get_self_id(),
        )

        yield await event.send(MessageChain([Plain(result)]))

    async def _extract_image_info(self, event: AstrMessageEvent):
        """优先从插件自身维护的 media_cache 中提取图片 media_id"""
        image_url = None
        image_file = None
        media_id = None

        # 1. 尝试从引用回复消息链中解析插件独立 media_cache
        message_obj = getattr(event, "message_obj", None)
        if message_obj and hasattr(message_obj, "message"):
            for comp in message_obj.message:
                if isinstance(comp, Reply) and hasattr(comp, "chain") and comp.chain:
                    try:
                        parsed = await self.plugin.message_parser.chain_to_result(comp.chain)
                        if parsed and parsed.media_id_list:
                            return None, None, parsed.media_id_list[0]
                    except Exception as e:
                        logger.warning(f"[Giftia] 引用消息解析 media_id 异常: {e}")

        # 2. 尝试从当前消息链解析插件独立 media_cache
        try:
            parsed = await self.plugin.message_parser.chain_to_result(event.get_messages())
            if parsed and parsed.media_id_list:
                return None, None, parsed.media_id_list[0]
        except Exception as e:
            logger.warning(f"[Giftia] 当前消息解析 media_id 异常: {e}")

        # 3. Fallback: 提取 Image 组件中的 url 或 file 属性
        if message_obj and hasattr(message_obj, "message"):
            for comp in message_obj.message:
                if isinstance(comp, Image):
                    image_url = getattr(comp, "url", None)
                    image_file = getattr(comp, "file", None)
                    if image_url or image_file:
                        return image_url, image_file, media_id
                elif isinstance(comp, Reply) and hasattr(comp, "chain") and comp.chain:
                    for sub in comp.chain:
                        if isinstance(sub, Image):
                            image_url = getattr(sub, "url", None)
                            image_file = getattr(sub, "file", None)
                            if image_url or image_file:
                                return image_url, image_file, media_id

        return image_url, image_file, media_id

    async def _execute_anime_search(
        self, event: AstrMessageEvent, image_url, image_file, media_id, limit=3
    ):
        bot_id = event.get_self_id()
        bot_name = self.plugin.adapter_id_map.get(event.platform_meta.id, "Giftia")
        if media_id:
            return await search_anime_by_media_id(
                self.plugin,
                media_id,
                limit=limit,
                bot_id=bot_id,
                bot_name=bot_name,
            )
        if image_url:
            return await search_anime_by_image(
                image_url=image_url,
                limit=limit,
                bot_id=bot_id,
                bot_name=bot_name,
            )
        if image_file:
            return await search_anime_by_image(
                image_url=image_file,
                limit=limit,
                bot_id=bot_id,
                bot_name=bot_name,
            )
        return False, MessageChain([Plain("无法识别有效图片")]), "Invalid image"

    def has_anime_demand(self, sender_id: str) -> bool:
        now = time.time()
        if sender_id in self.anime_demand_users and now > self.anime_demand_users[sender_id]:
            del self.anime_demand_users[sender_id]
        if sender_id in self.illust_demand_users and now > self.illust_demand_users[sender_id]:
            del self.illust_demand_users[sender_id]
        return (sender_id in self.anime_demand_users) or (sender_id in self.illust_demand_users)

    async def _schedule_demand_timeout(
        self, event: AstrMessageEvent, sender: str, demand_type: str
    ):
        await asyncio.sleep(30)
        target_dict = (
            self.anime_demand_users
            if demand_type == "anime"
            else self.illust_demand_users
        )
        if sender in target_dict:
            del target_dict[sender]
            name = "搜番" if demand_type == "anime" else "搜插画"
            await event.send(
                MessageChain([Plain(f"🧐你没有发送图片，{name}请求已取消了喵")])
            )

    async def fulfill_anime_demand(self, event: AstrMessageEvent) -> bool:
        sender = event.get_sender_id()
        image_url, image_file, media_id = await self._extract_image_info(event)
        if not (image_url or image_file or media_id):
            return False

        demand_type = None
        if sender in self.anime_demand_users:
            demand_type = "anime"
            del self.anime_demand_users[sender]
        elif sender in self.illust_demand_users:
            demand_type = "illust"
            del self.illust_demand_users[sender]

        if not demand_type:
            return False

        if demand_type == "illust":
            ok, chain, err = await self._execute_illust_search(
                event, image_url, image_file, media_id
            )
        else:
            ok, chain, err = await self._execute_anime_search(
                event, image_url, image_file, media_id
            )
        await event.send(chain)
        return True

    async def search_anime_cmd(self, event: AstrMessageEvent):
        """以图搜番"""
        sender = event.get_sender_id()
        image_url, image_file, media_id = await self._extract_image_info(event)

        if image_url or image_file or media_id:
            if sender in self.anime_demand_users:
                del self.anime_demand_users[sender]
            ok, chain, err = await self._execute_anime_search(
                event, image_url, image_file, media_id
            )
            yield await event.send(chain)
            return

        if sender in self.anime_demand_users or sender in self.illust_demand_users:
            yield await event.send(
                MessageChain([Plain("正在等你发图喵，请不要重复发送")])
            )
            return

        self.anime_demand_users[sender] = time.time() + 30
        yield await event.send(
            MessageChain([Plain("请在 30 秒内发送一张图片让我识别喵")])
        )
        asyncio.create_task(
            self._schedule_demand_timeout(event, sender, "anime")
        )

    async def _execute_illust_search(
        self, event: AstrMessageEvent, image_url, image_file, media_id, limit=3
    ):
        bot_id = event.get_self_id()
        bot_name = self.plugin.adapter_id_map.get(event.platform_meta.id, "Giftia")
        api_key = self.plugin.tools_config.get("saucenao_api_key", "")
        if media_id:
            return await search_illust_by_media_id(
                self.plugin,
                media_id,
                limit=limit,
                api_key=api_key,
                bot_id=bot_id,
                bot_name=bot_name,
            )
        if image_url:
            return await search_illust_by_image(
                image_url=image_url,
                limit=limit,
                api_key=api_key,
                bot_id=bot_id,
                bot_name=bot_name,
            )
        if image_file:
            return await search_illust_by_image(
                image_url=image_file,
                limit=limit,
                api_key=api_key,
                bot_id=bot_id,
                bot_name=bot_name,
            )
        return False, MessageChain([Plain("无法识别有效插画图片")]), "Invalid image"

    async def search_illust_cmd(self, event: AstrMessageEvent):
        """以图搜插画 (SauceNAO)"""
        sender = event.get_sender_id()
        image_url, image_file, media_id = await self._extract_image_info(event)

        if image_url or image_file or media_id:
            if sender in self.illust_demand_users:
                del self.illust_demand_users[sender]
            ok, chain, err = await self._execute_illust_search(
                event, image_url, image_file, media_id
            )
            yield await event.send(chain)
            return

        if sender in self.anime_demand_users or sender in self.illust_demand_users:
            yield await event.send(
                MessageChain([Plain("正在等你发图喵，请不要重复发送")])
            )
            return

        self.illust_demand_users[sender] = time.time() + 30
        yield await event.send(
            MessageChain([Plain("请在 30 秒内发送一张插画图片让我识别来源喵")])
        )
        asyncio.create_task(
            self._schedule_demand_timeout(event, sender, "illust")
        )

