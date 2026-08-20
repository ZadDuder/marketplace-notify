from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import html
import json
import re
import unicodedata
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_window(hours_back: int, hours_forward: int = 24) -> tuple[str, str]:
    now = utc_now()
    return rfc3339(now - timedelta(hours=hours_back)), rfc3339(now + timedelta(hours=hours_forward))


def date_window(days_back: int = 7) -> tuple[str, str]:
    now = utc_now().date()
    return (now - timedelta(days=days_back)).isoformat(), now.isoformat()


def month_key() -> str:
    return utc_now().strftime("%Y-%m")


def recent_dates(days_back: int = 7) -> list[str]:
    today = utc_now().date()
    return [(today - timedelta(days=days)).isoformat() for days in range(days_back, -1, -1)]


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return sha256(encoded).hexdigest()


def h(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def normalize_plain_text(value: str) -> str:
    text = html.unescape(value)
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(
        {
            ord("\u00a0"): " ",
            ord("\u2007"): " ",
            ord("\u202f"): " ",
            ord("\u200b"): None,
            ord("\u200c"): None,
            ord("\u200d"): None,
            ord("\u2060"): None,
            ord("\ufeff"): None,
        }
    )
    text = "".join(
        character
        for character in text
        if character in "\n\t" or unicodedata.category(character) != "Cc"
    )
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def strip_tags(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    text = re.sub(r"</(?:div|p|li|h[1-6])\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<li(?:\s[^>]*)?>", "• ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return normalize_plain_text(text)


def strip_markdown(value: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.S)
    text = re.sub(r"`{1,3}(.+?)`{1,3}", r"\1", text, flags=re.S)
    return normalize_plain_text(text)


def truncate(value: str, limit: int = 3500) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def deep_find(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_present(data: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        value = data.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def list_from_response(data: Any, preferred: list[str]) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    result = data.get("result", data)
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return []
    for key in preferred:
        value = result.get(key)
        if isinstance(value, list):
            return value
    for value in result.values():
        if isinstance(value, list):
            return value
    return []
