import json
import logging
from ..utils.schemas import MediaCaption

logger = logging.getLogger("astrbot")

def parse_markdown_json(text: str) -> dict | None:
    """解析可能包裹在 markdown 语法中的 JSON 字符串"""
    if not text:
        return None
    clean_text = text.strip()
    # 兼容 markdown 代码块
    if clean_text.startswith("```json"):
        clean_text = clean_text[7:]
    elif clean_text.startswith("```"):
        clean_text = clean_text[3:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    clean_text = clean_text.strip()
    try:
        return json.loads(clean_text)
    except Exception as e:
        logger.error(f"解析 JSON 失败: {e}, 原始文本: {text[:1000]}")
        return None

def decode_media_caption_json(json_str: str) -> MediaCaption | None:
    """解析图片描述的 JSON，返回 MediaCaption 对象"""
    data = parse_markdown_json(json_str)
    if not data:
        return None
    
    caption_text = data.get("caption") or data.get("image_description", "")
    if not caption_text:
        logger.warning(f"图片描述主字段 caption 为空, 原始数据: {json_str[:500]}")
        return None
        
    return MediaCaption(
        media_type="image",
        genre=data.get("genre") or "",
        character=data.get("character") or "",
        source=data.get("source") or "",
        text=data.get("text") or "",
        caption=caption_text,
        is_captioned=True
    )

def decode_media_audio_json(json_str: str) -> MediaCaption | None:
    """解析音频转述的 JSON，返回 MediaCaption 对象"""
    data = parse_markdown_json(json_str)
    if not data:
        return None
        
    caption_text = data.get("caption") or data.get("audio_description", "")
    if not caption_text:
        logger.warning(f"音频转述主字段 caption 为空, 原始数据: {json_str[:500]}")
        return None
        
    return MediaCaption(
        media_type="audio",
        genre=data.get("genre") or "",
        character=data.get("character") or "",
        source=data.get("source") or "",
        text=data.get("text") or "",
        caption=caption_text,
        is_captioned=True
    )
