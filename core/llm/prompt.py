from datetime import datetime
from xml.sax.saxutils import escape, quoteattr

from ..utils.schemas import MediaCaption, MemoryItem, MessageData, ShortTask, Status

# 构造消息的XML标签的属性，属性按顺序添加。MessageData对象的属性若不在这或者值为空，将不添加该属性
MSG_PROPS = [
    "nickname",
    "user_id",
    "time",
    "message_id",
    "is_recalled",
]
# 操作同上
CAPTION_PROPS = ["genre", "character", "source", "text", "caption"]
USER_PROFILE_FIELDS = [
    ("call_name", "你的称呼"),
    ("aliases", "其他外号"),
    ("personality", "性格风格"),
    ("interests", "兴趣话题"),
    ("attitude", "互动态度"),
    ("agreements", "关键约定"),
    ("extra", "其他补充"),
]


def build_decision_prompt(
    user_id: str,
    group_data: str,
    recent_messages: list[MessageData],
    current_message: MessageData,
    bot_status: Status,
    user_relation: tuple[int, str] | None = None,
    user_profile: str | dict | None = None,
    group_profile: str | None = None,
    active_user_briefs: list[dict] | None = None,
    short_tasks: list[ShortTask] | None = None,
    short_task_limit: int = 3,
) -> str:
    user_prompt = []
    # 时间
    user_prompt.append(
        f"<time>{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M (UTC%z, %A)')}</time>"
    )
    # 群数据
    if group_data:
        user_prompt.append(f"<group_data>\n{group_data.strip()}\n</group_data>")
    # 机器人状态
    if bot_status:
        user_prompt.append(f"<status>\n{parse_status_to_str(bot_status)}\n</status>")
    # 群画像
    group_profile_text = normalize_profile_text(group_profile)
    if group_profile_text:
        user_prompt.append(f"<group_profile>\n{group_profile_text}\n</group_profile>")
    # 用户画像
    user_profile_block = build_user_profile_block(
        user_id=user_id,
        user_profile=user_profile,
        user_relation=user_relation,
    )
    if user_profile_block:
        user_prompt.append(user_profile_block)
    # 窗口内其他活跃用户摘要
    active_user_briefs_block = build_active_user_briefs(active_user_briefs)
    if active_user_briefs_block:
        user_prompt.append(active_user_briefs_block)
    # 短期任务看板
    task_board_block = build_short_task_board(short_tasks, short_task_limit)
    if task_board_block:
        user_prompt.append(task_board_block)
    # 近期消息
    if recent_messages:
        recent_messages_str = "\n".join(
            parse_message_to_str(msg) for msg in recent_messages
        )
        user_prompt.append(
            f"<recent_messages>\n{recent_messages_str}\n</recent_messages>"
        )
    # 当前消息
    if current_message:
        user_prompt.append(
            f"<current_message>\n{parse_message_to_str(current_message)}\n</current_message>"
        )

    return "\n\n".join(user_prompt)


def process_media_captions_for_prompt(
    messages: list[MessageData],
    media_captions: list[MediaCaption],
    threshold: int = 100,
) -> tuple[list[MessageData], list[MediaCaption]]:
    """
    分析消息列表与媒体转述，进行内联判定与替换。
    返回: (修改后的消息副本列表, 剩余未内联的媒体转述列表)
    """
    import copy
    from collections import Counter

    copied_messages = [copy.copy(msg) for msg in messages] if messages else []

    # 统计所有消息中媒体 ID 的出现频次
    hash_counts = Counter()
    for msg in copied_messages:
        if msg.media_id_list:
            hash_counts.update(msg.media_id_list)

    # 建立 hash_val -> MediaCaption 映射
    caption_map = {}
    if media_captions:
        for c in media_captions:
            if c and c.hash_val:
                caption_map[c.hash_val] = c

    # 过滤和替换逻辑
    inline_hashes = set()
    for hash_val, caption in caption_map.items():
        # 计算非空描述性字段的总长度
        raw_text = "".join(
            getattr(caption, f, "")
            for f in ["genre", "character", "source", "text", "caption"]
            if getattr(caption, f, "")
        )
        # 只有在非空描述长度在 (0, threshold] 之间，且频次为 1 时才内联
        if 0 < len(raw_text) <= threshold and hash_counts[hash_val] == 1:
            inline_hashes.add(hash_val)

    # 对可内联媒体进行内容格式化和占位符替换
    def format_inline_caption(c: MediaCaption) -> str:
        parts = []
        if c.caption:
            parts.append(f"画面: {c.caption}")
        if c.text:
            parts.append(f"文字: {c.text}")
        if c.genre:
            parts.append(f"类型: {c.genre}")
        if c.character:
            parts.append(f"人物: {c.character}")
        if c.source:
            parts.append(f"来源: {c.source}")
        return "; ".join(parts)

    for msg in copied_messages:
        if msg.media_id_list:
            for hash_val in msg.media_id_list:
                if hash_val in inline_hashes:
                    formatted = format_inline_caption(caption_map[hash_val])
                    msg.content = msg.content.replace(
                        f"[图片:{hash_val}]", f"[图片: {formatted}]"
                    )
                    msg.content = msg.content.replace(
                        f"[语音:{hash_val}]", f"[语音: {formatted}]"
                    )
                elif hash_val not in caption_map:
                    # 缺失的媒体替换为 generic 占位符
                    msg.content = msg.content.replace(f"[图片:{hash_val}]", "[图片]")
                    msg.content = msg.content.replace(f"[语音:{hash_val}]", "[语音]")

    # 剩余未内联的媒体
    remaining_captions = []
    if media_captions:
        for caption in media_captions:
            if caption and caption.hash_val not in inline_hashes:
                remaining_captions.append(caption)

    return copied_messages, remaining_captions


def build_reply_prompt(
    recent_messages: list[MessageData],
    media_captions: list[MediaCaption],
    bot_status: Status,
    user_relation: tuple[int, str] | None = None,
    group_data: str = "",
    user_id: str = "",
    nickname: str = "",
    current_message: MessageData | None = None,
    remind_message: str | None = None,
    tool_results: list[dict[str, str]] | None = None,
    long_memories: list[MemoryItem] | None = None,
    relevant_memories: list[str] | None = None,
    user_profile: str | dict | None = None,
    group_profile: str | None = None,
    active_user_briefs: list[dict] | None = None,
    short_tasks: list[ShortTask] | None = None,
    short_task_limit: int = 3,
    other_data: list[str] | None = None,
    bot_sticker: str | None = None,
) -> str:
    # 合并近期消息与当前消息进行统一的频次与内联处理
    all_messages = []
    if recent_messages:
        all_messages.extend(recent_messages)
    if current_message:
        all_messages.append(current_message)

    processed_messages, remaining_captions = process_media_captions_for_prompt(
        messages=all_messages,
        media_captions=media_captions,
        threshold=100,
    )

    # 拆分回 recent_messages 和 current_message
    copied_recent = (
        processed_messages[: len(recent_messages)] if recent_messages else []
    )
    copied_current = (
        processed_messages[len(recent_messages) :][0] if current_message else None
    )

    user_prompt = []
    # 时间
    user_prompt.append(
        f"<time>{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M (UTC%z, %A)')}</time>"
    )
    # 群数据
    if group_data:
        user_prompt.append(f"<group_data>\n{group_data.strip()}\n</group_data>")
    # 群画像
    group_profile_text = normalize_profile_text(group_profile)
    if group_profile_text:
        user_prompt.append(f"<group_profile>\n{group_profile_text}\n</group_profile>")
    # 用户画像
    user_profile_block = build_user_profile_block(
        user_id=user_id,
        user_profile=user_profile,
        user_relation=user_relation,
    )
    if user_profile_block:
        user_prompt.append(user_profile_block)
    # 窗口内其他活跃用户摘要
    active_user_briefs_block = build_active_user_briefs(active_user_briefs)
    if active_user_briefs_block:
        user_prompt.append(active_user_briefs_block)
    # 短期任务看板
    task_board_block = build_short_task_board(short_tasks, short_task_limit)
    if task_board_block:
        user_prompt.append(task_board_block)
    # 长期记忆
    if long_memories:
        user_prompt.append(
            f"<long_memories>\n{build_long_memories(long_memories)}\n</long_memories>"
        )
    # 媒体转述
    if remaining_captions:
        media_captions_block = "\n".join(
            parse_caption_to_str(caption) for caption in remaining_captions
        )
        user_prompt.append(f"<media_content>\n{media_captions_block}\n</media_content>")
    # 近期消息
    if copied_recent:
        recent_messages_str = "\n".join(
            parse_message_to_str(msg) for msg in copied_recent
        )
        user_prompt.append(
            f"<recent_messages>\n{recent_messages_str}\n</recent_messages>"
        )
    # 当前消息
    if copied_current:
        user_prompt.append(
            f"<current_message>\n{parse_message_to_str(copied_current)}\n</current_message>"
        )
    # 机器人状态
    if bot_status:
        user_prompt.append(f"<status>\n{parse_status_to_str(bot_status)}\n</status>")
    # 表情包
    if bot_sticker:
        user_prompt.append(f"<stickers>\n{bot_sticker}\n</stickers>")
    # 相关记忆
    if relevant_memories:
        user_prompt.append(
            f"<relevant_memories>\n{build_rag_results(relevant_memories)}\n</relevant_memories>"
        )
    # 提醒消息
    if remind_message:
        user_prompt.append(f"<remind_message>\n{remind_message}\n</remind_message>")
    # 工具结果
    if tool_results:
        user_prompt.append(f"<tool_results>\n{tool_results}\n</tool_results>")
    # 其他数据
    if other_data:
        user_prompt.append("\n\n".join(other_data))

    return "\n\n".join(user_prompt)


def parse_message_to_str(message: MessageData) -> str:
    """构建xml格式的消息，用于提示词传递聊天记录"""
    if getattr(message, "role", "message") == "operation_log":
        time_str = format_time_to_hhmmss(message.time) if message.time else ""
        return f"<operation_log time={quoteattr(time_str)}>\n{message.content.strip()}\n</operation_log>"

    props = ""
    for prop in MSG_PROPS:
        value = getattr(message, prop, "")
        # 如果是 is_recalled 字段，只有在值为真（非0）时才加入属性
        if prop == "is_recalled" and not value:
            continue

        if value is not None and value != "":
            # 如果是时间，格式化成 时:分:秒
            if prop == "time":
                value = format_time_to_hhmmss(value)
            props += f" {prop}={quoteattr(str(value))}"
    return f"<message{props}>{message.content}</message>"


def parse_caption_to_str(media_caption: MediaCaption) -> str:
    """构建xml格式的图片等媒体的消息，用于提示词传递聊天记录"""
    props = ""
    caption_text = ""
    for prop in CAPTION_PROPS:
        value = getattr(media_caption, prop, "")
        if value is not None and value != "":
            if prop == "caption":
                caption_text = value
                continue
            props += f" {prop}={quoteattr(str(value))}"
    return (
        f'<caption media_id="{media_caption.hash_val}"{props}>{caption_text}</caption>'
    )


def parse_status_to_str(status: Status) -> str:
    """构建xml格式的状态，用于提示词传递聊天记录"""
    try:
        energy_val = float(status.energy.strip().strip('"'))
    except ValueError:
        energy_val = 100
    return f"""心情：{status.mood}
状态：{status.state}
思考：{status.memory}
动作：{status.action}
能量：{energy_val:.0f}"""


def build_long_memories(long_memories: list[MemoryItem]) -> str:
    """构建长期记忆的提示词"""
    xmls = []
    for memory in long_memories:
        xmls.append(f"<memory id={memory.memory_id}>{memory.text}</memory>")
    return "\n".join(xmls)


def build_rag_results(rag_memories: list[str]) -> str:
    """构建RAG提示词，用于提示词传递聊天记录"""
    rag_memories_str = "\n".join(rag_memories)
    return rag_memories_str


def normalize_profile_text(profile: str | None) -> str:
    if not profile:
        return ""

    normalized_lines = []
    empty_values = {"", "无", "暂无", "未知", "none", "null", "n/a", "N/A"}
    for raw_line in str(profile).splitlines():
        line = raw_line.strip()
        if not line or line in empty_values:
            continue

        content = line[1:].strip() if line.startswith(("-", "*")) else line
        separator = "：" if "：" in content else ":" if ":" in content else ""
        if separator:
            _, value = content.split(separator, 1)
            if value.strip() in empty_values:
                continue

        normalized_lines.append(line)

    return "\n".join(normalized_lines)


def normalize_profile_value(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text in {"", "无", "暂无", "未知", "none", "null", "n/a", "N/A"}:
        return ""
    return text


def build_user_relation(relation: tuple[int, str] | None) -> str:
    if not relation:
        return ""

    score = relation[0] if relation[0] is not None else 0
    title = relation[1] or ""
    if score == 0 and not title:
        return ""

    text = []
    if score != 0:
        text.append(f"好感度：{score}")
    if title:
        text.append(f"称号：{title}")
    return "\n".join(text)


def build_user_profile_block(
    user_id: str,
    user_profile: str | dict | None,
    user_relation: tuple[int, str] | None,
) -> str:
    parts = []

    legacy_profile = ""
    if isinstance(user_profile, dict):
        structured_lines = []
        for field, label in USER_PROFILE_FIELDS:
            value = normalize_profile_value(user_profile.get(field))
            if value:
                structured_lines.append(f"{label}：{value}")
        if structured_lines:
            parts.append("\n".join(structured_lines))
        legacy_profile = user_profile.get("profile") or ""
    else:
        legacy_profile = user_profile or ""

    if not parts:
        profile_text = normalize_profile_text(legacy_profile)
        if profile_text:
            parts.append(profile_text)

    relation_text = build_user_relation(user_relation)
    if relation_text:
        parts.append(relation_text)

    if not parts:
        return ""

    content = "\n".join(parts)
    return f"<user_profile user_id={quoteattr(str(user_id))}>\n{content}\n</user_profile>"


def build_active_user_briefs(active_user_briefs: list[dict] | None) -> str:
    if not active_user_briefs:
        return ""

    user_blocks = []
    for item in active_user_briefs:
        user_id = item.get("user_id")
        if not user_id:
            continue

        props = f" user_id={quoteattr(str(user_id))}"
        nickname = normalize_profile_value(item.get("nickname"))
        if nickname:
            props += f" nickname={quoteattr(nickname)}"

        lines = []
        relation = item.get("relation")
        if relation not in (None, "", 0):
            lines.append(f"好感度：{relation}")
        title = normalize_profile_value(item.get("title"))
        if title:
            lines.append(f"关系头衔：{title}")
        call_name = normalize_profile_value(item.get("call_name"))
        if call_name:
            lines.append(f"你的称呼：{call_name}")
        aliases = normalize_profile_value(item.get("aliases"))
        if aliases:
            lines.append(f"其他外号：{aliases}")

        if lines:
            content = "\n".join(lines)
            user_blocks.append(f"<user{props}>\n{content}\n</user>")

    if not user_blocks:
        return ""
    return "<active_user_briefs>\n" + "\n".join(user_blocks) + "\n</active_user_briefs>"


def build_short_task_board(
    short_tasks: list[ShortTask] | None, short_task_limit: int = 3
) -> str:
    if not short_tasks:
        return ""

    task_blocks = []
    for task in short_tasks:
        if not task or task.status != "active":
            continue

        props = f" id={quoteattr(str(task.task_id))}"
        props += f" creator_user_id={quoteattr(str(task.creator_user_id))}"
        if task.creator_nickname:
            props += f" creator_nickname={quoteattr(str(task.creator_nickname))}"
        if task.created_at:
            props += f" created_at={quoteattr(str(task.created_at))}"
        if task.expires_at:
            props += f" expires_at={quoteattr(str(task.expires_at))}"

        task_blocks.append(f"<task{props}>{escape(task.content)}</task>")

    if not task_blocks:
        return ""

    return (
        f"<task_board active_count={quoteattr(str(len(task_blocks)))} "
        f"limit={quoteattr(str(short_task_limit))}>\n"
        + "\n".join(task_blocks)
        + "\n</task_board>"
    )


def format_time_to_hhmmss(db_value: str) -> str:
    if not db_value:
        return ""
    try:
        # 解析 ISO 格式字符串
        dt = datetime.fromisoformat(db_value)
        # 转换成时分秒
        return dt.strftime("%H:%M:%S")
    except ValueError:
        # 兼容处理
        return db_value[11:19] if len(db_value) >= 19 else db_value
