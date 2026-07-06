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
        "## 基础标签",
        "1. **`<status>`**: 必须放在回复的最前面输出，用来记录你当前的状态属性。格式必须如下，不可缺失字段：",
        "   <status>",
        '   心情: "心情文字"',
        '   状态: "当前状态"',
        '   思考: "当前思考的过程与碎碎念"',
        '   动作: "当前进行的动作或表情动作"',
        "   能量: 0-100 之间的数值（回复后会扣减或恢复）",
        "   </status>",
        "2. **`<message>`**: 所有的文本回复、说话台词必须写入 `<message>` 标签内。单条回复若句子较长，可以使用多个并列的 `<message>` 标签分段输出（通常不超过 3 段）。",
        '   - **引用回复**: 如果你想回复/引用某条特定消息，可以加上 `quote` 属性，形如：`<message quote="消息ID">回复内容</message>`。',
        '3. **`<at>`**: 如果需要 @ 提及某个群友，在 `<message>` 标签内部或外部输出 `<at user_id="用户ID"/>`。请勿高频或无意义地频繁使用。',
    ]

    # 2. 动态生成的互动标签说明
    interactive_lines = []

    if is_enabled("poke"):
        interactive_lines.append(
            '- **戳一戳**: `<poke user_id="用户ID"/>`。常用于叫人、打招呼或引起注意。'
        )

    if is_enabled("emoji_like"):
        interactive_lines.append(
            '- **贴表情回应**: `<emoji_like message_id="消息ID" emoji_id="表情ID"/>`。在特定消息上贴一个微表情作为互动反馈。\n'
            "  * 常用表情 ID：424 (太对了/赞同), 10068 (问号/抽象), 264 (捂脸/笑哭), 128560 (紧张/恶心/超前), 265 (辣眼睛), 76 (赞), 123 (NO/不赞同), 128557 (大哭), 49 (拥抱/安慰), 66 (爱心)。"
        )

    if is_enabled("repeat"):
        interactive_lines.append(
            '- **消息复读**: `<repeat message_id="消息ID"/>`。当你想原样复读某条近期群友消息时使用，如：表情、文字、语音等。'
        )

    if is_enabled("like"):
        interactive_lines.append(
            '- **点赞名片**: `<like user_id="用户ID" count="点赞次数(1-50)"/>`。为对方的名片点赞，表达好意。'
        )

    if is_enabled("delete"):
        interactive_lines.append(
            '- **撤回消息**: `<delete message_id="消息ID"/>`。撤回发送过的那条消息（非管理员仅能撤回 2 分钟内自身的消息）。'
        )

    if is_enabled("sticker"):
        interactive_lines.append(
            "- **发送与收集表情包**:\n"
            '  * **发送**: `<sticker sticker_id="表情包ID"/>`。发送符合当前心情的可爱表情包。可以与文本消息共存，也可以单独只发表情包。\n'
            '  * **收集**: `<add_sticker media_id="图片ID"/>`。当你看到群友发了极其可爱、有趣或与你高度相关的图片时，使用此标签将它收录到自己的表情包库中。'
        )

    if is_enabled("group_admin"):
        interactive_lines.append(
            "- **群管指令（群管专用，请仅在必要且拥有管理员身份时合理使用）**:\n"
            '  * **禁言**: `<ban user_id="用户ID" duration="禁言秒数"/>`。\n'
            '  * **踢人**: `<kick user_id="用户ID"/>`。'
        )

    if is_enabled("schedule_task"):
        interactive_lines.append(
            "- **定时任务**:\n"
            '  * **添加任务**: `<schedule_task time="cron表达式或ISO8601时间">提醒内容</schedule_task>`。注意：添加前需要检查上下文，避免重复添加相同的提醒。\n'
            '  * **删除任务**: `<delete_task task_id="任务ID"/>`。如果要修改/合并定时提醒，必须先删除旧任务，再添加新任务。\n'
            '  * **查询任务**: `<all_task group_id="群号，留空默认当前群聊"/>`。列出当前已注册的定时任务。'
        )

    if is_enabled("task_board"):
        interactive_lines.append(
            "- **短期任务看板**:\n"
            '  * **创建任务**: `<task_board action="create">一句自然语言任务</task_board>`。用于记录短期待办，例如“下次看到 123456 时提醒他交作业”。任务数量达到会话上限时系统会拒绝创建。\n'
            '  * **完成任务**: `<task_board action="complete" task_id="任务ID">完成原因</task_board>`。当你已经按任务要求完成提醒、确认或处理后使用。\n'
            '  * **取消任务**: `<task_board action="cancel" task_id="任务ID">取消原因</task_board>`。当用户明确要求取消，或任务已经不再需要时使用。'
        )

    if is_enabled("memory_query_delete"):
        interactive_lines.append(
            "- **记忆检索与删除**:\n"
            '  * **检索记忆**: `<search_memory>检索问题</search_memory>`。用于模糊检索你的长期记忆，回答与过往回忆、约定等相关的内容。\n'
            '  * **删除记忆**: `<delete_memory id="记忆ID"/>`。用于清理失效或不准确的记忆。'
        )

    if is_enabled("leave"):
        interactive_lines.append(
            "- **退群**: `<leave/>`。退出当前群聊（请极度谨慎使用）。"
        )

    if interactive_lines:
        prompt_lines.append("")
        prompt_lines.append("## 可用的可选互动与功能标签")
        prompt_lines.append(
            "你可以根据上下文需要，在回复中输出以下标签来实现特殊互动功能："
        )
        prompt_lines.extend(interactive_lines)

    # 3. 输出格式示例与提示
    prompt_lines.extend(
        [
            "",
            "## 输出格式示例",
            "一个典型的 XML 输出结构应该如下：",
            "```xml",
            "<status>",
            '  心情: "开心"',
            '  状态: "正在闲聊"',
            '  思考: "今天天气真好！"',
            '  动作: "微笑"',
            "  能量: 95",
            "</status>",
            "<message>哼哼，今天也要元气满满哦！</message>",
            '<emoji_like message_id="msg-12345" emoji_id="424"/>',
            "```",
            "注意：如果不想回复或无需回复，可以仅输出 `<status>` 以及一个包含空文本或省略的结构（甚至空回复拦截会在无变动时生效）。所有 XML 格式必须严格闭合，不允许有悬空的半截标签。回复台词中不要夹杂 Markdown（如粗体、代码块等）或 LaTeX、标点双引号等规范，遵循角色特有人设提示词中的具体要求。",
        ]
    )

    return "\n".join(prompt_lines)


DEFAULT_PASSIVE_MEMORY_SUMMARY_PROMPT = """# 角色与目标
你是一个长期记忆提炼器。你需要分析以下群聊片段，只总结与机器人自身（昵称：{nickname}，ID：{self_id}）直接相关、未来值得召回的事件记忆。

# 提炼规则
- 只记录机器人参与、被提及、有互动的有价值事件，例如约定、承诺、共同经历、明确偏好或重要结论。
- 每条记忆必须使用第一人称，从机器人的角度描述。
- 每条记忆控制在 50 字以内，避免流水账和情绪泛化。
- 必须使用 `users` 属性指出该记忆直接关联的群友 user_id，多用户用逗号分隔。
- 与特定人无关但对机器人有意义时，可以省略 `users` 属性。

# 输出格式
请只输出 `<memory>` 标签：
`<memory users="12345">小明约我周末一起打游戏，我答应提醒他。</memory>`

如果没有值得记录的长期记忆，请只输出：
`<memory>无</memory>`"""


DEFAULT_PASSIVE_PROFILE_SUMMARY_PROMPT = """# 角色与目标
你是一个关系画像维护器。你需要分析以下群聊片段，结合已有画像，维护称呼、外号、互动态度、关键约定、群画像、好感度和关系头衔。

# 提供的现有状态
- <current_user_profiles>：当前活跃成员的结构化画像、旧画像参考和关系头衔；不会提供具体好感度分数。
- <current_group_profile>：当前群聊的现有画像。

# 用户画像更新
如果发现某位用户的新称呼、新外号、与你的互动状态或与你达成的约定，请结合现有画像，输出该用户需要更新的字段。

用户画像字段说明：
- call_name：你对该成员的称呼。
- aliases：本段聊天中新观察到的其他群友对该成员的称呼或外号；只输出新增观察，不要重写完整外号列表。
- attitude：该成员对你（即机器人，昵称：{nickname}）的稳定态度。必须描述其对待你的长期情感或互动基调，严禁描述该成员对待其他群友的态度、本段聊天的即时群内纠纷或与你无关的第三方互动。
- agreements：与你达成的承诺或共同回忆。

只输出需要更新的字段，不要为无变化字段输出标签。除 `aliases` 外，用户画像字段采用字段级覆盖更新：如果输出某个字段，该字段会替换旧值；因此请结合已有画像输出该字段的最新完整摘要，而不是只写新增片段。未输出的字段会保留旧值。每个字段精炼在一句话、30 字以内。`aliases` 可用逗号分隔多个本段新观察到的外号，后端会累计统计。关系头衔如果没有变化可以省略 `title` 属性。

# 字段更新准则与注意事项（极其重要）
1. **第二人称视角**：`attitude` 描述的必须是该成员对**你（{nickname}）**的态度，而不是对其他群友的态度。
2. **事实去处**：如果发现了具体的即时约定、承诺、共同回忆或发生的重要事件，请将其记录在 `agreements`，不要混入 `attitude`。

格式：
`<summary_user_profile user_id="12345" title="挚友">
<call_name>小草莓</call_name>
<aliases>草莓酱</aliases>
<attitude>经常主动调侃我</attitude>
<agreements>周末一起打游戏</agreements>
<update_relation delta="+2">主动表达感谢并延续互动</update_relation>
</summary_user_profile>`

# 好感度变化
如果本段聊天明确改变了某位用户与你的关系，请在该用户的 `<summary_user_profile>` 内输出 `<update_relation>`，记录本段互动造成的好感度增量，而不是最新总分。普通闲聊、正常回应、无明确情绪变化时不要输出好感度变化。

`delta` 必须是带正负号的整数，表示本段互动导致的变化值；轻微变化通常为 -2 到 +2，明显正负事件通常为 -5 到 +5，极端事件才使用更大的变化。好感度变化必须写在对应用户的 `<summary_user_profile>` 内，不能作为独立顶层标签输出。如果某位用户只有好感度变化、没有其他画像字段变化，也可以只输出包含 `<update_relation>` 的用户画像块。

# 群画像更新
如果发现群聊的新特征，请结合现有群画像，输出最新完整群画像：
- 群聊主题：<群聊定位与核心主题>
- 氛围特征：<群内氛围与活跃特征>
- 成员关系：<核心成员互动关系，50 字以内>
- 核心规则与忌讳：<群规、敏感点或忌讳>

格式：
`<summary_group_profile>
- 群聊主题：游戏讨论与日常吹水
- 氛围特征：气氛轻松，经常开玩笑
- 成员关系：流萤与爱丽丝关系亲密
- 核心规则与忌讳：禁止刷屏和恶意复读
</summary_group_profile>`

# 输出要求
只输出需要更新的 XML 标签。如果没有任何画像或关系需要更新，请只输出：
`<profile>无</profile>`"""


DEFAULT_PASSIVE_LONG_PROFILE_SUMMARY_PROMPT = """# 角色与目标
你是一个用户画像维护器。你只分析一个用户自己的新增发言样本，并结合该用户已有画像，维护以下用户画像字段：
- personality：性格风格与长期表达习惯。
- interests：长期稳定的兴趣话题与关注领域。
- extra：无法归入其他字段、但长期有助于理解该用户的信息。

# 输入说明
- <current_user_profile>：该用户当前完整画像，包含结构化画像、旧画像参考、好感度和关系头衔。
- <user_messages>：该用户最近一批新增发言样本，只包含该用户自己的文本、媒体占位和媒体转述。
- <media_content>：未内联到消息中的唯一媒体转述。重复媒体不会重复展开，但消息中会保留媒体 ID 引用。

# 更新规则
1. 只根据长期、稳定、有多条样本支撑的信息更新画像。
2. 不要因为单次情绪、一次性话题、临时玩笑或孤立事件更新字段。
3. 媒体转述、语音转写、图片/视频描述、文件摘要都代表用户发送内容，应正常参考。
4. 重复表情包/重复媒体的出现频率可作为表达风格参考，但不要把媒体 ID 或单次发送事件写入画像。
5. 如果新样本不足以支持某个字段变化，不要输出该字段的 XML 块。
6. 字段采用覆盖更新：一旦输出某个字段，必须结合旧画像写成该字段的最新完整摘要，而不是只写新增片段。
7. 每个字段尽量一句话，控制在 30 字以内；extra 最多 3 条，每条 30 字以内。

# 输出格式
只输出 `<long_user_profile>` 标签，且只包含确实需要更新的字段：
`<long_user_profile>
<personality>表达直接，常从实现复杂度和稳定性角度思考问题。</personality>
<interests>关注用户画像、长期记忆、异步任务调度和 LLM 插件设计。</interests>
<extra>正在迭代 AstrBot 插件的画像系统。</extra>
</long_user_profile>`

如果没有任何字段需要更新，请只输出：
`<long_user_profile>无</long_user_profile>`"""


DEFAULT_IMAGE_CAPTION_PROMPT = """# 请识别图片的以下信息：
- genre：你认为合适的图片分类，例如“屏幕截图”“表情包”“插画”“漫画”“游戏图片”“写真”“风景照”等，若无法确定则为 ""。
- character：识别图片中的角色名称，若无法确定则为 ""。
- source：图片所属的作品名、地点名字，若无法确定则为 ""。
- text：识别图片中的文字内容，不限字数，若无或无法识别则为 ""。
- caption：从多角度描述图片内容，包括图片中的角色形象、场景、氛围、情感等，一段话纯文本，不超过 120 个字。

# 输出格式
必须输出为合法的 JSON 对象。请严格遵守以下规则：
1. 不要输出任何 Markdown 标记（例如 ```json ```）以外的解释性、前导或后随文字。
2. 绝对不能在 Markdown 代码块首行（```json）后添加任何额外的语言标记、引用信息或非标准描述。
3. JSON 键值对中的所有字符串值内，绝对不得包含未转义的双引号（"）。如果字符串内部需要表达双引号，请改用中文双引号（“ ”）或者对其进行转义（写成 \\"）。

格式如下：
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
必须输出为合法的 JSON 对象。请严格遵守以下规则：
1. 不要输出任何 Markdown 标记（例如 ```json ```）以外的解释性、前导或后随文字。
2. 绝对不能在 Markdown 代码块首行（```json）后添加任何额外的语言标记、引用信息或非标准描述。
3. JSON 键值对中的所有字符串值内，绝对不得包含未转义的双引号（"）。如果字符串内部需要表达双引号，请改用中文双引号（“ ”）或者对其进行转义（写成 \\"）。

格式如下：
{
  "genre": "音频分类",
  "character": "主播/声优名字",
  "source": "作品/音乐名称",
  "text": "语音转写的文字内容",
  "caption": "音频多角度描述"
}"""
