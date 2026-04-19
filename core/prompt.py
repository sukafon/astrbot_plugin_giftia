import inspect
from datetime import datetime
from xml.sax.saxutils import escape, quoteattr

from .schemas import MediaCaption, MessageData, Status

# 构造消息的XML标签的属性，属性按顺序添加。MessageData对象的属性若不在这或者值为空，将不添加该属性
MSG_PROPS = [
    "nickname",
    "user_id",
    "time",
    "message_id",
]  # 由于没有做撤回监听，is_recalled暂时不加入
# 操作同上
CAPTION_PROPS = ["genre", "character", "source", "text", "caption"]


def build_decision_prompt(
    user_id: str,
    recent_messages: list[MessageData],
    current_message: MessageData,
    bot_status: Status,
    user_profile: str | None = None,
    group_profile: str | None = None,
) -> str:
    user_prompt = []
    # 时间
    user_prompt.append(
        f"<time>{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M (UTC%z, %A)')}</time>"
    )
    # 机器人状态
    if bot_status:
        user_prompt.append(f"<status>\n{parse_status_to_str(bot_status)}\n</status>")
    # 群画像
    if group_profile:
        user_prompt.append(f"<group_profile>\n{group_profile}\n</group_profile>")
    # 用户画像
    if user_profile:
        user_prompt.append(
            f"<user_profile user_id={user_id}>\n{user_profile}\n</user_profile>"
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
    group_data: str = "",
    user_id: str = "",
    current_message: MessageData | None = None,
    remind_message: str | None = None,
    tool_results: list[dict[str, str]] | None = None,
    rag_memories: list[str] | None = None,
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
    if user_profile:
        user_prompt.append(
            f"<user_profile user_id={user_id}>\n{user_profile}\n</user_profile>"
        )
    # 媒体转述
    if media_captions:
        media_captions_block = "\n".join(
            parse_caption_to_str(caption) for caption in media_captions
        )
        user_prompt.append(f"<media_caption>\n{media_captions_block}\n</media_caption>")
    # 提醒消息
    if remind_message:
        user_prompt.append(f"<remind_message>{remind_message}</remind_message>")
    # RAG记忆
    if rag_memories:
        user_prompt.append(f"<rag_memories>\n{rag_memories}\n</rag_memories>")
    # 工具结果
    if tool_results:
        user_prompt.append(f"<tool_results>\n{tool_results}\n</tool_results>")
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


def parse_message_to_str(message: MessageData) -> str:
    """构建xml格式的消息，用于提示词传递聊天记录"""
    props = ""
    for prop in MSG_PROPS:
        value = getattr(message, prop, "")
        if value is not None and value != "":
            # 如果是时间，格式化成 时:分:秒
            if prop == "time":
                value = format_time_to_hhmmss(value)
            props += f" {prop}={quoteattr(str(value))}"
    safe_content = escape(str(message.content))
    return f"<message{props}>{safe_content}</message>"


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
    return inspect.cleandoc(f"""<mood>{escape(str(status.mood))}</mood>
<state>{escape(str(status.state))}</state>
<memory>{escape(str(status.memory))}</memory>
<action>{escape(str(status.action))}</action>
<energy>{escape(str(status.energy))}</energy>""")


def build_rag_results(rag_memories: list[str]):
    """构建RAG提示词，用于提示词传递聊天记录"""
    rag_memories_block = "\n".join(rag_memories)
    return inspect.cleandoc(f"""
<rag_memories>
{rag_memories_block}
</rag_memories>
""")


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
