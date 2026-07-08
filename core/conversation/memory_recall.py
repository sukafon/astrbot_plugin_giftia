from astrbot.api import logger


def conf_int(conf: dict, key: str, default: int) -> int:
    try:
        return int(conf.get(key, default))
    except (TypeError, ValueError):
        return default


def conf_float(conf: dict, key: str, default: float) -> float:
    try:
        return float(conf.get(key, default))
    except (TypeError, ValueError):
        return default


def resolve_memory_search_limit(
    embedding_conf: dict,
    *,
    limit_key: str | None = None,
    default: int = 5,
) -> int:
    fallback = conf_int(
        embedding_conf,
        "limit",
        conf_int(embedding_conf, "top_k", default),
    )
    if limit_key:
        return conf_int(embedding_conf, limit_key, fallback)
    return fallback


async def search_memories_with_rerank(
    plugin,
    *,
    bot_name: str,
    group_or_user_id: str,
    query: str,
    recent_messages: list,
    limit_key: str | None = None,
    default_limit: int = 5,
    log_context: str = "记忆召回",
) -> list[dict]:
    """Search memories with shared embedding/rerank configuration handling."""
    if not plugin.embedding_conf.get("enabled", False):
        return []

    query = (query or "").strip()
    if not query:
        return []

    search_limit = resolve_memory_search_limit(
        plugin.embedding_conf,
        limit_key=limit_key,
        default=default_limit,
    )
    if search_limit <= 0:
        return []

    threshold = conf_float(plugin.embedding_conf, "threshold", 0.7)
    try:
        memories = await plugin.passive_memory_manager.search_and_filter_memories(
            bot_name=bot_name,
            group_or_user_id=group_or_user_id,
            query=query,
            recent_messages=recent_messages,
            limit=search_limit,
            threshold=threshold,
        )
        if memories and plugin.rerank_conf.get("enabled", False):
            memories = await plugin.ltm.rerank_memories(
                query=query,
                memories=memories,
                top_k=conf_int(plugin.rerank_conf, "top_k", search_limit),
                threshold=conf_float(plugin.rerank_conf, "threshold", 0.45),
            )
        if memories:
            await plugin.data_cache.record_memory_hits(memories)
        return memories or []
    except Exception as e:
        logger.warning(f"[Giftia] {log_context}失败: {e}")
        return []
