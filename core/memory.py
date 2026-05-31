import asyncio
import gc
import uuid

import lancedb
from fastembed import TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder
from lancedb.pydantic import LanceModel, Vector

from astrbot.api import logger
from astrbot.api.star import StarTools


class MemorySchema(LanceModel):
    id: str
    bot_name: str
    group_or_user_id: str
    text: str
    vector: Vector(512)  # type: ignore (BAAI/bge-small-zh-v1.5 的向量维度是 512)
    metadata: str = "{}"
    created_at: str
    updated_at: str


class LTM:
    def __init__(self, embedding_conf: dict, rerank_conf: dict):
        self.embedding_conf = embedding_conf
        self.rerank_conf = rerank_conf
        self.db_path = StarTools.get_data_dir("astrbot_plugin_giftia") / "lancedb"
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.table_name = "ltm"

        # 连接数据库 (没有文件夹会自动创建)，这一步仅做本地 I/O，无需特意放入协程
        self.db = lancedb.connect(self.db_path)
        self.table = self.db.create_table(
            self.table_name, schema=MemorySchema, exist_ok=True
        )
        # 创建索引
        # self.table.create_index()
        # 使用 FastEmbed 初始化计算引擎，它底层自带针对 CPU 的 ONNX 优化
        # 请注意：FastEmbed并不官方支持通用模型，且支持的模型名单上并没有BAAI/bge-base-zh-v1.5
        if self.embedding_conf.get("enabled", False):
            self.embed_model = TextEmbedding(
                model_name=self.embedding_conf.get(
                    "model_name", "BAAI/bge-small-zh-v1.5"
                )
            )
            logger.info(
                f"LTM当前运行的embedding模型名称: {self.embed_model.model_name}"
            )
            for model_info in TextEmbedding.list_supported_models():
                if model_info["model"] == self.embed_model.model_name:
                    logger.info(f"模型官方定义维度: {model_info['dim']}")
                    logger.info(f"模型硬盘空间占用: {model_info['size_in_GB']} GB")
                    logger.info(f"模型描述: {model_info['description']}")
        if self.rerank_conf.get("enabled", False):
            self.reranker = TextCrossEncoder(
                model_name=self.rerank_conf.get("model_name", "BAAI/bge-reranker-base")
            )
            logger.info(f"LTM当前运行的rerank模型名称: {self.reranker.model_name}")
            for model_info in TextCrossEncoder.list_supported_models():
                if model_info["model"] == self.reranker.model_name:
                    logger.info(f"模型硬盘空间占用: {model_info['size_in_GB']} GB")
                    logger.info(f"模型描述: {model_info['description']}")

    def get_all_models(self) -> list[dict]:
        """获取所有支持的模型信息"""
        return TextEmbedding.list_supported_models()

    def get_all_rerank_models(self) -> list[dict]:
        """获取所有支持的模型信息"""
        return TextCrossEncoder.list_supported_models()

    async def add_memory(
        self,
        bot_name: str,
        group_or_user_id: str,
        text: str,
        time: str,
        metadata: str = "{}",
    ) -> tuple[str, bytes] | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._add_memory_sync,
            bot_name,
            group_or_user_id,
            text,
            time,
            metadata,
        )

    def _add_memory_sync(
        self, bot_name: str, group_or_user_id: str, text: str, time: str, metadata: str
    ) -> tuple[str, bytes] | None:
        """添加一条记忆"""
        if not hasattr(self, "embed_model"):
            logger.error("Embedding model not initialized")
            return None
        memory_id = str(uuid.uuid4())

        # 使用 TextEmbedding 实例手动计算向量
        vector = list(self.embed_model.embed([text]))[0]

        self.table.add([
            {
                "id": memory_id,
                "bot_name": bot_name,
                "group_or_user_id": group_or_user_id,
                "text": text,
                "vector": vector.tolist() if hasattr(vector, "tolist") else vector,
                "metadata": metadata,
                "created_at": time,
                "updated_at": time,
            }
        ])
        return memory_id, vector.tobytes()

    async def get_memory(self, memory_ids: list[str]) -> list[dict] | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_memory_sync, memory_ids)

    def _get_memory_sync(self, memory_ids: list[str]) -> list[dict] | None:
        """根据ID获取记忆"""
        try:
            results = (
                self.table
                .search()
                .where(f"id IN ({','.join(memory_ids)})")
                .limit(len(memory_ids))
                .to_list()
            )
            if results:
                return results
            return None
        except Exception as e:
            logger.error(f"Get memory failed: {e}")
            return None

    async def search_memory(
        self,
        bot_name: str,
        group_or_user_id: str,
        query: str,
        limit: int = 5,
        threshold: float = 0.7,
    ) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._search_memory_sync,
            bot_name,
            group_or_user_id,
            query,
            limit,
            threshold,
        )

    def _search_memory_sync(
        self,
        bot_name: str,
        group_or_user_id: str,
        query: str,
        limit: int,
        threshold: float,
    ) -> list[dict]:
        """语义搜索相关记忆"""
        try:
            if not hasattr(self, "embed_model"):
                logger.error("Embedding model not initialized")
                return []
            # 手动将搜索文本转换为向量再喂给 LanceDB 查询
            query_vector = list(self.embed_model.embed([query]))[0]
            if hasattr(query_vector, "tolist"):
                query_vector = query_vector.tolist()

            results = (
                self.table
                .search(query_vector)
                .where(
                    f"bot_name = '{bot_name}' AND group_or_user_id = '{group_or_user_id}'",
                    prefilter=True,
                )
                .limit(limit)
                .to_list()
            )
            # 过滤低相关性条目 (LanceDB 的 _distance 越小代表越相似)
            # logger.debug(f"Memory search results: {results}")
            if threshold is not None:
                results = [r for r in results if r.get("_distance", 0) <= threshold]
            return results
        except Exception as e:
            logger.error(f"Search memory failed: {e}")
            return []

    async def get_all_memories(
        self, bot_name: str, group_or_user_id: str, limit: int = 100
    ) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._get_all_memories_sync, bot_name, group_or_user_id, limit
        )

    def _get_all_memories_sync(
        self, bot_name: str, group_or_user_id: str, limit: int
    ) -> list[dict]:
        """获取所有早期记忆"""
        try:
            results = (
                self.table
                .search()
                .where(
                    f"bot_name = '{bot_name}' AND group_or_user_id = '{group_or_user_id}'"
                )
                .limit(limit)
                .to_list()
            )
            return results
        except Exception as e:
            logger.error(f"Get all memories failed: {e}")
            return []

    # async def update_memory(
    #     self, memory_id: str, text: str, metadata: str = "{}"
    # ) -> bool:
    #     """修改记忆内容（重写以更新向量）"""
    #     loop = asyncio.get_running_loop()
    #     return await loop.run_in_executor(
    #         None, self._update_memory_sync, memory_id, text, metadata
    #     )

    # def _update_memory_sync(self, memory_id: str, text: str, metadata: str) -> bool:
    #     """修改记忆内容（重写以更新向量）"""
    #     try:
    #         memory = self._get_memory_sync([memory_id])
    #         if not memory:
    #             return False
    #         self._delete_memory_sync(memory_id)
    #         now = datetime.now().isoformat()
    #         self.table.add([
    #             {
    #                 "id": memory_id,
    #                 "bot_name": memory[0]["bot_name"],
    #                 "group_or_user_id": memory[0]["group_or_user_id"],
    #                 "text": text,
    #                 "metadata": metadata,
    #                 "created_at": memory[0]["created_at"],
    #                 "updated_at": now,
    #             }
    #         ])
    #         return True
    #     except Exception as e:
    #         logger.error(f"Update memory failed: {e}")
    #         return False

    async def delete_memory(self, memory_id: str) -> bool:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._delete_memory_sync, memory_id)

    def _delete_memory_sync(self, memory_id: str) -> bool:
        """删除一条记忆"""
        try:
            self.table.delete(f"id = '{memory_id}'")
            return True
        except Exception as e:
            logger.error(f"Delete memory failed: {e}")
            return False

    async def delete_all_memories(self, bot_name: str, group_or_user_id: str) -> bool:
        """删除全部记忆"""
        try:
            self.table.delete(
                f"bot_name = '{bot_name}' AND group_or_user_id = '{group_or_user_id}'"
            )
            return True
        except Exception as e:
            logger.error(f"Delete all memories failed: {e}")
            return False

    async def rerank_memories(
        self, query: str, memories: list[dict], top_k: int = 5, threshold: float = 0.5
    ) -> list[dict]:
        """
        使用 Cross-Encoder 对记忆进行重排序
        :param query: 用户的查询语句
        :param memories: 记忆列表，每个元素包含 'text' 字段
        :param top_k: 返回前 k 个最相关的记忆
        :return: 按相关性排序后的记忆列表
        """
        try:
            if not hasattr(self, "reranker"):
                logger.error("Reranker not initialized")
                return []
            # 提取记忆文本
            memory_texts = [memory["text"] for memory in memories]

            # 使用 Cross-Encoder 计算相关性分数
            # reranker.rerank 返回的是一个生成器，包含每个记忆的分数
            scores_gen = self.reranker.rerank(query, memory_texts)
            scores = list(scores_gen)

            # 将分数与记忆合并，并按分数从高到低排序
            reranked_memories = []
            for score, memory in zip(scores, memories):
                if score < threshold:
                    continue
                memory["score"] = score
                reranked_memories.append(memory)

            # 按分数排序（降序）
            reranked_memories.sort(key=lambda x: x["score"], reverse=True)

            # 返回前 top_k 个结果
            return reranked_memories[:top_k]

        except Exception as e:
            logger.error(f"Rerank memories failed: {e}")
            # 如果失败，返回原始记忆（不排序）
            return memories

    def close(self):
        """显式释放模型底层 ONNX 内存"""
        logger.info("正在释放 LTM 模型内存...")
        if hasattr(self, "embed_model"):
            del self.embed_model
        if hasattr(self, "reranker"):
            del self.reranker

        # 强制进行 Python 垃圾回收，促使底层 C++ 对象析构
        gc.collect()
