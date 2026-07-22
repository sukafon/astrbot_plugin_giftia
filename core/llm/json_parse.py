import json
import logging

from ..utils.schemas import MediaCaption

logger = logging.getLogger("astrbot")


import re


def parse_markdown_json(text: str) -> dict | list | None:
    """解析可能包含前导思考文本或包裹在 markdown 语法中的 JSON 字符串 (支持 Object 与 Array)"""
    if not text:
        return None
    clean_text = text.strip()

    # 1. 尝试从 ```json [ ... ] ``` 或 ``` { ... } ``` 代码块中提取
    codeblock_match = re.search(r"```(?:json)?\s*([\[\{].*?[\]\}])\s*```", clean_text, re.DOTALL)
    if codeblock_match:
        json_str = codeblock_match.group(1).strip()
        try:
            return json.loads(json_str)
        except Exception:
            pass

    # 2. 查找最外层的 JSON 结构: 对象 { ... } 或 数组 [ ... ]
    obj_first = clean_text.find("{")
    obj_last = clean_text.rfind("}")
    arr_first = clean_text.find("[")
    arr_last = clean_text.rfind("]")

    candidates = []
    if obj_first != -1 and obj_last > obj_first:
        candidates.append((obj_first, clean_text[obj_first : obj_last + 1]))
    if arr_first != -1 and arr_last > arr_first:
        candidates.append((arr_first, clean_text[arr_first : arr_last + 1]))

    candidates.sort(key=lambda x: x[0])

    for _, snippet in candidates:
        try:
            return json.loads(snippet.strip())
        except Exception:
            pass

    # 3. 兜底直接解析整体文本
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
        is_captioned=True,
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
        is_captioned=True,
    )
