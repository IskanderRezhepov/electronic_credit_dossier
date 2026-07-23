from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime

from app.parsers.base import field, normalize_contract_number
from app.services.document_reader import ReadDocument
from app.services.text_utils import parse_money


ROLE_LABELS = {
    "lessee": "Лизингополучатель",
    "lessor": "Лизингодатель",
    "borrower": "Заёмщик",
    "guarantor": "Гарант",
    "recipient": "Получатель",
    "financial_agency": "Финансовое агентство",
    "leasing_company": "Лизинговая компания",
    "seller": "Продавец",
    "buyer": "Покупатель",
    "sender": "Отправитель",
    "beneficiary": "Бенефициар",
}

ROLE_KEYWORDS = {
    "lessee": (
        "ЛИЗИНГОПОЛУЧАТЕЛ", "ЛИЗИНГ АЛУШ", "ЛИЗИНГАЛУШЫ",
    ),
    "lessor": (
        "ЛИЗИНГОДАТЕЛ", "ЛИЗИНГ БЕРУШ", "ЛИЗИНГБЕРУШІ",
    ),
    "borrower": (
        "ЗАЕМЩИК", "ЗАЁМЩИК", "ҚАРЫЗ АЛУШ",
    ),
    "guarantor": (
        "ГАРАНТ", "КЕПІЛГЕР",
    ),
    "recipient": (
        "ПОЛУЧАТЕЛ", "АЛУШЫ",
    ),
    "financial_agency": (
        "ФИНАНСОВОЕ АГЕНТСТВО", "ҚАРЖЫ АГЕНТТІГІ",
    ),
    "leasing_company": (
        "ЛИЗИНГОВАЯ КОМПАНИЯ", "ЛИЗИНГТІК КОМПАНИЯ",
    ),
    "seller": (
        "ПРОДАВЕЦ", "САТУШЫ",
    ),
    "buyer": (
        "ПОКУПАТЕЛ", "САТЫП АЛУШЫ",
    ),
    "sender": (
        "ОТПРАВИТЕЛ", "ЖӨНЕЛТУШІ",
    ),
    "beneficiary": (
        "БЕНЕФИЦИАР",
    ),
}

ROLE_FIELD_NAMES = {
    "lessee": ("lessee_iin_bin", "lessee_iban"),
    "lessor": ("lessor_iin_bin", "lessor_iban"),
    "borrower": ("borrower_iin_bin", "borrower_iban"),
    "guarantor": ("guarantor_iin_bin", "guarantor_iban"),
    "recipient": ("recipient_iin_bin", "recipient_iban"),
    "financial_agency": ("financial_agency_iin_bin", "financial_agency_iban"),
    "leasing_company": ("leasing_company_iin_bin", "leasing_company_iban"),
    "seller": ("seller_iin_bin", "seller_iban"),
    "buyer": ("buyer_iin_bin", "buyer_iban"),
    "sender": ("sender_iin_bin", "sender_iban"),
    "beneficiary": ("beneficiary_iin_bin", "beneficiary_iban"),
}

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def _normalise(value: object) -> str:
    return re.sub(r"[^0-9A-ZА-ЯӘҒҚҢӨҰҮІҺ]", "", str(value or "").upper())


def _existing(fields: list[dict], name: str) -> dict | None:
    return next((item for item in fields if item.get("name") == name), None)


def _upsert(fields: list[dict], item: dict, *, replace_candidate: bool = True) -> None:
    existing = _existing(fields, item["name"])
    if existing is None:
        fields.append(item)
        return
    if replace_candidate and (
        existing.get("status") == "candidate"
        or float(item.get("confidence") or 0) > float(existing.get("confidence") or 0)
    ):
        existing.clear()
        existing.update(item)


def _date_iso(day: str, month: str, year: str) -> str:
    return datetime(int(year), MONTHS[month.lower()], int(day)).strftime("%d.%m.%Y")


def _find_signature_date(document: ReadDocument) -> tuple[str, int, str, str] | None:
    patterns = (
        r"ДАТА\s+ПОДПИСАНИЯ\s*[:\-]?\s*(\d{2}[.\-/]\d{2}[.\-/]\d{4})",
        r"ПОДПИСАН[АО]?\s*[:\-]?\s*(\d{2}[.\-/]\d{2}[.\-/]\d{4})",
    )
    for page in reversed(document.pages):
        for pattern in patterns:
            match = re.search(pattern, page.text, re.I)
            if match:
                value = match.group(1).replace("/", ".").replace("-", ".")
                quote = page.text[max(0, match.start() - 130):match.end() + 130]
                return value, page.page_number, quote, page.extraction_method
        match = re.search(
            r"ДАТА\s+ПОДПИСАНИЯ\s*[:\-]?\s*[«\"]?(\d{1,2})[»\"]?\s*"
            r"(января|февраля|марта|апреля|мая|июня|июля|августа|"
            r"сентября|октября|ноября|декабря)\s*(20\d{2})",
            page.text,
            re.I,
        )
        if match:
            value = _date_iso(*match.groups())
            quote = page.text[max(0, match.start() - 130):match.end() + 130]
            return value, page.page_number, quote, page.extraction_method
    return None


def _find_heading_date(document: ReadDocument) -> tuple[str, int, str, str] | None:
    for page in document.pages[:2]:
        patterns = (
            r"(?:г\.?\s*[А-ЯA-ZЁҰҮҚҒӨӘІ][^,\n]{0,40}|[А-ЯA-ZЁҰҮҚҒӨӘІ][^,\n]{0,40}\s*қ\.?)"
            r".{0,100}?(\d{2}[.\-/]\d{2}[.\-/]\d{4})",
            r"[«\"]?(\d{1,2})[»\"]?\s*"
            r"(января|февраля|марта|апреля|мая|июня|июля|августа|"
            r"сентября|октября|ноября|декабря)\s*(20\d{2})",
        )
        match = re.search(patterns[0], page.text, re.I | re.S)
        if match:
            value = match.group(1).replace("/", ".").replace("-", ".")
            return value, page.page_number, page.text[max(0, match.start()-120):match.end()+120], page.extraction_method
        match = re.search(patterns[1], page.text, re.I)
        if match:
            value = _date_iso(*match.groups())
            return value, page.page_number, page.text[max(0, match.start()-120):match.end()+120], page.extraction_method
    return None


def _repair_contract_number(value: str) -> str:
    text = str(value or "").upper().strip()
    text = text.replace("\\", "/").replace("|", "/")
    text = re.sub(r"\s+", "", text)
    parts = [part for part in text.split("/") if part]
    if len(parts) < 5:
        return normalize_contract_number(text)

    # Kazakhstan BCC-style contract schema: PREFIX/YEAR/U/S/NUMBER etc.
    first = parts[0]
    if re.fullmatch(r"[АA][0ОOQ][ОO0]5", first):
        first = "AQ5"
    elif first.startswith(("А0", "A0", "AO", "АО")) and first.endswith("5"):
        first = "AQ5"
    first = first.replace("А", "A").replace("О", "O")

    year = parts[1]
    mapping = {"0": "U", "О": "U"} if len(parts) >= 5 else {}
    third = mapping.get(parts[2], parts[2])
    fourth_map = {"8": "S", "5": "S", "0": "S", "О": "S"}
    fourth = fourth_map.get(parts[3], parts[3])
    repaired = "/".join([first, year, third, fourth] + parts[4:])
    return normalize_contract_number(repaired)


def _contract_candidates(document: ReadDocument) -> list[tuple[str, int, str, str]]:
    pattern = re.compile(
        r"[A-ZА-Я0-9]{2,8}\s*/\s*20\d{2}\s*/\s*[A-ZА-Я0-9]\s*/\s*"
        r"[A-ZА-Я0-9]\s*/\s*\d{4,8}",
        re.I,
    )
    results = []
    for page in document.pages[:3]:
        for match in pattern.finditer(page.text):
            raw = match.group(0)
            value = _repair_contract_number(raw)
            results.append((
                value,
                page.page_number,
                page.text[max(0, match.start()-150):match.end()+150],
                page.extraction_method,
            ))
    return results


def _layout_occurrences(document: ReadDocument, value: str) -> list[dict]:
    target = _normalise(value)
    results = []
    for page in document.pages:
        words = page.layout_words or []
        tokens = [_normalise(word.get("text")) for word in words]
        for start in range(len(tokens)):
            combined = ""
            for end in range(start, min(len(tokens), start + 12)):
                combined += tokens[end]
                if combined == target:
                    selected = words[start:end+1]
                    results.append({
                        "page": page,
                        "start": start,
                        "end": end,
                        "x": sum((float(word["x0"]) + float(word["x1"])) / 2 for word in selected) / len(selected),
                        "y": sum((float(word["y0"]) + float(word["y1"])) / 2 for word in selected) / len(selected),
                    })
                    break
                if len(combined) >= len(target):
                    break
    return results


def _layout_role(document: ReadDocument, value: str) -> tuple[str, int, str, float, str] | None:
    candidates = []
    for occurrence in _layout_occurrences(document, value):
        page = occurrence["page"]
        x, y = occurrence["x"], occurrence["y"]
        nearby = []
        for word in page.layout_words or []:
            wx = (float(word["x0"]) + float(word["x1"])) / 2
            wy = (float(word["y0"]) + float(word["y1"])) / 2
            # Same requisites column. Prefer headings above and close text.
            if abs(wx - x) <= max(90, (page.page_width or 800) * 0.14) and y - 520 <= wy <= y + 100:
                nearby.append((wy, wx, str(word.get("text") or "")))
        nearby.sort()
        context = " ".join(item[2] for item in nearby).upper()
        for role, keywords in ROLE_KEYWORDS.items():
            score = 0.0
            for keyword in keywords:
                if keyword in context:
                    score = max(score, 0.88)
            if score:
                # Stronger if role appears in the last part of the column before value.
                tail = context[-500:]
                if any(keyword in tail for keyword in keywords):
                    score = 0.96
                candidates.append((
                    score, role, page.page_number, context[-700:], page.extraction_method,
                ))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    score, role, page, quote, method = candidates[0]
    if len(candidates) > 1 and candidates[1][0] == score and candidates[1][1] != role:
        return None
    return role, page, quote, score, method


def _field_name_for(role: str, value: str) -> tuple[str, str]:
    is_iban = value.startswith("KZ")
    id_name, iban_name = ROLE_FIELD_NAMES[role]
    label = f"IBAN — {ROLE_LABELS[role]}" if is_iban else f"ИИН/БИН — {ROLE_LABELS[role]}"
    return (iban_name if is_iban else id_name), label


def _assign_layout_candidates(document: ReadDocument, fields: list[dict]) -> None:
    candidate_names = {"iin_bin_candidates", "iban_candidates"}
    candidate_fields = [item for item in fields if item.get("name") in candidate_names]
    for candidate_field in candidate_fields:
        values = list(candidate_field.get("value") or [])
        remaining = []
        for value in values:
            resolved = _layout_role(document, str(value))
            if not resolved:
                remaining.append(value)
                continue
            role, page, quote, score, method = resolved
            name, label = _field_name_for(role, str(value))
            _upsert(fields, field(
                name=name,
                label_ru=label,
                value=value,
                page=page,
                quote=quote,
                confidence=score,
                extraction_method=method,
                status="extracted" if score >= 0.92 else "candidate",
                notes="Роль определена по визуальной колонке блока реквизитов.",
            ))
        candidate_field["value"] = remaining

    fields[:] = [
        item for item in fields
        if item.get("name") not in candidate_names or item.get("value")
    ]


def _remove_duplicate_candidates(fields: list[dict], tables: list[dict] | None = None) -> None:
    confirmed_ids = set()
    confirmed_ibans = set()
    table_vins = set()
    for item in fields:
        value = item.get("value")
        if isinstance(value, list) or item.get("status") in {"candidate", "rejected"}:
            continue
        name = str(item.get("name") or "")
        if isinstance(value, str) and value.startswith("KZ"):
            confirmed_ibans.add(value)
        elif re.fullmatch(r"\d{12}", str(value or "")):
            confirmed_ids.add(str(value))
    for table in tables or []:
        if table.get("name") == "asset_vin_rows":
            table_vins.update(
                str(row.get("vin")) for row in table.get("rows", []) if row.get("vin")
            )

    for item in fields:
        value = item.get("value")
        if not isinstance(value, list):
            continue
        if item.get("name") == "iban_candidates":
            item["value"] = [entry for entry in value if entry not in confirmed_ibans]
        elif item.get("name") == "iin_bin_candidates":
            item["value"] = [entry for entry in value if str(entry) not in confirmed_ids]
        elif item.get("name") == "vin_candidates":
            item["value"] = [entry for entry in value if str(entry) not in table_vins]
    fields[:] = [
        item for item in fields
        if not isinstance(item.get("value"), list) or item.get("value")
    ]


def _party_name(document: ReadDocument, role: str) -> tuple[str, int, str, str] | None:
    patterns = {
        "lessee": (
            r"(?:ТОО|Товарищество\s+с\s+ограниченной\s+ответственностью|ЖШС)\s*[«\"]?([^»\"\n,]{2,100})[»\"]?"
            r".{0,220}?(?:Лизингополучатель|Лизинг\s+алушы)",
            r"(?:Лизингополучатель|ЛИЗИНГ\s+АЛУШЫ)\s*[:\-]\s*(?:ТОО|ЖШС)?\s*[«\"]?([^»\"\n,]{2,100})",
        ),
        "recipient": (
            r"(?:ТОО|Товарищество\s+с\s+ограниченной\s+ответственностью|ЖШС)\s*[«\"]?([^»\"\n,]{2,100})[»\"]?"
            r".{0,220}?(?:Получатель|Алушы)",
        ),
        "borrower": (
            r"(?:ТОО|Товарищество\s+с\s+ограниченной\s+ответственностью|ЖШС)\s*[«\"]?([^»\"\n,]{2,100})[»\"]?"
            r".{0,220}?(?:Заемщик|Заёмщик|Қарыз\s+алушы)",
        ),
        "guarantor": (
            r"(?:Гарант|Кепілгер)\s*[:\-]\s*(?:Ф\.?И\.?О\.?)?\s*([А-ЯЁҰҮҚҒӨӘІ][А-Яа-яЁёҰұҮүҚқҒғӨөӘәІі\-]+\s+"
            r"[А-ЯЁҰҮҚҒӨӘІ][А-Яа-яЁёҰұҮүҚқҒғӨөӘәІі\-]+\s+"
            r"[А-ЯЁҰҮҚҒӨӘІ][А-Яа-яЁёҰұҮүҚқҒғӨөӘәІі\-]+)",
        ),
    }
    for page in document.pages:
        for pattern in patterns.get(role, ()):
            match = re.search(pattern, page.text, re.I | re.S)
            if match:
                value = re.sub(r"\s+", " ", match.group(1)).strip(" ,.;:\"")
                return value, page.page_number, page.text[max(0, match.start()-120):match.end()+120], page.extraction_method
    return None



def _page_window(page_text: str, value: str, radius: int = 650) -> str:
    match = re.search(re.escape(str(value)), page_text, re.I)
    if not match:
        return ""
    return page_text[max(0, match.start() - radius):match.end() + radius]


def _explicit_account_role(document: ReadDocument, iban: str) -> str | None:
    patterns = {
        "lessee": (
            r"ЛИЗИНГОПОЛУЧАТЕЛ.{0,650}" + re.escape(iban),
            r"ЛИЗИНГ\s+АЛУШ.{0,650}" + re.escape(iban),
            re.escape(iban) + r".{0,260}(?:СЧЕТ\s+ЛИЗИНГОПОЛУЧАТЕЛ|ЛИЗИНГ\s+АЛУШЫНЫҢ\s+ШОТ)",
        ),
        "lessor": (
            r"ЛИЗИНГОДАТЕЛ.{0,650}" + re.escape(iban),
            r"ЛИЗИНГ\s+БЕРУШ.{0,650}" + re.escape(iban),
            re.escape(iban) + r".{0,260}(?:СЧЕТ\s+ЛИЗИНГОДАТЕЛ|ЛИЗИНГ\s+БЕРУШІНІҢ\s+ШОТ)",
        ),
        "guarantor": (
            r"ГАРАНТ.{0,650}" + re.escape(iban),
            r"КЕПІЛГЕР.{0,650}" + re.escape(iban),
        ),
        "recipient": (
            r"ПОЛУЧАТЕЛ.{0,650}" + re.escape(iban),
            r"АЛУШЫ.{0,650}" + re.escape(iban),
        ),
    }
    best = []
    for page in document.pages:
        upper = page.text.upper()
        for role, role_patterns in patterns.items():
            for pattern in role_patterns:
                match = re.search(pattern, upper, re.I | re.S)
                if match:
                    length = max(1, match.end() - match.start())
                    best.append((1 / length, role))
    if not best:
        return None
    best.sort(reverse=True)
    if len(best) > 1 and best[0][0] == best[1][0] and best[0][1] != best[1][1]:
        return None
    return best[0][1]


def _reassign_iban_fields(document: ReadDocument, document_type: str, fields: list[dict]) -> None:
    iban_items = [
        item for item in fields
        if isinstance(item.get("value"), str)
        and item["value"].startswith("KZ")
        and re.fullmatch(r"KZ[0-9A-Z]{18}", item["value"])
    ]
    for item in iban_items:
        role = _explicit_account_role(document, item["value"])
        if not role:
            continue
        name, label = _field_name_for(role, item["value"])
        item["name"] = name
        item["label_ru"] = label
        item["status"] = "extracted"
        item["confidence"] = max(float(item.get("confidence") or 0), 0.96)
        item["notes"] = "Роль подтверждена явной подписью банковского счёта в документе."

    # A leasing contract has lessee/lessor, not a generic recipient.
    if document_type == "lease_contract":
        fields[:] = [
            item for item in fields
            if item.get("name") not in {
                "recipient_name", "recipient_iin_bin", "recipient_bin", "recipient_iban"
            }
        ]

    # Deduplicate same IBAN and prefer specific leasing/guarantee roles.
    priority = {
        "lessee_iban": 100, "lessor_iban": 100, "guarantor_iban": 100,
        "recipient_iban": 90, "financial_agency_iban": 90,
        "leasing_company_iban": 90, "sender_iban": 50,
    }
    chosen = {}
    others = []
    for item in fields:
        value = item.get("value")
        if isinstance(value, str) and value.startswith("KZ") and not isinstance(value, list):
            score = priority.get(str(item.get("name")), 0) + int(float(item.get("confidence") or 0) * 10)
            current = chosen.get(value)
            if current is None or score > current[0]:
                chosen[value] = (score, item)
        else:
            others.append(item)
    fields[:] = others + [entry[1] for entry in chosen.values()]


def _explicit_guarantor_identifier(document: ReadDocument) -> tuple[str, int, str, str] | None:
    patterns = (
        r"(?:ЛИЧН(?:УЮ|АЯ)\s+ГАРАНТИЮ|ГАРАНТ|КЕПІЛГЕР).{0,400}?"
        r"(?:ИИН|ЖСН)\s*[:№-]?\s*(\d{12})",
        r"(?:ИИН|ЖСН)\s*[:№-]?\s*(\d{12}).{0,300}?"
        r"(?:ДОГОВОР[АУ]?\s+ГАРАНТИИ|КЕПІЛДІК\s+ШАРТ)",
    )
    for page in document.pages:
        for pattern in patterns:
            match = re.search(pattern, page.text, re.I | re.S)
            if match:
                return (
                    match.group(1), page.page_number,
                    page.text[max(0, match.start()-150):match.end()+150],
                    page.extraction_method,
                )
    return None


def _guarantee_header_date(document: ReadDocument) -> tuple[str, int, str, str] | None:
    for page in document.pages[:2]:
        # Search a short region after the guarantee title/number; this prevents
        # a later power-of-attorney date from replacing the document date.
        title = re.search(
            r"(?:ДОГОВОР\s+ГАРАНТИИ|КЕПІЛДІК\s+ШАРТЫ).{0,180}?"
            r"(?:№|NE|Nº)?\s*[A-ZА-Я0-9/]+",
            page.text, re.I | re.S,
        )
        region_start = title.start() if title else 0
        region_end = min(len(page.text), (title.end() if title else 0) + 350)
        region = page.text[region_start:region_end]
        numeric = re.search(r"\b(\d{2}[.\-/]\d{2}[.\-/]20\d{2})\b", region)
        if numeric:
            value = numeric.group(1).replace("/", ".").replace("-", ".")
            return value, page.page_number, region, page.extraction_method
        words = re.search(
            r"[«\"]?(\d{1,2})[»\"]?\s*"
            r"(января|февраля|марта|апреля|мая|июня|июля|августа|"
            r"сентября|октября|ноября|декабря)\s*(20\d{2})",
            region, re.I,
        )
        if words:
            return _date_iso(*words.groups()), page.page_number, region, page.extraction_method
    return None


def _guarantee_parties(document: ReadDocument) -> dict[str, tuple[str, int, str, str]]:
    result = {}
    for page in document.pages:
        text = page.text
        # Robust to OCR punctuation and mixed Kazakh/Russian layouts.
        lessee = re.search(
            r"(?:ТОО|ЖШС|ТОВАРИЩЕСТВО\s+С\s+ОГРАНИЧЕННОЙ\s+ОТВЕТСТВЕННОСТЬЮ)"
            r"\s*[«\"]?\s*([A-ZА-ЯЁ][A-ZА-ЯЁa-zа-яё0-9 .&_-]{2,80})[»\"]?"
            r".{0,260}?(?:ЛИЗИНГОПОЛУЧАТЕЛ|ЛИЗИНГ\s+АЛУШ)",
            text, re.I | re.S,
        )
        if lessee:
            value = re.sub(r"\s+", " ", lessee.group(1)).strip(" ,.;:\"")
            value = re.split(r"\s+(?:БИН|БСН|В\s+ЛИЦЕ|АТЫНАН)\b", value, 1, flags=re.I)[0].strip()
            if value and value != "—":
                result["lessee"] = (
                    value, page.page_number,
                    text[max(0, lessee.start()-120):lessee.end()+120],
                    page.extraction_method,
                )

        guarantor = re.search(
            r"(?:ГРАЖДАН(?:КА|ИН)|ГАРАНТ|КЕПІЛГЕР).{0,160}?"
            r"([А-ЯЁҰҮҚҒӨӘІ][А-Яа-яЁёҰұҮүҚқҒғӨөӘәІі-]+\s+"
            r"[А-ЯЁҰҮҚҒӨӘІ][А-Яа-яЁёҰұҮүҚқҒғӨөӘәІі-]+\s+"
            r"[А-ЯЁҰҮҚҒӨӘІ][А-Яа-яЁёҰұҮүҚқҒғӨөӘәІі-]+)",
            text, re.I | re.S,
        )
        if guarantor:
            result["guarantor"] = (
                re.sub(r"\s+", " ", guarantor.group(1)).strip(),
                page.page_number,
                text[max(0, guarantor.start()-120):guarantor.end()+120],
                page.extraction_method,
            )
    return result


def _explicit_guarantee_amount(document: ReadDocument) -> tuple[float, int, str, str] | None:
    patterns = (
        r"(?:ГАРАНТ.{0,500}?(?:ОБЯЗУЕТСЯ|ОТВЕЧАЕТ)|РАЗМЕРЕ)"
        r".{0,180}?(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?)",
        r"(?:СУММА\s+ГАРАНТИИ|КЕПІЛДІК\s+СОМАСЫ)\s*[:\-]?\s*"
        r"(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?)",
    )
    for page in document.pages[:3]:
        for pattern in patterns:
            match = re.search(pattern, page.text, re.I | re.S)
            if match:
                amount = parse_money(match.group(1))
                if amount and float(amount) >= 100000:
                    return (
                        float(amount), page.page_number,
                        page.text[max(0, match.start()-180):match.end()+180],
                        page.extraction_method,
                    )
    return None


def _clean_party_fields(document_type: str, fields: list[dict]) -> None:
    fields[:] = [
        item for item in fields
        if str(item.get("value") or "").strip() not in {"", "—", "-", "None"}
    ]
    if document_type == "guarantee_contract":
        fields[:] = [
            item for item in fields
            if item.get("name") not in {"recipient_name", "recipient_iin_bin", "recipient_bin", "recipient_iban"}
        ]


def improve_fields(document: ReadDocument, document_type: str, fields: list[dict],
                   tables: list[dict] | None = None) -> list[dict]:
    result = deepcopy(fields)

    # Correct document dates.
    if document_type == "addendum":
        signed = _find_signature_date(document)
        if signed:
            value, page, quote, method = signed
            _upsert(result, field(
                name="addendum_date",
                label_ru="Дата дополнительного соглашения",
                value=value,
                page=page,
                quote=quote,
                confidence=0.99 if method == "digital" else 0.92,
                extraction_method=method,
                notes="Использована электронная дата подписания документа.",
            ))
    elif document_type in {"lease_contract", "guarantee_contract"}:
        heading_date = _find_heading_date(document)
        if heading_date:
            value, page, quote, method = heading_date
            name = "lease_contract_date" if document_type == "lease_contract" else "guarantee_contract_date"
            label = "Дата договора лизинга" if document_type == "lease_contract" else "Дата договора гарантии"
            _upsert(result, field(
                name=name, label_ru=label, value=value, page=page, quote=quote,
                confidence=0.98 if method == "digital" else 0.88,
                extraction_method=method,
            ))

    # Contract numbers and OCR repair.
    contracts = _contract_candidates(document)
    if document_type == "guarantee_contract":
        for value, page, quote, method in contracts:
            if "/W/P/" in value:
                _upsert(result, field(
                    name="guarantee_contract_number",
                    label_ru="Номер договора гарантии",
                    value=value, page=page, quote=quote,
                    confidence=0.90 if method == "ocr" else 0.98,
                    extraction_method=method,
                    notes="Номер нормализован по структуре договора.",
                ))
            elif "/U/S/" in value or "/U/L/" in value:
                _upsert(result, field(
                    name="linked_lease_contract_number",
                    label_ru="Связанный договор финансового лизинга",
                    value=value, page=page, quote=quote,
                    confidence=0.86 if method == "ocr" else 0.97,
                    extraction_method=method,
                    notes="Исправлены типичные OCR-подмены в сегментах номера.",
                ))

        # Guarantee amount from explicit obligation amount.
        for page in document.pages[:2]:
            match = re.search(
                r"(?:в\s+размере|мөлшерінде)\s*(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?)",
                page.text, re.I,
            )
            if match:
                amount = parse_money(match.group(1))
                if amount:
                    _upsert(result, field(
                        name="guarantee_amount_kzt",
                        label_ru="Сумма гарантии, тенге",
                        value=amount, page=page.page_number,
                        quote=page.text[max(0, match.start()-150):match.end()+150],
                        confidence=0.88 if page.extraction_method == "ocr" else 0.98,
                        extraction_method=page.extraction_method,
                    ))
                    break

    if document_type == "lease_contract":
        # Related guarantee number and nominal rate.
        for value, page, quote, method in contracts:
            if "/W/P/" in value:
                _upsert(result, field(
                    name="guarantee_contract_number",
                    label_ru="Связанный договор гарантии",
                    value=value, page=page, quote=quote,
                    confidence=0.96 if method == "digital" else 0.84,
                    extraction_method=method,
                ))
        for page in document.pages[:5]:
            match = re.search(
                r"(?:ставк[аи]\s+вознаграждения|сыйақы\s+мөлшерлемесі).{0,100}?(\d{1,2}(?:[,.]\d+)?)\s*%",
                page.text, re.I | re.S,
            )
            if match:
                _upsert(result, field(
                    name="nominal_rate_percent",
                    label_ru="Ставка вознаграждения, %",
                    value=float(match.group(1).replace(",", ".")),
                    page=page.page_number,
                    quote=page.text[max(0, match.start()-120):match.end()+120],
                    confidence=0.94 if page.extraction_method == "digital" else 0.80,
                    extraction_method=page.extraction_method,
                ))
                break

    # Use visual requisites columns for unresolved IDs/IBANs.
    _assign_layout_candidates(document, result)

    # Correct party names.
    role_to_name = {
        "lessee": ("lessee_name", "Лизингополучатель"),
        "recipient": ("recipient_name", "Получатель"),
        "borrower": ("borrower_name", "Заёмщик"),
        "guarantor": ("guarantor_name", "Гарант"),
    }
    for role, (name, label) in role_to_name.items():
        found = _party_name(document, role)
        if found:
            value, page, quote, method = found
            _upsert(result, field(
                name=name, label_ru=label, value=value,
                page=page, quote=quote,
                confidence=0.95 if method == "digital" else 0.82,
                extraction_method=method,
            ))

    # A wrongly inferred sender must not override an explicit recipient.
    recipient_ids = {
        str(item.get("value")) for item in result
        if item.get("name") in {"recipient_iin_bin", "recipient_bin"}
    }
    result = [
        item for item in result
        if not (
            item.get("name") == "sender_iin_bin"
            and str(item.get("value")) in recipient_ids
        )
    ]

    # Targeted semantic corrections from the real regression documents.
    if document_type == "lease_contract":
        explicit_guarantor = _explicit_guarantor_identifier(document)
        if explicit_guarantor:
            value, page, quote, method = explicit_guarantor
            _upsert(result, field(
                name="guarantor_iin_bin",
                label_ru="ИИН/БИН — Гарант",
                value=value,
                page=page,
                quote=quote,
                confidence=0.96 if method == "digital" else 0.86,
                extraction_method=method,
                status="extracted",
                notes="Роль подтверждена условием личной гарантии.",
            ))

    if document_type == "addendum":
        recipient_name = _existing(result, "recipient_name")
        recipient_value = recipient_name.get("value") if recipient_name else None
        # Electronic receipt: the company identified as Recipient must own its BIN.
        for item in list(result):
            if item.get("name") == "sender_iin_bin":
                quote = str(item.get("quote") or "").upper()
                if "ПОЛУЧАТЕЛ" in quote and str(item.get("value") or "") in quote:
                    item["name"] = "recipient_iin_bin"
                    item["label_ru"] = "ИИН/БИН — Получатель"
                    item["status"] = "extracted"
                    item["confidence"] = 0.96
                    item["notes"] = "Роль исправлена по электронной квитанции: организация указана Получателем."
        if recipient_value:
            for item in result:
                if item.get("name") == "recipient_iin_bin":
                    item["related_party_name"] = recipient_value

    if document_type == "guarantee_contract":
        header_date = _guarantee_header_date(document)
        if header_date:
            value, page, quote, method = header_date
            _upsert(result, field(
                name="guarantee_contract_date",
                label_ru="Дата договора гарантии",
                value=value,
                page=page,
                quote=quote,
                confidence=0.96 if method == "digital" else 0.90,
                extraction_method=method,
                status="extracted",
                notes="Дата взята из заголовка договора рядом с его номером.",
            ))

        parties = _guarantee_parties(document)
        if "lessee" in parties:
            value, page, quote, method = parties["lessee"]
            _upsert(result, field(
                name="lessee_name", label_ru="Лизингополучатель",
                value=value, page=page, quote=quote,
                confidence=0.94 if method == "digital" else 0.84,
                extraction_method=method, status="extracted",
            ))
        if "guarantor" in parties:
            value, page, quote, method = parties["guarantor"]
            _upsert(result, field(
                name="guarantor_name", label_ru="Гарант",
                value=value, page=page, quote=quote,
                confidence=0.94 if method == "digital" else 0.84,
                extraction_method=method, status="extracted",
            ))

        amount = _explicit_guarantee_amount(document)
        if amount:
            value, page, quote, method = amount
            _upsert(result, field(
                name="guarantee_amount_kzt",
                label_ru="Сумма гарантии, тенге",
                value=value, page=page, quote=quote,
                confidence=0.96 if method == "digital" else 0.88,
                extraction_method=method, status="extracted",
            ))

    _reassign_iban_fields(document, document_type, result)
    _clean_party_fields(document_type, result)
    _remove_duplicate_candidates(result, tables)
    return result
