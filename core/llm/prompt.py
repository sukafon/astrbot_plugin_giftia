from datetime import datetime
from xml.sax.saxutils import quoteattr

from ..utils.schemas import MediaCaption, MemoryItem, MessageData, Status

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


def build_decision_prompt(
    user_id: str,
    group_data: str,
    recent_messages: list[MessageData],
    current_message: MessageData,
    bot_status: Status,
    user_relation: tuple[int, str],
    user_profile: str | None = None,
    group_profile: str | None = None,
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
    if group_profile:
        user_prompt.append(f"<group_profile>\n{group_profile}\n</group_profile>")
    # 用户画像
    user_prompt.append(
        f"<user_profile user_id={user_id}>\n{user_profile}\n</user_profile>"
    )
    # 好感度
    user_prompt.append(
        f"<user_relation user_id={user_id}>\n{build_user_relation(user_relation)}\n</user_relation>"
    )
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


def build_reply_prompt(
    recent_messages: list[MessageData],
    media_captions: list[MediaCaption],
    bot_status: Status,
    user_relation: tuple[int, str],
    group_data: str = "",
    user_id: str = "",
    nickname: str = "",
    current_message: MessageData | None = None,
    remind_message: str | None = None,
    tool_results: list[dict[str, str]] | None = None,
    long_memories: list[MemoryItem] | None = None,
    relevant_memories: list[str] | None = None,
    user_profile: str | None = None,
    group_profile: str | None = None,
    other_data: list[str] | None = None,
    bot_sticker: str | None = None,
) -> str:
    user_prompt = []
    # 时间
    user_prompt.append(
        f"<time>{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M (UTC%z, %A)')}</time>"
    )
    # 群数据
    if group_data:
        user_prompt.append(f"<group_data>\n{group_data.strip()}\n</group_data>")
    # 预设提示词
    #     user_prompt.append("""# 请基于以下指示生成回复
    # - 严格遵循角色设定进行扮演
    # - 综合分析上下文，结合角色知识和状态生成回复""")
    # 群画像
    if group_profile:
        user_prompt.append(f"<group_profile>\n{group_profile}\n</group_profile>")
    # 用户画像
    user_prompt.append(
        f"<user_profile user_id={user_id}>\n{user_profile}\n</user_profile>"
    )
    # 好感度
    user_prompt.append(
        f"<user_relation user_id={user_id}>\n{build_user_relation(user_relation)}\n</user_relation>"
    )
    # 长期记忆
    if long_memories:
        user_prompt.append(
            f"<long_memories>\n{build_long_memories(long_memories)}\n</long_memories>"
        )
    # 媒体转述
    if media_captions:
        media_captions_block = "\n".join(
            parse_caption_to_str(caption) for caption in media_captions
        )
        user_prompt.append(f"<media_content>\n{media_captions_block}\n</media_content>")
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

    #     user_prompt.append("""# 请按以下格式输出，包含空行
    # <status>
    # 更新后的状态
    # </status>

    # <other_tags...>

    # <message>消息内容</message>

    # # 提示
    # - 综合分析上下文，结合角色知识和状态生成回复
    # - 请一次性输出所有需要的操作，除非需要分步调用获取信息
    # """)

    #     user_prompt.append("""# 提示
    # - 并非每一条消息都需要回复：
    #   - 有时候引用你的消息的人只是在跟别人说话，这种情况下不要回复
    #   - 当一条消息已经过去十分钟以上时，最好不回复，因为已经错过最佳时机
    # - 请在输出前仔细思考：
    #   - 你需要哪些信息？你是否记得它们？如果不记得，仔细遍历**整个聊天记录**去找到它们。
    #   - 你的输出是否符合**所有的要求、设定**？如果不符合，需要修正。
    #   - 完成后，才能输出正式的结果。""")

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


def build_user_relation(relation: tuple[int, str]) -> str:
    text = [f"好感度：{relation[0] if relation else 0}"]
    if relation[1]:
        text.append(f"称号：{relation[1]}")
    return "\n".join(text)


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
