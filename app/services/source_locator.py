from __future__ import annotations

import re
from copy import deepcopy


def _normalise(value: object) -> str:
    return re.sub(r"[^0-9A-ZА-ЯӘҒҚҢӨҰҮІҺ]", "", str(value or "").upper())


def _display_quote(words: list[dict], start: int, end: int, radius: int = 7) -> str:
    left = max(0, start - radius)
    right = min(len(words), end + radius + 1)
    return " ".join(
        str(item.get("text") or "").strip()
        for item in words[left:right]
        if str(item.get("text") or "").strip()
    )[:500]


def locate_value(page_layouts: list[dict], value: object, limit: int = 5) -> list[dict]:
    """Find a candidate value in saved layout words.

    It supports identifiers broken into several OCR words, for example
    ``790 105 403 331`` or ``KZ35 8562 ...``.
    """
    target = _normalise(value)
    if len(target) < 3:
        return []

    locations: list[dict] = []
    for layout in page_layouts or []:
        words = [item for item in layout.get("words", []) if isinstance(item, dict)]
        tokens = [_normalise(item.get("text")) for item in words]

        for start, token in enumerate(tokens):
            if not token:
                continue

            # Direct token match.
            if target == token or (len(target) >= 6 and (target in token or token in target)):
                item = words[start]
                locations.append({
                    "page": int(layout.get("page") or 1),
                    "quote": _display_quote(words, start, start),
                    "box": [
                        float(item.get("x0", 0)), float(item.get("y0", 0)),
                        float(item.get("x1", 0)), float(item.get("y1", 0)),
                    ],
                    "match_type": "exact",
                })
                if len(locations) >= limit:
                    return locations

            # Concatenate up to 8 neighbouring OCR words.
            combined = ""
            for end in range(start, min(len(tokens), start + 8)):
                if not tokens[end]:
                    continue
                combined += tokens[end]
                if combined == target:
                    selected = words[start:end + 1]
                    locations.append({
                        "page": int(layout.get("page") or 1),
                        "quote": _display_quote(words, start, end),
                        "box": [
                            min(float(item.get("x0", 0)) for item in selected),
                            min(float(item.get("y0", 0)) for item in selected),
                            max(float(item.get("x1", 0)) for item in selected),
                            max(float(item.get("y1", 0)) for item in selected),
                        ],
                        "match_type": "joined",
                    })
                    if len(locations) >= limit:
                        return locations
                    break
                if len(combined) > len(target) + 4:
                    break

    return locations


def enrich_field_locations(fields: list[dict], page_layouts: list[dict]) -> list[dict]:
    enriched = []
    for field in fields or []:
        item = deepcopy(field)
        value = item.get("value")

        if isinstance(value, list):
            locations = {}
            for candidate in value[:30]:
                found = locate_value(page_layouts, candidate)
                if found:
                    locations[str(candidate)] = found
            if locations:
                item["source_locations"] = locations
        elif value not in (None, ""):
            found = locate_value(page_layouts, value)
            if found:
                item["source_locations"] = {str(value): found}
                if not item.get("page"):
                    item["page"] = found[0]["page"]
                if not item.get("quote"):
                    item["quote"] = found[0]["quote"]

        enriched.append(item)
    return enriched


def unresolved_pages(page_methods: list[dict], document_type: str) -> list[dict]:
    """Return pages that deserve visual review even when no field was extracted."""
    items = []
    for page in page_methods or []:
        quality = float(page.get("quality") or 0)
        char_count = int(page.get("char_count") or 0)
        reasons = []
        if char_count < 80:
            reasons.append("очень мало распознанного текста")
        if quality < 0.55:
            reasons.append("низкое качество распознавания")
        if page.get("method") == "ocr" and not page.get("layout_word_count"):
            reasons.append("нет координат для подсветки")
        if reasons:
            items.append({
                "page": page.get("page"),
                "reason": ", ".join(reasons),
                "quality": quality,
                "char_count": char_count,
            })

    if document_type == "unknown" and not items:
        for page in (page_methods or [])[:5]:
            items.append({
                "page": page.get("page"),
                "reason": "тип документа не определён — проверьте заголовок и реквизиты",
                "quality": float(page.get("quality") or 0),
                "char_count": int(page.get("char_count") or 0),
            })
    return items[:20]
