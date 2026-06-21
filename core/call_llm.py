from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context
from astrbot.core.exceptions import EmptyModelOutputError

from .schemas import (
    Decision,
    MediaCaption,
    Sticker,
    XmlLlmResult,
)
from .xml_parse import XmlParse


class CallLLM:
    def __init__(
        self,
        context: Context,
        xml_parse: XmlParse,
        network_config: dict,
        caption_config: dict,
        sticker_analysis_prompt: str = "",
    ):
        self.context = context
        self.xml_parse = xml_parse
        self.network_conf = network_config
        self.sticker_analysis_prompt = sticker_analysis_prompt
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
        self.image_caption_prompt = caption_config.get(
            "image_caption_system_prompt", ""
        )
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
        self.audio_caption_prompt = caption_config.get(
            "audio_caption_system_prompt", ""
        )

    async def call_llm_decision(
        self,
        provider_ids: list[str],
        system_prompt: str,
        user_prompt: str,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
    ) -> Decision | None:
        """调用LLM进行决策"""
        # logger.info(f"\n<system_prompt>{system_prompt}</system_prompt>")
        # logger.info(f"\n<user_prompt>{user_prompt}</user_prompt>")
        for provider_id in provider_ids:
            for i in range(self.network_conf["decision_retry_times"]):
                if i > 0:
                    logger.warning(f"LLM决策失败，{provider_id} 重试第 {i} 次")
                try:
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        system_prompt=system_prompt,
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
                    if use_source_tools:
                        tools_set = (
                            self.context.get_llm_tool_manager().get_full_tool_set()
                        )
                        for tool in tools_set.tools[:]:
                            if not tool.active:
                                tools_set.remove_tool(tool.name)
                        llm_resp = await self.context.tool_loop_agent(
                            event=event,
                            chat_provider_id=provider_id,
                            system_prompt=system_prompt,
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
                            system_prompt=system_prompt,
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
                        return XmlLlmResult()
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
        self, image_urls: list[str]
    ) -> MediaCaption | None:
        """调用LLM生成图片描述"""
        logger.info(f"调用LLM生成图片描述，共{len(image_urls)}张图片")
        for provider_id in self.image_caption_provider_ids:
            for i in range(self.network_conf["image_caption_retry_times"]):
                if i > 0:
                    logger.warning(f"LLM生成图片描述失败，{provider_id} 重试第 {i} 次")
                try:
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=self.image_caption_prompt,
                        image_urls=image_urls,
                    )
                    if llm_resp.completion_text:
                        return self.xml_parse.decode_media_caption_xml(
                            llm_resp.completion_text
                        )
                    logger.error(f"LLM回复失败: {str(llm_resp)[:1024]}")
                    continue
                except Exception as e:
                    logger.error(f"LLM回复失败: {str(e)}")
                    continue
        return None

    async def call_llm_audio_caption(
        self, audio_urls: list[str]
    ) -> MediaCaption | None:
        """调用LLM生成音频描述"""
        logger.info(f"调用LLM生成音频描述，共{len(audio_urls)}个音频")
        for provider_id in self.audio_caption_provider_ids:
            for i in range(self.network_conf["audio_caption_retry_times"]):
                if i > 0:
                    logger.warning(f"LLM生成音频描述失败，{provider_id} 重试第 {i} 次")
                try:
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=self.audio_caption_prompt,
                        audio_urls=audio_urls,
                    )
                    if llm_resp.completion_text:
                        return self.xml_parse.decode_media_caption_xml(
                            llm_resp.completion_text
                        )
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
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=prompt,
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
