from __future__ import annotations

import re
from copy import deepcopy


ROLE_WORDS = (
    "ЛИЗИНГОПОЛУЧАТЕЛ", "ЛИЗИНГ АЛУШ", "ЛИЗИНГОДАТЕЛ", "ЛИЗИНГ БЕРУШ",
    "ЗАЕМЩИК", "ҚАРЫЗ АЛУШ", "ГАРАНТ", "КЕПІЛГЕР", "ПОЛУЧАТЕЛ", "АЛУШЫ",
    "ПРОДАВЕЦ", "САТУШЫ", "ПОКУПАТЕЛ", "САТЫП АЛУШЫ", "БЕНЕФИЦИАР",
    "ОТПРАВИТЕЛ", "ЖӨНЕЛТУШІ", "БАНКОВСКИЙ СЧЕТ", "ИИК", "IBAN", "БИН", "ИИН",
)


def _normalise(value: object) -> str:
    return re.sub(r"[^0-9A-ZА-ЯӘҒҚҢӨҰҮІҺ]", "", str(value or "").upper())


def _display_quote(words: list[dict], start: int, end: int, radius: int = 10) -> str:
    left = max(0, start - radius)
    right = min(len(words), end + radius + 1)
    return " ".join(
        str(item.get("text") or "").strip()
        for item in words[left:right]
        if str(item.get("text") or "").strip()
    )[:700]


def _box(words: list[dict]) -> list[float]:
    return [
        min(float(item.get("x0", 0)) for item in words),
        min(float(item.get("y0", 0)) for item in words),
        max(float(item.get("x1", 0)) for item in words),
        max(float(item.get("y1", 0)) for item in words),
    ]


def _same_location(left: dict, right: dict) -> bool:
    if left["page"] != right["page"]:
        return False
    a, b = left["box"], right["box"]
    return (
        abs(a[0] - b[0]) < 4 and abs(a[1] - b[1]) < 4
        and abs(a[2] - b[2]) < 8 and abs(a[3] - b[3]) < 8
    )


def _context_score(quote: str, target: str) -> int:
    upper = quote.upper()
    score = sum(2 for word in ROLE_WORDS if word in upper)
    if target.startswith("KZ") and any(word in upper for word in ("ИИК", "IBAN", "СЧЕТ", "СЧЁТ", "ЖСК")):
        score += 6
    if len(target) == 12 and any(word in upper for word in ("БИН", "ИИН", "БСН", "ЖСН")):
        score += 6
    if len(target) == 17 and "VIN" in upper:
        score += 6
    return score


def locate_value(page_layouts: list[dict], value: object, limit: int = 5) -> list[dict]:
    """Find only real occurrences and remove duplicate OCR/digital-layer boxes."""
    target = _normalise(value)
    if len(target) < 3:
        return []

    found: list[dict] = []
    for layout in page_layouts or []:
        words = [item for item in layout.get("words", []) if isinstance(item, dict)]
        tokens = [_normalise(item.get("text")) for item in words]

        for start, token in enumerate(tokens):
            if not token:
                continue

            matches: list[tuple[int, str]] = []
            if token == target:
                matches.append((start, "exact"))

            combined = ""
            for end in range(start, min(len(tokens), start + 12)):
                if not tokens[end]:
                    continue
                combined += tokens[end]
                if combined == target:
                    matches.append((end, "joined"))
                    break
                if len(combined) >= len(target):
                    break

            for end, match_type in matches:
                selected = words[start:end + 1]
                quote = _display_quote(words, start, end)
                item = {
                    "page": int(layout.get("page") or 1),
                    "quote": quote,
                    "box": _box(selected),
                    "match_type": match_type,
                    "context_score": _context_score(quote, target),
                }
                if not any(_same_location(item, prior) for prior in found):
                    found.append(item)

    # Prefer occurrences with labels/role context; one real occurrence per page
    # is usually enough for review unless boxes are clearly far apart.
    found.sort(key=lambda item: (-item["context_score"], item["page"], item["box"][1], item["box"][0]))
    result: list[dict] = []
    for item in found:
        if any(_same_location(item, prior) for prior in result):
            continue
        result.append(item)
        if len(result) >= limit:
            break
    return result


def enrich_field_locations(fields: list[dict], page_layouts: list[dict]) -> list[dict]:
    enriched = []
    for field in fields or []:
        item = deepcopy(field)
        item.pop("source_locations", None)
        value = item.get("value")

        if isinstance(value, list):
            locations = {}
            for candidate in value[:30]:
                candidate_found = locate_value(page_layouts, candidate)
                if candidate_found:
                    locations[str(candidate)] = candidate_found
            if locations:
                item["source_locations"] = locations
        elif value not in (None, ""):
            candidate_found = locate_value(page_layouts, value)
            if candidate_found:
                item["source_locations"] = {str(value): candidate_found}
                if not item.get("page"):
                    item["page"] = candidate_found[0]["page"]
                if not item.get("quote"):
                    item["quote"] = candidate_found[0]["quote"]

        enriched.append(item)
    return enriched


def unresolved_pages(page_methods: list[dict], document_type: str) -> list[dict]:
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
        for page in (page_methods or [])[:3]:
            items.append({
                "page": page.get("page"),
                "reason": "тип документа не определён — проверьте заголовок и реквизиты",
                "quality": float(page.get("quality") or 0),
                "char_count": int(page.get("char_count") or 0),
            })
    return items[:20]
