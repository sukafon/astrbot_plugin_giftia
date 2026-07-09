import asyncio
import re
from dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.message_components import Record
from astrbot.core.provider.provider import TTSProvider

from ..utils.schemas import TTSRequest


LANGUAGE_LABELS = {
    "中文": "zh-CN",
    "汉语": "zh-CN",
    "普通话": "zh-CN",
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-CN": "zh-CN",
    "英文": "en-US",
    "英语": "en-US",
    "en": "en-US",
    "en-us": "en-US",
    "en-US": "en-US",
    "日文": "ja-JP",
    "日语": "ja-JP",
    "ja": "ja-JP",
    "ja-jp": "ja-JP",
    "ja-JP": "ja-JP",
}
LANGUAGE_NAMES = {
    "zh-CN": "中文",
    "en-US": "英文",
    "ja-JP": "日文",
}

MINIMAX_EMOTIONS = {
    "happy",
    "sad",
    "angry",
    "fearful",
    "disgusted",
    "surprised",
    "calm",
    "fluent",
    "whisper",
}
MINIMAX_TONE_TAGS = [
    "(laughs)",
    "(chuckle)",
    "(coughs)",
    "(clear-throat)",
    "(groans)",
    "(breath)",
    "(pant)",
    "(inhale)",
    "(exhale)",
    "(gasps)",
    "(sniffs)",
    "(sighs)",
    "(snorts)",
    "(burps)",
    "(lip-smacking)",
    "(humming)",
    "(hissing)",
    "(emm)",
    "(sneezes)",
]


@dataclass(slots=True)
class ResolvedTTSRequest:
    request: TTSRequest
    provider: TTSProvider
    provider_id: str
    lang: str
    text: str
    emotion: str


class TTSManager:
    def __init__(self, plugin):
        self.plugin = plugin
        self._provider_locks: dict[str, asyncio.Lock] = {}

    @property
    def config(self) -> dict:
        return getattr(self.plugin, "tts_config", {}) or {}

    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    def provider_type(self) -> str:
        provider_type = str(self.config.get("provider_type", "minimax")).strip().lower()
        return provider_type if provider_type in {"minimax", "fishaudio"} else "minimax"

    def _language_items(self) -> list[tuple[str, str]]:
        items = self.config.get("language_provider_map") or []
        if not isinstance(items, list):
            return []

        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            lang = self.normalize_language(
                item.get("language") or item.get("lang") or item.get("语言") or ""
            )
            provider_id = str(
                item.get("provider_id")
                or item.get("provider")
                or item.get("tts_provider_id")
                or item.get("供应商")
                or ""
            ).strip()
            if lang:
                result.append((lang, provider_id))
        return result

    @staticmethod
    def normalize_language(value: str) -> str:
        key = str(value or "").strip()
        return LANGUAGE_LABELS.get(key) or LANGUAGE_LABELS.get(key.lower(), "")

    def default_language(self) -> str:
        items = self._language_items()
        return items[0][0] if items else "zh-CN"

    def _provider_id_for_language(self, lang: str) -> tuple[str, str]:
        items = self._language_items()
        if not items:
            return "", lang or "zh-CN"

        normalized_lang = lang if lang in LANGUAGE_NAMES else ""
        for item_lang, provider_id in items:
            if item_lang == normalized_lang:
                return provider_id, item_lang
        return items[0][1], items[0][0]

    def _lock_for_provider(self, provider_id: str) -> asyncio.Lock:
        if provider_id not in self._provider_locks:
            self._provider_locks[provider_id] = asyncio.Lock()
        return self._provider_locks[provider_id]

    def build_prompt_instruction(self) -> str:
        if not self.enabled():
            return ""

        default_lang = self.default_language()
        default_lang_name = LANGUAGE_NAMES.get(default_lang, default_lang)
        lines = [
            "",
            "## TTS 语音输出",
            '如果需要发送语音，请输出并列的 `<tts lang="语言代码" emotion="情绪">语音文本</tts>` 标签；不要把 `<tts>` 放进 `<message>` 内。',
            "可用语言代码仅限：`zh-CN`（中文）、`en-US`（英文）、`ja-JP`（日文）。无法判断语言时使用配置列表第一项作为默认语言："
            f"`{default_lang}`（{default_lang_name}）。",
            "`emotion` 为可选属性；没有明确情绪时可以省略。可以连续输出多个 `<tts>` 标签，系统会按出现顺序合成。",
        ]

        if self.provider_type() == "minimax":
            lines.extend(
                [
                    "",
                    "### MiniMax TTS 标签规则",
                    "语音文本中可以插入以下语气词标签，且只能使用这些标签："
                    + "、".join(f"`{tag}`" for tag in MINIMAX_TONE_TAGS)
                    + "。",
                    "情绪 `emotion` 只建议使用："
                    + "、".join(f"`{emotion}`" for emotion in sorted(MINIMAX_EMOTIONS))
                    + "。不确定时省略；非法情绪会被系统忽略。",
                    '示例：`<tts lang="ja-JP" emotion="happy">えへへ (laughs)、任せて！</tts>`',
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "### FishAudio TTS 标签规则",
                    "语音文本中可以使用自由的方括号标签，例如 `[softly]`、`[laughing]`、`[whisper]`。",
                    "情绪 `emotion` 可以更自由，但应保持短小，例如 `happy`、`sad`、`gentle`、`excited`；系统会把它拼到语音文本最前端的方括号标签中。",
                    '示例：`<tts lang="zh-CN" emotion="gentle">[softly]我在这里。</tts>`',
                ]
            )

        return "\n".join(lines)

    def _warn_provider_type_mismatch(self, provider: TTSProvider, provider_id: str) -> None:
        expected = {
            "minimax": "minimax_tts_api",
            "fishaudio": "fishaudio_tts_api",
        }.get(self.provider_type())
        actual = ""
        try:
            actual = provider.meta().type
        except Exception:
            actual = getattr(provider, "provider_config", {}).get("type", "")
        if expected and actual and actual != expected:
            logger.warning(
                f"[Giftia TTS] 配置的 TTS 供应商类型为 {self.provider_type()}，"
                f"但 provider_id={provider_id} 的实际类型是 {actual}。"
            )

    def resolve(self, segment: TTSRequest) -> ResolvedTTSRequest | None:
        if not self.enabled():
            return None

        text = str(segment.text or "").strip()
        if not text:
            return None

        lang = self.normalize_language(segment.lang) or self.default_language()
        provider_id, resolved_lang = self._provider_id_for_language(lang)
        if not provider_id:
            logger.warning(
                f"[Giftia TTS] 未配置 {LANGUAGE_NAMES.get(resolved_lang, resolved_lang)} 的 AstrBot TTS 供应商，跳过语音合成。"
            )
            return None

        provider = self.plugin.context.get_provider_by_id(provider_id)
        if not isinstance(provider, TTSProvider):
            logger.warning(
                f"[Giftia TTS] provider_id={provider_id} 不是可用的 AstrBot TTS 供应商，跳过语音合成。"
            )
            return None

        self._warn_provider_type_mismatch(provider, provider_id)

        emotion = str(segment.emotion or "").strip()
        return ResolvedTTSRequest(
            request=segment,
            provider=provider,
            provider_id=provider_id,
            lang=resolved_lang,
            text=self._adapt_text(text, emotion),
            emotion=emotion,
        )

    def _adapt_text(self, text: str, emotion: str) -> str:
        if self.provider_type() != "fishaudio" or not emotion:
            return text

        tag = re.sub(r"[\[\]\r\n]+", " ", emotion).strip()
        tag = re.sub(r"\s+", " ", tag)[:40]
        if not tag:
            return text
        return f"[{tag}]{text}"

    async def get_audio_path(self, resolved: ResolvedTTSRequest) -> str:
        lock = self._lock_for_provider(resolved.provider_id)
        async with lock:
            if self.provider_type() != "minimax":
                return await resolved.provider.get_audio(resolved.text)

            emotion = resolved.emotion.strip().lower()
            if emotion not in MINIMAX_EMOTIONS or not hasattr(
                resolved.provider, "voice_setting"
            ):
                return await resolved.provider.get_audio(resolved.text)

            voice_setting = resolved.provider.voice_setting
            marker = object()
            old_emotion = voice_setting.get("emotion", marker)
            voice_setting["emotion"] = emotion
            try:
                return await resolved.provider.get_audio(resolved.text)
            finally:
                if old_emotion is marker:
                    voice_setting.pop("emotion", None)
                else:
                    voice_setting["emotion"] = old_emotion

    async def build_record(self, event, segment: TTSRequest) -> Record | None:
        resolved = self.resolve(segment)
        if not resolved:
            return None

        try:
            logger.info(
                f"[Giftia TTS] 请求语音合成: lang={resolved.lang}, "
                f"provider={resolved.provider_id}, text={resolved.text}"
            )
            audio_path = await self.get_audio_path(resolved)
            if not audio_path:
                logger.error("[Giftia TTS] TTS 供应商未返回音频文件路径。")
                return None

            if hasattr(event, "track_temporary_local_file"):
                event.track_temporary_local_file(audio_path)

            logger.info(f"[Giftia TTS] 语音合成完成: {audio_path}")
            return Record.fromFileSystem(audio_path, text=segment.text)
        except Exception as e:
            logger.error(f"[Giftia TTS] 语音合成失败: {e}", exc_info=True)
            return None
