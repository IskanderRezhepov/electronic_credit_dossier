
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\xad", "").replace("￾", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_text(text)).strip()


def parse_money(raw: str) -> Decimal | None:
    cleaned = raw.replace("\u00a0", " ").replace(" ", "")
    cleaned = cleaned.replace(",", ".")
    cleaned = re.sub(r"[^0-9.]", "", cleaned)

    if not cleaned:
        return None

    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def quote_around(text: str, start: int, end: int, radius: int = 180) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return compact_text(text[left:right])


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\\\|?*]+', "_", name)
    return name.strip() or "document"
