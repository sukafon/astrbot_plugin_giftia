def build_xml_instructions(enabled_features: list[str] | None) -> str:
    """
    根据配置启用的内置交互功能列表，动态生成硬编码的 XML 提示词和交互规范说明。
    """
    def is_enabled(feature_name: str) -> bool:
        if enabled_features is None:
            # 默认除 leave (退群) 之外全部开启
            return feature_name != "leave"
        return any(f.startswith(feature_name) for f in enabled_features)

    # 1. 基础消息格式与输出规范 (永远启用)
    prompt_lines = [
        "# 交互规范与输出格式 (XML 格式)",
        "你的回复包含多个并列的 XML 格式标签。以下是基本消息标签的说明：",
        "",
        "## 必须包含的基础标签",
        "1. **`<status>`**: 必须放在回复的最前面输出，用来记录你当前的状态属性。格式必须如下，不可缺失字段：",
        "   <status>",
        "   心情: \"心情文字\"",
        "   状态: \"当前状态\"",
        "   思考: \"当前思考的过程与碎碎念\"",
        "   动作: \"当前进行的动作或表情动作\"",
        "   能量: 0-100 之间的数值（回复后会扣减或恢复）",
        "   </status>",
        "2. **`<message>`**: 所有的文本回复、说话台词必须写入 `<message>` 标签内。单条回复若句子较长，可以使用多个并列的 `<message>` 标签分段输出（通常不超过 3 段）。",
        "   - **引用回复**: 如果你想回复/引用某条特定消息，可以加上 `quote` 属性，形如：`<message quote=\"消息ID\">回复内容</message>`。",
        "3. **`<at>`**: 如果需要 @ 提及某个群友，在 `<message>` 标签内部或外部输出 `<at user_id=\"用户ID\"/>`。请勿高频或无意义地频繁使用。"
    ]

    # 2. 动态生成的互动标签说明
    interactive_lines = []

    if is_enabled("poke"):
        interactive_lines.append("- **戳一戳**: `<poke user_id=\"用户ID\"/>`。常用于叫人、打招呼或引起注意。")

    if is_enabled("emoji_like"):
        interactive_lines.append(
            "- **贴表情回应**: `<emoji_like message_id=\"消息ID\" emoji_id=\"表情ID\"/>`。在特定消息上贴一个微表情作为互动反馈。\n"
            "  * 常用表情 ID：424 (太对了/赞同), 10068 (问号/抽象), 264 (捂脸/笑哭), 128560 (紧张/恶心/超前), 265 (辣眼睛), 76 (赞), 123 (NO/不赞同), 128557 (大哭), 49 (拥抱/安慰), 66 (爱心)。"
        )

    if is_enabled("like"):
        interactive_lines.append("- **点赞名片**: `<like user_id=\"用户ID\" count=\"点赞次数(1-50)\"/>`。为对方的名片点赞，表达好意。")

    if is_enabled("delete"):
        interactive_lines.append("- **撤回消息**: `<delete message_id=\"消息ID\"/>`。撤回发送过的那条消息（非管理员仅能撤回 2 分钟内自身的消息）。")

    if is_enabled("sticker"):
        interactive_lines.append(
            "- **发送与收集表情包**:\n"
            "  * **发送**: `<sticker sticker_id=\"表情包ID\"/>`。发送符合当前心情的可爱表情包。可以与文本消息共存，也可以单独只发表情包。\n"
            "  * **收集**: `<add_sticker media_id=\"图片ID\"/>`。当你看到群友发了极其可爱、有趣或与你高度相关的图片时，使用此标签将它收录到自己的表情包库中。"
        )

    if is_enabled("group_admin"):
        interactive_lines.append(
            "- **群管指令（群管专用，请仅在必要且拥有管理员身份时合理使用）**:\n"
            "  * **禁言**: `<ban user_id=\"用户ID\" duration=\"禁言秒数\"/>`。\n"
            "  * **踢人**: `<kick user_id=\"用户ID\"/>`。"
        )

    if is_enabled("schedule_task"):
        interactive_lines.append(
            "- **定时任务**:\n"
            "  * **添加任务**: `<schedule_task time=\"cron表达式或ISO8601时间\">提醒内容</schedule_task>`。注意：添加前需要检查上下文，避免重复添加相同的提醒。\n"
            "  * **删除任务**: `<delete_task task_id=\"任务ID\"/>`。如果要修改/合并定时提醒，必须先删除旧任务，再添加新任务。\n"
            "  * **查询任务**: `<all_task group_id=\"群号，留空默认当前群聊\"/>`。列出当前已注册的定时任务。"
        )

    if is_enabled("memory_query_delete"):
        interactive_lines.append(
            "- **记忆检索与删除**:\n"
            "  * **检索记忆**: `<search_memory>检索问题</search_memory>`。用于模糊检索你的长期记忆，回答与过往回忆、约定等相关的内容。\n"
            "  * **删除记忆**: `<delete_memory id=\"记忆ID\"/>`。用于清理失效或不准确的记忆。"
        )

    if is_enabled("leave"):
        interactive_lines.append("- **退群**: `<leave/>`。退出当前群聊（请极度谨慎使用）。")

    if interactive_lines:
        prompt_lines.append("")
        prompt_lines.append("## 可用的可选互动与功能标签")
        prompt_lines.append("你可以根据上下文需要，在回复中输出以下标签来实现特殊互动功能：")
        prompt_lines.extend(interactive_lines)

    # 3. 输出格式示例与提示
    prompt_lines.extend([
        "",
        "## 输出格式示例",
        "一个典型的 XML 输出结构应该如下：",
        "```xml",
        "<status>",
        "  心情: \"开心\"",
        "  状态: \"正在闲聊\"",
        "  思考: \"今天天气真好！\"",
        "  动作: \"微笑\"",
        "  能量: 95",
        "</status>",
        "<message>哼哼，今天也要元气满满哦！</message>",
        "<emoji_like message_id=\"msg-12345\" emoji_id=\"424\"/>",
        "```",
        "注意：如果不想回复或无需回复，可以仅输出 `<status>` 以及一个包含空文本或省略的结构（甚至空回复拦截会在无变动时生效）。所有 XML 格式必须严格闭合，不允许有悬空的半截标签。回复台词中不要夹杂 Markdown（如粗体、代码块等）或 LaTeX、标点双引号等规范，遵循角色特有人设提示词中的具体要求。"
    ])

    return "\n".join(prompt_lines)


DEFAULT_IMAGE_CAPTION_PROMPT = """# 请识别图片的以下信息：
- genre：你认为合适的图片分类，例如“屏幕截图”“表情包”“插画”“漫画”“游戏图片”“写真”“风景照”等，若无法确定则为 ""。
- character：识别图片中的角色名称，若无法确定则为 ""。
- source：图片所属的作品名、地点名字，若无法确定则为 ""。
- text：识别图片中的文字内容，不限字数，若无或无法识别则为 ""。
- caption：从多角度描述图片内容，包括图片中的角色形象、场景、氛围、情感等，一段话纯文本，不超过 120 个字。

# 输出格式
必须输出为合法的 JSON 对象，且不要输出任何 Markdown 标记以外的解释性文字。格式如下：
{
  "genre": "图片分类",
  "character": "角色名称",
  "source": "作品/来源名",
  "text": "图片内识别出的文字",
  "caption": "图片详细描述"
}"""


DEFAULT_AUDIO_CAPTION_PROMPT = """# 请识别音频的以下信息：
- genre：语音对话、直播语音、声优台词、音乐，若无法确定则为 ""。
- character：如果为非语音对话，识别出具体主播或者声优名字，包括视频主播和虚拟主播以及游戏、动漫CV等，若无法确定则为 ""。
- source：如果是声优台词，请识别出作品名；如果是音乐，请识别出音乐名称，若无法确定则为 ""。
- text：识别语音的文字内容，若无或无法识别则为 ""。
- caption：从多角度描述音频内容，包括音频的氛围、情感、场景等，一段话纯文本，不超过 120 个字。

# 输出格式
必须输出为合法的 JSON 对象，且不要输出任何 Markdown 标记以外的解释性文字。格式如下：
{
  "genre": "音频分类",
  "character": "主播/声优名字",
  "source": "作品/音乐名称",
  "text": "语音转写的文字内容",
  "caption": "音频多角度描述"
}"""
