
from __future__ import annotations

import re
from collections import defaultdict

from app.parsers.base import field
from app.services.document_reader import ReadDocument
from app.services.text_utils import parse_money, quote_around


ROLE_RULES = {
    "seller": {
        "label": "Продавец",
        "field": "seller_iin_bin",
        "keywords": ("ПРОДАВЕЦ", "САТУШЫ"),
    },
    "buyer": {
        "label": "Покупатель",
        "field": "buyer_iin_bin",
        "keywords": ("ПОКУПАТЕЛЬ", "САТЫП АЛУШЫ"),
    },
    "lessee": {
        "label": "Лизингополучатель",
        "field": "lessee_iin_bin",
        "keywords": ("ЛИЗИНГОПОЛУЧАТЕЛЬ", "ЛИЗИНГ АЛУШЫ"),
    },
    "lessor": {
        "label": "Лизингодатель",
        "field": "lessor_iin_bin",
        "keywords": ("ЛИЗИНГОДАТЕЛЬ", "ЛИЗИНГ БЕРУШІ"),
    },
}

GENERIC_CANDIDATE_NAMES = {
    "iin_bin_candidates",
    "iban_candidates",
    "money_candidates",
}


def _occurrences(document: ReadDocument, pattern: str) -> list[dict]:
    found: list[dict] = []
    for page in document.pages:
        for match in re.finditer(pattern, page.text, re.I):
            found.append({
                "value": match.group(0).upper(),
                "page": page.page_number,
                "method": page.extraction_method,
                "quality": page.quality,
                "quote": quote_around(page.text, match.start(), match.end(), radius=230),
            })
    return found


def _score_role(context: str, role: str) -> float:
    upper = context.upper()
    score = 0.0
    for keyword in ROLE_RULES[role]["keywords"]:
        position = upper.rfind(keyword)
        if position >= 0:
            # The closest preceding role heading is the strongest signal.
            distance = max(0, len(upper) - position)
            score = max(score, 1.0 - min(distance, 450) / 700)
    if re.search(r"\b(?:ИИН|ЖСН|БИН|БСН)\b", upper):
        score += 0.15
    if role == "lessor" and ("BCC LEASING" in upper or "CENTER LEASING" in upper):
        score += 0.25
    return min(score, 1.0)



def _explicit_role_score(context: str, role: str, value: str) -> float:
    """
    Score a 12-digit identifier by explicit role labels near the value.
    The closest heading before the identifier receives the highest score.
    """
    upper = context.upper()
    score = _score_role(context, role)

    explicit_patterns = {
        "seller": (
            r"(?:ПРОДАВЕЦ|САТУШЫ).{0,260}?(?:БИН|ИИН|БСН|ЖСН)\s*[:№-]?\s*"
            + re.escape(value)
        ),
        "buyer": (
            r"(?:ПОКУПАТЕЛЬ|САТЫП АЛУШЫ).{0,260}?(?:БИН|ИИН|БСН|ЖСН)\s*[:№-]?\s*"
            + re.escape(value)
        ),
        "lessee": (
            r"(?:ЛИЗИНГОПОЛУЧАТЕЛЬ|ЛИЗИНГ АЛУШЫ).{0,260}?"
            r"(?:БИН|ИИН|БСН|ЖСН)\s*[:№-]?\s*"
            + re.escape(value)
        ),
        "lessor": (
            r"(?:ЛИЗИНГОДАТЕЛЬ|ЛИЗИНГ БЕРУШІ).{0,260}?"
            r"(?:БИН|ИИН|БСН|ЖСН)\s*[:№-]?\s*"
            + re.escape(value)
        ),
    }

    pattern = explicit_patterns[role]
    if not isinstance(pattern, (str, bytes, re.Pattern)):
        raise TypeError(
            f"Некорректный regex для роли {role!r}: ожидалась строка, "
            f"получено {type(pattern).__name__}"
        )

    if re.search(pattern, upper, re.I | re.S):
        score += 0.55

    # Strong organisation cues.
    if role == "lessor" and (
        "BCC LEASING" in upper
        or "БСС LEASING" in upper
        or "БАНК ЦЕНТРКРЕДИТ" in upper
    ):
        score += 0.18

    return min(score, 1.0)


def _promote_identifiers(document: ReadDocument) -> tuple[list[dict], set[str]]:
    occurrences = _occurrences(document, r"\b\d{12}\b")
    by_value: dict[str, list[dict]] = defaultdict(list)
    for item in occurrences:
        by_value[item["value"]].append(item)

    candidates: list[tuple[float, str, str, dict]] = []
    for role in ROLE_RULES:
        for value, items in by_value.items():
            best_item = max(
                items,
                key=lambda item: _explicit_role_score(item["quote"], role, value),
            )
            score = _explicit_role_score(best_item["quote"], role, value)
            if score >= 0.62:
                candidates.append((score, role, value, best_item))

    # Global one-to-one matching:
    # one identifier cannot be silently assigned to two different roles.
    candidates.sort(key=lambda item: item[0], reverse=True)
    assigned_roles: set[str] = set()
    assigned_values: set[str] = set()
    promoted: list[dict] = []

    for score, role, value, item in candidates:
        if role in assigned_roles or value in assigned_values:
            continue

        # Require a clear margin over the next alternative for this role.
        alternatives = sorted(
            [
                candidate_score
                for candidate_score, candidate_role, candidate_value, _ in candidates
                if candidate_role == role and candidate_value != value
            ],
            reverse=True,
        )
        if alternatives and score - alternatives[0] < 0.08:
            continue

        config = ROLE_RULES[role]
        promoted.append(
            field(
                name=config["field"],
                label_ru=f"ИИН/БИН — {config['label']}",
                value=value,
                page=item["page"],
                quote=item["quote"],
                confidence=max(0.72, min(0.96, score)),
                extraction_method=item["method"],
                status="extracted" if score >= 0.80 else "candidate",
                notes=f"Определено по ближайшему контексту роли «{config['label']}».",
            )
        )
        assigned_roles.add(role)
        assigned_values.add(value)

    return promoted, assigned_values


def _promote_ibans(document: ReadDocument) -> tuple[list[dict], set[str]]:
    occurrences = _occurrences(document, r"\bKZ[0-9A-Z]{18}\b")
    used: set[str] = set()
    promoted: list[dict] = []

    for role, config in ROLE_RULES.items():
        ranked = []
        for item in occurrences:
            score = _score_role(item["quote"], role)
            if "ИИК" in item["quote"].upper() or "IBAN" in item["quote"].upper() or "СЧЕТ" in item["quote"].upper():
                score += 0.12
            ranked.append((min(score, 1.0), item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        if not ranked or ranked[0][0] < 0.68:
            continue
        if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 0.05:
            continue
        score, item = ranked[0]
        promoted.append(field(
            name=f"{role}_iban",
            label_ru=f"IBAN — {config['label']}",
            value=item["value"],
            page=item["page"],
            quote=item["quote"],
            confidence=max(0.72, min(0.95, score)),
            extraction_method=item["method"],
            status="extracted" if score >= 0.8 else "candidate",
            notes=f"Определено по реквизитам стороны «{config['label']}».",
        ))
        used.add(item["value"])
    return promoted, used


def resolve_candidates(document: ReadDocument, fields: list[dict]) -> list[dict]:
    """
    Convert generic candidate lists into role-specific fields where the local
    legal context is sufficiently strong. Values that cannot be resolved safely
    remain candidates; the program never invents a role.
    """
    kept = [item for item in fields if item.get("name") not in GENERIC_CANDIDATE_NAMES]

    promoted_ids, used_ids = _promote_identifiers(document)
    promoted_ibans, used_ibans = _promote_ibans(document)
    kept.extend(promoted_ids)
    kept.extend(promoted_ibans)

    all_ids = sorted(set(item["value"] for item in _occurrences(document, r"\b\d{12}\b")))
    unresolved_ids = [value for value in all_ids if value not in used_ids]
    if unresolved_ids:
        kept.append(field(
            name="iin_bin_candidates",
            label_ru="Неопределённые ИИН/БИН",
            value=unresolved_ids[:20],
            page=None,
            quote=None,
            confidence=0.68 if document.used_ocr else 0.84,
            extraction_method="mixed" if document.used_ocr else "digital",
            status="candidate",
            notes=(
                "Роль не определена автоматически из-за недостаточного или неоднозначного контекста. "
                f"Показаны {min(20, len(unresolved_ids))} из {len(unresolved_ids)}."
            ),
        ))

    all_ibans = sorted(set(item["value"] for item in _occurrences(document, r"\bKZ[0-9A-Z]{18}\b")))
    unresolved_ibans = [value for value in all_ibans if value not in used_ibans]
    if unresolved_ibans:
        kept.append(field(
            name="iban_candidates",
            label_ru="Неопределённые IBAN",
            value=unresolved_ibans[:20],
            page=None,
            quote=None,
            confidence=0.68 if document.used_ocr else 0.84,
            extraction_method="mixed" if document.used_ocr else "digital",
            status="candidate",
            notes="Не удалось надёжно привязать банковский счёт к конкретной стороне.",
        ))

    # Keep only monetary values that are not already present as confirmed fields.
    confirmed_money = {
        str(item.get("value"))
        for item in kept
        if item.get("status") != "candidate"
        and any(token in item.get("name", "") for token in ("amount", "value_kzt", "principal", "interest"))
    }
    monetary = []
    for page in document.pages:
        for match in re.finditer(r"\b\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})\b", page.text):
            value = parse_money(match.group(0))
            if value is not None and str(value) not in confirmed_money:
                monetary.append(str(value))
    monetary = sorted(set(monetary), key=float)
    if monetary:
        kept.append(field(
            name="money_candidates",
            label_ru="Другие денежные суммы",
            value=monetary[:12],
            page=None,
            quote=None,
            confidence=0.62 if document.used_ocr else 0.78,
            extraction_method="mixed" if document.used_ocr else "digital",
            status="candidate",
            notes=(
                "Это дополнительные суммы, не выбранные как итоговые поля. "
                f"Показаны {min(12, len(monetary))} из {len(monetary)}."
            ),
        ))

    # Stable order: confirmed fields first, unresolved candidates last.
    return sorted(
        kept,
        key=lambda item: (
            item.get("status") == "candidate",
            item.get("page") is None,
            item.get("page") or 0,
            item.get("label_ru", ""),
        ),
    )
