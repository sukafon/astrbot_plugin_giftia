from dataclasses import dataclass, field


@dataclass
class ChainParseResult:
    content: str = ""
    media_id_list: list[str] = field(default_factory=list)
    forward_messages: list[dict] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    audio_urls: list[str] = field(default_factory=list)

    def merge(self, other: "ChainParseResult", include_content: bool = False) -> None:
        if include_content and other.content:
            self.content = f"{self.content} {other.content}".strip()
        self.media_id_list.extend(other.media_id_list)
        self.forward_messages.extend(other.forward_messages)
        self.image_urls.extend(other.image_urls)
        self.audio_urls.extend(other.audio_urls)
