from xxhash import xxh3_64_hexdigest

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context
from astrbot.core.exceptions import EmptyModelOutputError

from ..utils.schemas import (
    Decision,
    MediaCaption,
    Sticker,
    XmlLlmResult,
)
from .json_parse import decode_media_audio_json, decode_media_caption_json
from .preset_prompts import (
    DEFAULT_AUDIO_CAPTION_PROMPT,
    DEFAULT_IMAGE_CAPTION_PROMPT,
    DEFAULT_STICKER_ANALYSIS_PROMPT,
    DEFAULT_DECISION_RULES,
    build_xml_instructions,
)
from .xml_parse import XmlParse


class CallLLM:
    def __init__(
        self,
        context: Context,
        xml_parse: XmlParse,
        network_config: dict,
        caption_config: dict,
    ):
        self.context = context
        self.xml_parse = xml_parse
        self.network_conf = network_config
        self.sticker_analysis_prompt = DEFAULT_STICKER_ANALYSIS_PROMPT
        # 图片转述配置
        image_caption_provider_ids = caption_config.get("image_caption_provider_ids")
        if not image_caption_provider_ids:
            old_image_provider_id = caption_config.get("image_caption_provider_id")
            if old_image_provider_id:
                image_caption_provider_ids = [
                    old_image_provider_id
                ] + caption_config.get("image_caption_fallback_provider_ids", [])
            else:
                image_caption_provider_ids = []
        self.image_caption_provider_ids = [p for p in image_caption_provider_ids if p]
        self.image_caption_prompt = DEFAULT_IMAGE_CAPTION_PROMPT
        # 音频转述配置
        audio_caption_provider_ids = caption_config.get("audio_caption_provider_ids")
        if not audio_caption_provider_ids:
            old_audio_provider_id = caption_config.get("audio_caption_provider_id")
            if old_audio_provider_id:
                audio_caption_provider_ids = [
                    old_audio_provider_id
                ] + caption_config.get("audio_caption_fallback_provider_ids", [])
            else:
                audio_caption_provider_ids = []
        self.audio_caption_provider_ids = [p for p in audio_caption_provider_ids if p]
        self.audio_caption_prompt = DEFAULT_AUDIO_CAPTION_PROMPT

    async def call_llm_decision(
        self,
        provider_ids: list[str],
        system_prompt: str,
        user_prompt: str,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
    ) -> Decision | None:
        """调用LLM进行决策"""
        if system_prompt:
            actual_system_prompt = system_prompt.strip() + "\n\n" + DEFAULT_DECISION_RULES
        else:
            actual_system_prompt = DEFAULT_DECISION_RULES

        logger.debug(f"\n<system_prompt>{actual_system_prompt}</system_prompt>")
        logger.debug(f"\n<user_prompt>{user_prompt}</user_prompt>")

        for provider_id in provider_ids:
            for i in range(self.network_conf["decision_retry_times"]):
                if i > 0:
                    logger.warning(f"LLM决策失败，{provider_id} 重试第 {i} 次")
                try:
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        system_prompt=actual_system_prompt,
                        prompt=user_prompt,
                        image_urls=image_urls,
                        audio_urls=audio_urls,
                    )
                    if llm_resp.completion_text:
                        logger.info(
                            f"\n<completion>\n{llm_resp.completion_text}\n</completion>"
                        )
                        result = self.xml_parse.decode_decision_xml(
                            llm_resp.completion_text
                        )
                        if result is not None:
                            return result
                        logger.warning(
                            f"LLM 决策 XML 解析失败，准备重试。provider_id: {provider_id}"
                        )
                        continue
                    logger.error(f"LLM回复失败: {str(llm_resp)[:1024]}")
                    continue
                except Exception as e:
                    logger.error(f"LLM回复失败: {str(e)}")
                    continue
        return None

    async def call_llm_reply(
        self,
        event: AstrMessageEvent,
        group_or_user_id: str,
        provider_ids: list[str],
        system_prompt: str,
        user_prompt: str,
        timeout: int = 120,
        use_source_tools: bool = False,
        force_xml_tools: bool = False,
        enabled_features: list[str] | None = None,
        tts_instruction: str = "",
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
    ) -> XmlLlmResult | None:
        """调用LLM进行回复"""
        # logger.info(f"\n<system_prompt>{system_prompt}</system_prompt>")
        # logger.info(f"\n<user_prompt>\n{user_prompt}\n</user_prompt>")
        for provider_id in provider_ids:
            for i in range(self.network_conf["reply_retry_times"]):
                if i > 0:
                    logger.warning(f"LLM回复失败，{provider_id} 重试第 {i} 次")
                try:
                    xml_inst = build_xml_instructions(enabled_features, tts_instruction)
                    actual_system_prompt = (system_prompt or "") + "\n\n" + xml_inst
                    tools_set = None
                    if use_source_tools or force_xml_tools:
                        tool_manager = self.context.get_llm_tool_manager()
                        tools_set = tool_manager.get_full_tool_set()
                        # AstrBot 内置的 Tavily 工具（web_search_tavily /
                        # tavily_extract_web_page）默认不会进入
                        # get_full_tool_set()，这里按白名单手动追加，
                        # 前提是 AstrBot 配置里开启了网页搜索且提供商为 tavily。
                        merged_builtin_tools: list[str] = []
                        try:
                            provider_settings = (
                                self.context.astrbot_config_mgr.default_conf.get(
                                    "provider_settings", {}
                                )
                            )
                        except Exception:
                            provider_settings = {}
                        if bool(provider_settings.get("web_search")) and (
                            provider_settings.get("websearch_provider") == "tavily"
                        ):
                            existing_names = {t.name for t in tools_set.tools}
                            for builtin_name in (
                                "web_search_tavily",
                                "tavily_extract_web_page",
                            ):
                                if (
                                    builtin_name in existing_names
                                    or not tool_manager.is_builtin_tool(builtin_name)
                                ):
                                    continue
                                builtin_tool = tool_manager.get_builtin_tool(
                                    builtin_name
                                )
                                tools_set.add_tool(builtin_tool)
                                existing_names.add(builtin_name)
                                merged_builtin_tools.append(builtin_name)
                        if merged_builtin_tools:
                            logger.debug(
                                f"<native_tool_merge>\n"
                                f"  added: {merged_builtin_tools}\n"
                                f"  reason: provider_settings.web_search=True, websearch_provider=tavily\n"
                                f"</native_tool_merge>"
                            )
                        for tool in tools_set.tools[:]:
                            if not tool.active:
                                tools_set.remove_tool(tool.name)
                        logger.debug(
                            f"\n<native_tools count={len(tools_set.tools)}>\n"
                            + "\n".join(
                                f"- name: {t.name}\n  description: {t.description}"
                                for t in tools_set.tools
                            )
                            + "\n</native_tools>"
                        )
                        target_tool_name = "web_search_tavily"
                        target_tool = next(
                            (t for t in tools_set.tools if t.name == target_tool_name),
                            None,
                        )
                        if target_tool is None:
                            is_builtin = tool_manager.is_builtin_tool(target_tool_name)
                            logger.debug(
                                f"<native_tool_probe>\n"
                                f"  target: {target_tool_name}\n"
                                f"  in_tools_set: False\n"
                                f"  is_builtin_tool: {is_builtin}\n"
                                f"  hint: {'内置工具未进入 tools_set（get_full_tool_set 仅遍历 func_list）' if is_builtin else '该工具未注册到当前工具管理器'}\n"
                                f"</native_tool_probe>"
                            )
                        else:
                            logger.debug(
                                f"<native_tool_probe>\n"
                                f"  target: {target_tool_name}\n"
                                f"  in_tools_set: True\n"
                                f"  active: {target_tool.active}\n"
                                f"</native_tool_probe>"
                            )

                        if force_xml_tools and tools_set and tools_set.tools:
                            import json

                            xml_tools_str = "\n".join(
                                f'  - <tool_call name="{t.name}" description="{t.description}">{json.dumps(t.parameters, ensure_ascii=False)}</tool_call>'
                                for t in tools_set.tools
                            )
                            xml_tools_instruction = (
                                "\n\n# 可用工具 (强制使用 XML 标签调用)\n"
                                '如果你需要使用工具，必须通过输出并列的 <tool_call name="工具名">参数JSON</tool_call> 标签来调用。不要使用原生的 function calling 功能。若没有需要调用的工具，则不要输出任何 tool_call 标签。\n'
                                "注意：参数部分必须是正确的 JSON 格式对象，例如：\n"
                                "<status>...</status>\n"
                                '<tool_call name="search_chat_history">{"keyword": "查询词"}</tool_call>\n\n'
                                "当前可用的工具列表：\n"
                            ) + xml_tools_str
                            actual_system_prompt = (
                                actual_system_prompt + xml_tools_instruction
                            )

                    logger.debug(
                        f"[Giftia] 触发大模型回复，最终系统提示词 (system_prompt):\n{actual_system_prompt}"
                    )

                    if use_source_tools and not force_xml_tools:
                        llm_resp = await self.context.tool_loop_agent(
                            event=event,
                            chat_provider_id=provider_id,
                            system_prompt=actual_system_prompt,
                            prompt=user_prompt,
                            image_urls=image_urls,
                            audio_urls=audio_urls,
                            tools=tools_set,
                            max_steps=10,
                            tool_call_timeout=timeout,
                            stream=True,
                        )
                    else:
                        llm_resp = await self.context.tool_loop_agent(
                            event=event,
                            chat_provider_id=provider_id,
                            system_prompt=actual_system_prompt,
                            prompt=user_prompt,
                            image_urls=image_urls,
                            audio_urls=audio_urls,
                            tool_call_timeout=timeout,
                            stream=True,
                        )
                    if llm_resp.tools_call_name:
                        logger.info(
                            f"\n<tools_call>\n{llm_resp.tools_call_name}\n</tools_call>"
                        )
                    if llm_resp.reasoning_content:
                        logger.info(
                            f"\n<reasoning>\n{llm_resp.reasoning_content}\n</reasoning>"
                        )
                    if llm_resp.completion_text:
                        logger.info(
                            f"\n<completion>\n{llm_resp.completion_text}\n</completion>"
                        )
                        result = await self.xml_parse.decode_llm_xml(
                            llm_resp.completion_text, group_or_user_id
                        )
                        if result is not None:
                            result.native_tools_called = list(
                                llm_resp.tools_call_name or []
                            )
                            return result
                        logger.warning(
                            f"LLM回复 XML 解析失败且无法补救，准备重试。provider_id: {provider_id}"
                        )
                        continue
                    elif llm_resp.reasoning_content:
                        # LLM generated reasoning but empty text completion; likely safety blocked or cut off.
                        logger.warning(
                            f"LLM generated reasoning but empty completion, treating as failure. provider_id: {provider_id}"
                        )
                        continue
                    else:
                        # Succeeded but both completion and reasoning are empty.
                        logger.info(
                            f"LLM returned completely empty response, treating as no reply. provider_id: {provider_id}"
                        )
                        return XmlLlmResult(
                            native_tools_called=list(llm_resp.tools_call_name or [])
                        )
                except EmptyModelOutputError:
                    # Gemini empty output error; treat as no reply needed.
                    logger.info(
                        f"LLM generated empty output error, treating as no reply. provider_id: {provider_id}"
                    )
                    return XmlLlmResult()
                except Exception as e:
                    logger.error(f"LLM回复失败: {str(e)}，provider_id: {provider_id}")
                    continue
        return None

    async def call_llm_image_caption(
        self, image_urls: list[str], question: str | None = None
    ) -> MediaCaption | None:
        """调用LLM生成图片描述"""
        logger.info(f"调用LLM生成图片描述，共{len(image_urls)}张图片")
        for provider_id in self.image_caption_provider_ids:
            for i in range(self.network_conf["image_caption_retry_times"]):
                if i > 0:
                    logger.warning(f"LLM生成图片描述失败，{provider_id} 重试第 {i} 次")
                try:
                    # Hash a 128-char window starting at offset 200 (past the ~150-char
                    # JPEG JFIF header in base64), so different images produce different
                    # fingerprints. Also include the payload length as a discriminator.
                    def _b64_sig(u: str) -> str:
                        payload = u.removeprefix("base64://")
                        return f"{len(payload)}:{xxh3_64_hexdigest(payload[200:328].encode())}"

                    b64_hashes = [_b64_sig(u) for u in image_urls]
                    logger.debug(
                        f"[Giftia] 发送给LLM的图片内容hash: {b64_hashes} "
                        f"provider={provider_id}"
                    )
                    # Append a unique fingerprint of the images to the prompt.
                    # This prevents any upstream proxy or API-level cache from returning
                    # a stale description if they compute cache keys based purely on the text prompt.
                    unique_prompt = f"{self.image_caption_prompt}\n\n[Image Fingerprint: {','.join(b64_hashes)}]"
                    if question:
                        unique_prompt += (
                            f"\n\n# 额外关注的确定问题\n"
                            f"请在此次转述中特别关注以下问题，并确保将针对该问题的分析或回答**包含在输出 JSON 的 \"caption\"（如果是画面描述相关）或 \"text\"（如果是图片内文字相关）字段中**：\n"
                            f"{question}"
                        )
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=unique_prompt,
                        image_urls=image_urls,
                    )
                    if llm_resp.completion_text:
                        logger.info(
                            f"[Giftia] LLM转述响应片段: "
                            f"{llm_resp.completion_text[:120]!r}"
                        )
                        parsed = decode_media_caption_json(llm_resp.completion_text)
                        if parsed:
                            return parsed
                        logger.warning("解析图片转述 JSON 失败，准备重试或降级...")
                    else:
                        logger.error(f"LLM回复失败: {str(llm_resp)[:1024]}")
                    continue
                except Exception as e:
                    logger.error(f"LLM回复失败: {str(e)}")
                    continue
        return None

    async def call_llm_audio_caption(
        self, audio_urls: list[str], question: str | None = None
    ) -> MediaCaption | None:
        """调用LLM生成音频描述"""
        logger.info(f"调用LLM生成音频描述，共{len(audio_urls)}个音频")
        for provider_id in self.audio_caption_provider_ids:
            for i in range(self.network_conf["audio_caption_retry_times"]):
                if i > 0:
                    logger.warning(f"LLM生成音频描述失败，{provider_id} 重试第 {i} 次")
                try:
                    # Generate a unique fingerprint of the audio URLs.
                    audio_fingerprints = [
                        xxh3_64_hexdigest(u.encode()) for u in audio_urls
                    ]
                    unique_prompt = f"{self.audio_caption_prompt}\n\n[Audio Fingerprint: {','.join(audio_fingerprints)}]"
                    if question:
                        unique_prompt += (
                            f"\n\n# 额外关注的确定问题\n"
                            f"请在此次转述中特别关注以下问题，并确保将针对该问题的分析或回答**包含在输出 JSON 的 \"caption\"（如果是音频氛围/情感描述相关）或 \"text\"（如果是语音转写的文字相关）字段中**：\n"
                            f"{question}"
                        )
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=unique_prompt,
                        audio_urls=audio_urls,
                    )
                    if llm_resp.completion_text:
                        parsed = decode_media_audio_json(llm_resp.completion_text)
                        if parsed:
                            return parsed
                        logger.warning("解析音频转述 JSON 失败，准备重试或降级...")
                    else:
                        logger.error(f"LLM回复失败: {str(llm_resp)[:1024]}")
                    continue
                except Exception as e:
                    logger.error(f"LLM回复失败: {str(e)}")
                    continue
        return None

    async def call_llm_sticker_analysis(
        self, image_urls: list[str], categories: list[str], media_id: str
    ) -> tuple[bool, Sticker | None]:
        """调用LLM生成表情包分析结果"""
        logger.info(f"调用LLM生成表情包分析结果，共{len(image_urls)}张图片")
        prompt_template = self.sticker_analysis_prompt
        if not prompt_template:
            logger.error("表情包分析提示词为空")
            return False, None

        categories_str = (
            "\n".join(f"- {c}" for c in categories) if categories else "- 无"
        )
        prompt = prompt_template.replace("{categories}", categories_str)

        for provider_id in self.image_caption_provider_ids:
            for i in range(self.network_conf.get("image_caption_retry_times", 1)):
                if i > 0:
                    logger.warning(f"LLM表情包分析失败，{provider_id} 重试第 {i} 次")
                try:
                    # Append a unique fingerprint of the images to the prompt.
                    # This prevents any upstream proxy or API-level cache from returning
                    # a stale description if they compute cache keys based purely on the text prompt.
                    unique_prompt = (
                        f"{prompt}\n\n[Sticker Fingerprint: {','.join(image_urls)}]"
                    )
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=unique_prompt,
                        image_urls=image_urls,
                    )
                    if llm_resp.completion_text:
                        result_dict = self.xml_parse.parse_str_json(
                            llm_resp.completion_text
                        )
                        if not result_dict:
                            continue

                        is_useful = result_dict.get("isUseful", False)
                        if not is_useful:
                            return False, None

                        tags = result_dict.get("tags", [])
                        if isinstance(tags, str):
                            tags = [tags]

                        sticker = Sticker(
                            sticker_id=media_id,
                            name=result_dict.get("name", "未知表情"),
                            category=result_dict.get("category", "默认分类"),
                            tags=tags,
                            description=result_dict.get("description", ""),
                        )
                        return True, sticker
                except Exception as e:
                    logger.error(f"LLM表情包分析失败: {str(e)}")
                    continue
        return False, None
