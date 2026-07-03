import re


def parse_aliases(aliases: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if not aliases:
        return []

    raw_items = aliases
    if isinstance(aliases, str):
        raw_items = re.split(r"[,，、;；\n\r|]+", aliases)

    parsed = []
    seen = set()
    for item in raw_items:
        alias = str(item or "").strip()
        if not alias or alias in seen:
            continue
        seen.add(alias)
        parsed.append(alias)
    return parsed
