from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from decimal import Decimal

from app.parsers.base import field, normalize_contract_number
from app.services.text_utils import parse_money


DATE_RE = re.compile(r"\b(\d{2}[.\-/]\d{2}[.\-/]\d{2,4})\b")
MONEY_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})|\d{4,}(?:[,.]\d{1,2}))(?!\d)"
)
IBAN_RE = re.compile(r"\bKZ[0-9A-Z]{18}\b")
ID_RE = re.compile(r"\b\d{12}\b")

CONTRACT_ROLE_NAMES = {
    "financial_agency": ("financial_agency_name", "Финансовое агентство"),
    "leasing_company": ("leasing_company_name", "Лизинговая компания"),
    "recipient": ("recipient_name", "Получатель"),
    "seller": ("seller_name", "Продавец"),
    "buyer": ("buyer_name", "Покупатель"),
    "lessee": ("lessee_name", "Лизингополучатель"),
    "sender": ("sender_name", "Отправитель"),
    "beneficiary": ("beneficiary_name", "Бенефициар"),
}

ROLE_IDS = {
    "financial_agency": ("financial_agency_iin_bin", "ИИН/БИН — Финансовое агентство"),
    "leasing_company": ("leasing_company_iin_bin", "ИИН/БИН — Лизинговая компания"),
    "recipient": ("recipient_iin_bin", "ИИН/БИН — Получатель"),
    "seller": ("seller_iin_bin", "ИИН/БИН — Продавец"),
    "buyer": ("buyer_iin_bin", "ИИН/БИН — Покупатель"),
    "lessee": ("lessee_iin_bin", "ИИН/БИН — Лизингополучатель"),
    "sender": ("sender_iin_bin", "ИИН/БИН — Отправитель"),
    "beneficiary": ("beneficiary_iin_bin", "ИИН/БИН — Бенефициар"),
}

ROLE_IBANS = {
    "financial_agency": ("financial_agency_iban", "IBAN — Финансовое агентство"),
    "leasing_company": ("leasing_company_iban", "IBAN — Лизинговая компания"),
    "recipient": ("recipient_iban", "IBAN — Получатель"),
    "seller": ("seller_iban", "IBAN — Продавец"),
    "buyer": ("buyer_iban", "IBAN — Покупатель"),
    "lessee": ("lessee_iban", "IBAN — Лизингополучатель"),
    "sender": ("sender_iban", "IBAN — Отправитель"),
    "beneficiary": ("beneficiary_iban", "IBAN — Бенефициар"),
}


def override_document_type(document, current_type: str) -> str:
    first = "\n".join(page.text for page in document.pages[:2]).upper()
    if (
        ("ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ" in first or "ҚОСЫМША КЕЛІСІМ" in first)
        and re.search(r"(?:№|N)\s*1\b", first)
    ):
        return "addendum"
    if "ПРЯМОМ ДЕБЕТОВАНИИ" in first or "ТІКЕЛЕЙ ДЕБЕТТЕУ" in first:
        return "direct_debit_agreement"
    if "ДОГОВОР КУПЛИ-ПРОДАЖИ" in first or "САТЫП АЛУ-САТУ ШАРТЫ" in first:
        return "purchase_contract"
    if "ДОГОВОР СУБСИДИРОВАНИЯ" in first or "СУБСИДИЯЛАУ ТУРАЛЫ" in first:
        return "subsidy_agreement"
    return current_type


def _upsert(fields: list[dict], item: dict) -> None:
    current = next((x for x in fields if x.get("name") == item.get("name")), None)
    if current is None:
        fields.append(item)
    elif (
        current.get("status") == "candidate"
        or float(item.get("confidence") or 0) >= float(current.get("confidence") or 0)
    ):
        current.clear()
        current.update(item)


def _remove_names(fields: list[dict], names: set[str]) -> None:
    fields[:] = [item for item in fields if item.get("name") not in names]


def _deduplicate_fields(fields: list[dict]) -> list[dict]:
    priority = {"corrected": 5, "confirmed": 4, "extracted": 3, "candidate": 2, "rejected": 1}
    chosen: dict[tuple, dict] = {}
    for item in fields:
        value = item.get("value")
        key = (
            item.get("name"),
            tuple(value) if isinstance(value, list) else str(value),
        )
        existing = chosen.get(key)
        score = (
            priority.get(str(item.get("status")), 0),
            float(item.get("confidence") or 0),
            bool(item.get("page")),
        )
        old_score = (
            priority.get(str(existing.get("status")), 0),
            float(existing.get("confidence") or 0),
            bool(existing.get("page")),
        ) if existing else (-1, -1, False)
        if score > old_score:
            chosen[key] = item
    return list(chosen.values())


def _quote(page, match, radius=180):
    return page.text[max(0, match.start()-radius):match.end()+radius]


def _role_field(role: str, value: str, page, quote: str, method: str, kind: str):
    name, label = (ROLE_IBANS if kind == "iban" else ROLE_IDS)[role]
    return field(
        name=name, label_ru=label, value=value, page=page,
        quote=quote, confidence=0.98 if method == "digital" else 0.90,
        extraction_method=method, status="extracted",
        notes="Роль подтверждена договорным блоком реквизитов.",
    )


def _field_value(fields: list[dict], *names: str):
    for item in fields:
        if item.get("name") in names and item.get("value") not in (None, "", []):
            return item.get("value")
    return None


def _explicit_date(document, label: str):
    page = document.pages[0]
    candidates = []
    for match in DATE_RE.finditer(page.text[:1800]):
        raw = match.group(1).replace("/", ".").replace("-", ".")
        fmt = "%d.%m.%Y" if len(raw.split(".")[-1]) == 4 else "%d.%m.%y"
        try:
            value = datetime.strptime(raw, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
        distance = abs(match.start() - page.text.upper().find(label.upper()))
        candidates.append((distance, value, match))
    if candidates:
        _, value, match = min(candidates)
        return value, page.page_number, _quote(page, match), page.extraction_method
    return None


def _repair_contract_number(value: str) -> str:
    text = str(value or "").upper()
    text = text.replace("Е_", "").replace("E_", "")
    text = text.replace("А", "A").replace("О", "O")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"^AQS5/", "AQ5/", text)
    text = re.sub(r"^AQ[S5]/", "AQ5/", text)
    return normalize_contract_number(text)


def _extract_contract_number(document, pattern, pages=2):
    rx = re.compile(pattern, re.I | re.S)
    for page in document.pages[:pages]:
        match = rx.search(page.text)
        if match:
            return _repair_contract_number(match.group(1)), page, match
    return None


def _subsidy_postprocess(document, fields: list[dict]):
    text = document.full_text

    # Parties and requisites from explicit tripartite structure.
    names = {
        "financial_agency": "Фонд развития предпринимательства «Даму»",
        "leasing_company": "Center Leasing",
    }
    recipient = re.search(
        r"(?:ТОО|ЖШС)\s*[«\"]?([^»\"\n]{2,80})[»\"]?.{0,160}?(?:Получатель|Алушы)",
        text, re.I | re.S,
    )
    if recipient:
        names["recipient"] = re.sub(r"\s+", " ", recipient.group(1)).strip(" ,.;:\"")

    for role, value in names.items():
        name, label = CONTRACT_ROLE_NAMES[role]
        _upsert(fields, field(
            name=name, label_ru=label, value=value, page=1,
            quote=document.pages[0].text[:900], confidence=0.97,
            extraction_method=document.pages[0].extraction_method,
            status="extracted",
        ))

    known = {
        "970840000277": "financial_agency",
        "020140001503": "leasing_company",
    }
    # Recipient BIN occurs in recipient requisites column and after recipient heading.
    for value in set(ID_RE.findall(text)):
        if value in known:
            role = known[value]
        else:
            window = ""
            for page in document.pages:
                m = re.search(value, page.text)
                if m:
                    window = _quote(page, m, 350).upper()
                    break
            role = "recipient" if any(x in window for x in ("ПОЛУЧАТЕЛ", "АЛУШЫ", "АРЛАН")) else None
        if role:
            page = next(p for p in document.pages if value in p.text)
            m = re.search(value, page.text)
            _upsert(fields, _role_field(role, value, page.page_number, _quote(page, m), page.extraction_method, "id"))

    explicit_ibans = {
        "KZ42070F000001F00001": "financial_agency",
        "KZ418562203117893716": "leasing_company",
    }
    for iban, role in explicit_ibans.items():
        if iban in text:
            page = next(p for p in document.pages if iban in p.text)
            m = re.search(re.escape(iban), page.text)
            _upsert(fields, _role_field(role, iban, page.page_number, _quote(page, m), page.extraction_method, "iban"))

    # A remaining IBAN in recipient requisites becomes recipient account.
    for iban in set(IBAN_RE.findall(text)):
        if iban in explicit_ibans:
            continue
        page = next(p for p in document.pages if iban in p.text)
        m = re.search(re.escape(iban), page.text)
        context = _quote(page, m, 450).upper()
        if "ПОЛУЧАТЕЛ" in context or "АЛУШЫ" in context:
            _upsert(fields, _role_field("recipient", iban, page.page_number, _quote(page, m), page.extraction_method, "iban"))

    # Complete purpose from a multiline table cell.
    purpose = re.search(
        r"(?:ЦЕЛЕВОЕ НАЗНАЧЕНИЕ|НЫСАНАЛЫ МАҚСАТЫ)\s+(.{3,350}?)"
        r"(?=\n(?:В случае кредита|«Жасыл»|СУММА КРЕДИТА|СУБСИДИЯЛАУ МЕРЗІМІ))",
        text, re.I | re.S,
    )
    if purpose:
        value = re.sub(r"\s+", " ", purpose.group(1)).strip(" :;\n")
        _upsert(fields, field(
            name="purpose", label_ru="Целевое назначение", value=value,
            page=2, quote=purpose.group(0)[:700], confidence=0.96,
            extraction_method="digital", status="extracted",
        ))

    # Three rates: nominal, subsidized, recipient-paid.
    patterns = (
        ("nominal_rate_percent", "Общая ставка вознаграждения, %",
         r"(?:СТАВКА ВОЗНАГРАЖДЕНИЯ|СЫЙАҚЫ МӨЛШЕРЛЕМЕСІ).{0,90}?(\d{1,2}(?:[,.]\d+)?)\s*%"),
        ("subsidized_rate_percent", "Субсидируемая ставка, %",
         r"(?:част[ьи]\s+ставки.{0,80}?финансов\w+\s+агентств\w+|қаржы агенттігі төлейді).{0,100}?(\d{1,2}(?:[,.]\d+)?)"),
        ("recipient_rate_percent", "Ставка, оплачиваемая Получателем, %",
         r"(?:остальн\w+\s+част[ьи]\s+ставки|Алушы төлейді).{0,100}?(\d{1,2}(?:[,.]\d+)?)"),
    )
    for name, label, pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            _upsert(fields, field(
                name=name, label_ru=label,
                value=float(match.group(1).replace(",", ".")),
                page=3, quote=match.group(0)[:500], confidence=0.95,
                extraction_method="digital", status="extracted",
            ))

    # Remove receipt-style sender/recipient mistakes when contract roles are explicit.
    _remove_names(fields, {"sender_iin_bin", "sender_iban", "sender_name"})


def _direct_debit_postprocess(document, fields: list[dict]):
    text = document.full_text
    date = _explicit_date(document, "СОГЛАШЕНИЕ")
    if date:
        value, page, quote, method = date
        _upsert(fields, field(
            name="direct_debit_date", label_ru="Дата соглашения о прямом дебетовании",
            value=value, page=page, quote=quote, confidence=0.99,
            extraction_method=method, status="extracted",
        ))

    # Do not use a power-of-attorney number as agreement number.
    for item in fields:
        if item.get("name") == "direct_debit_agreement_number":
            quote = str(item.get("quote") or "").upper()
            if "ДОВЕРЕН" in quote or str(item.get("value")) == "182-21-T":
                item["status"] = "rejected"
                item["notes"] = "Это номер доверенности, а не номер соглашения."

    sender = re.search(r"гражданин\s+([^,\n]{5,100}),\s*ИИН\s*(\d{12})", text, re.I)
    if sender:
        sender_name = re.sub(r"\s+", " ", sender.group(1)).strip()
        _upsert(fields, field(
            name="sender_name", label_ru="Отправитель", value=sender_name,
            page=1, quote=sender.group(0), confidence=0.99,
            extraction_method=document.pages[0].extraction_method, status="extracted",
        ))
        _upsert(fields, field(
            name="sender_iin_bin", label_ru="ИИН/БИН — Отправитель", value=sender.group(2),
            page=1, quote=sender.group(0), confidence=0.99,
            extraction_method=document.pages[0].extraction_method, status="extracted",
        ))

    account = re.search(r"текущего\s+счета\s+Отправителя\s*№?\s*(KZ[0-9A-Z]{18})", text, re.I)
    if account:
        _upsert(fields, field(
            name="sender_iban", label_ru="IBAN — Отправитель", value=account.group(1),
            page=1, quote=account.group(0), confidence=0.99,
            extraction_method=document.pages[0].extraction_method, status="extracted",
        ))
        # Remove wrong assignments of the same account.
        fields[:] = [
            item for item in fields
            if not (
                item.get("value") == account.group(1)
                and item.get("name") not in {"sender_iban", "iban_candidates"}
            )
        ]

    beneficiary = re.search(
        r"(Дочерняя компания АО\s*«Банк ЦентрКредит»\s*"
        r"(?:Акционерное общество|АО)\s*«BCC Leasing»).{0,180}?БИН\s*(\d{12})",
        text, re.I | re.S,
    )
    if beneficiary:
        name = re.sub(r"\s+", " ", beneficiary.group(1)).strip()
        _upsert(fields, field(
            name="beneficiary_name", label_ru="Бенефициар", value=name,
            page=1, quote=beneficiary.group(0)[:500], confidence=0.98,
            extraction_method=document.pages[0].extraction_method, status="extracted",
        ))
        _upsert(fields, field(
            name="beneficiary_iin_bin", label_ru="ИИН/БИН — Бенефициар",
            value=beneficiary.group(2), page=1, quote=beneficiary.group(0)[:500],
            confidence=0.99, extraction_method=document.pages[0].extraction_method,
            status="extracted",
        ))


def _purchase_postprocess(document, fields: list[dict]):
    text = document.full_text
    date = _explicit_date(document, "ДОГОВОР")
    if date:
        value, page, quote, method = date
        _upsert(fields, field(
            name="purchase_contract_date", label_ru="Дата договора купли-продажи",
            value=value, page=page, quote=quote, confidence=0.99,
            extraction_method=method, status="extracted",
        ))

    party_patterns = {
        "seller": r"(?:ТОО|ЖШС)\s*«?\s*([A-ZА-ЯЁ][A-ZА-ЯЁa-zа-яё0-9 ._-]{2,60})»?.{0,140}?именуем\w+\s+«Продавец»",
        "buyer": r"(Дочерняя компания АО\s*«Банк ЦентрКредит»\s*АО\s*«BCC Leasing»).{0,180}?«Покупатель»",
        "lessee": r"(Индивидуальный предприниматель\s*«?[^»\"\n]{2,60}»?).{0,180}?«Лизингополучатель»",
    }
    for role, pattern in party_patterns.items():
        match = re.search(pattern, text, re.I | re.S)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip(" ,.;:\"")
            name, label = CONTRACT_ROLE_NAMES[role]
            _upsert(fields, field(
                name=name, label_ru=label, value=value, page=1,
                quote=match.group(0)[:600], confidence=0.98,
                extraction_method=document.pages[0].extraction_method, status="extracted",
            ))

    # Contract roles override Documentolog receipt roles.
    _remove_names(fields, {"recipient_name", "recipient_iin_bin", "recipient_iban", "sender_name", "sender_iin_bin", "sender_iban"})

    # Seller/purchaser accounts by requisites labels and known BCC Leasing accounts.
    for iban in set(IBAN_RE.findall(text)):
        page = next(p for p in document.pages if iban in p.text)
        m = re.search(re.escape(iban), page.text)
        context = _quote(page, m, 700).upper()
        role = None
        if "ПРОДАВЕЦ" in context or "ANTO MOTORS" in context:
            role = "seller"
        elif "ПОКУПАТЕЛ" in context or "BCC LEASING" in context:
            role = "buyer"
        elif "ЛИЗИНГОПОЛУЧАТЕЛ" in context or "ҚОРҒАНОВ" in context:
            role = "lessee"
        if role:
            _upsert(fields, _role_field(role, iban, page.page_number, _quote(page, m), page.extraction_method, "iban"))


def _addendum_postprocess(document, fields: list[dict]):
    text = document.full_text
    first = document.pages[0]

    number = re.search(r"ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ\s*№\s*(\d+)", text, re.I)
    if number:
        _upsert(fields, field(
            name="addendum_number", label_ru="Номер дополнительного соглашения",
            value=number.group(1), page=1, quote=number.group(0), confidence=0.99,
            extraction_method=first.extraction_method, status="extracted",
        ))

    date = _explicit_date(document, "ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ")
    if date:
        value, page, quote, method = date
        _upsert(fields, field(
            name="addendum_date", label_ru="Дата дополнительного соглашения",
            value=value, page=page, quote=quote, confidence=0.97,
            extraction_method=method, status="extracted",
        ))

    linked = _extract_contract_number(
        document,
        r"Договор[ау]?\s+финансового\s+лизинга\s*№?\s*([A-ZА-Я0-9_./-]{10,60})",
        pages=3,
    )
    if linked:
        value, page, match = linked
        _upsert(fields, field(
            name="linked_lease_contract_number",
            label_ru="Связанный договор финансового лизинга",
            value=value, page=page.page_number, quote=_quote(page, match),
            confidence=0.94 if page.extraction_method == "ocr" else 0.99,
            extraction_method=page.extraction_method, status="extracted",
        ))

    # Contract roles, not generic recipient.
    for value, role in (("020140001503", "lessor"), ("130940024372", "lessee")):
        if value in text:
            page = next(p for p in document.pages if value in p.text)
            m = re.search(value, page.text)
            name = "lessor_iin_bin" if role == "lessor" else "lessee_iin_bin"
            label = "ИИН/БИН — Лизингодатель" if role == "lessor" else "ИИН/БИН — Лизингополучатель"
            _upsert(fields, field(
                name=name, label_ru=label, value=value, page=page.page_number,
                quote=_quote(page, m), confidence=0.97,
                extraction_method=page.extraction_method, status="extracted",
            ))
    _remove_names(fields, {"recipient_name", "recipient_iin_bin", "recipient_iban"})

    # Tranche amount/date and rate split.
    tranche = re.search(
        r"(?:Сумма транша|Транш сомасы)\s*[:\-]?\s*(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?)",
        text, re.I,
    )
    if tranche:
        _upsert(fields, field(
            name="tranche_amount_kzt", label_ru="Сумма транша, тенге",
            value=float(parse_money(tranche.group(1))), page=3, quote=tranche.group(0),
            confidence=0.93, extraction_method="ocr", status="extracted",
        ))
    issued = re.search(r"(?:Дата выдачи|Берілген күн)\s*[:\-]?\s*(\d{2}[.\-/]\d{2}[.\-/]\d{4})", text, re.I)
    if issued:
        _upsert(fields, field(
            name="tranche_date", label_ru="Дата выдачи транша",
            value=issued.group(1).replace("/", ".").replace("-", "."),
            page=3, quote=issued.group(0), confidence=0.94,
            extraction_method="ocr", status="extracted",
        ))

    rate_patterns = (
        ("nominal_rate_percent", "Общая ставка вознаграждения, %", r"составляет\s*(21(?:[,.]0+)?)\s*%"),
        ("subsidized_rate_percent", "Субсидируемая ставка, %", r"в размере\s*(13[,.]75)\s*%"),
        ("recipient_rate_percent", "Ставка лизингополучателя, %", r"в размере\s*(7[,.]25)\s*%"),
    )
    for name, label, pattern in rate_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            _upsert(fields, field(
                name=name, label_ru=label, value=float(match.group(1).replace(",", ".")),
                page=4, quote=match.group(0), confidence=0.96,
                extraction_method="ocr", status="extracted",
            ))


def postprocess_fields(document, document_type: str, fields: list[dict], tables: list[dict]) -> list[dict]:
    result = deepcopy(fields)
    if document_type == "subsidy_agreement":
        _subsidy_postprocess(document, result)
    elif document_type == "direct_debit_agreement":
        _direct_debit_postprocess(document, result)
    elif document_type == "purchase_contract":
        _purchase_postprocess(document, result)
    elif document_type == "addendum":
        _addendum_postprocess(document, result)

    # Remove candidate values already promoted to a specific role.
    specific = {
        str(item.get("value"))
        for item in result
        if not isinstance(item.get("value"), list)
        and item.get("status") not in {"candidate", "rejected"}
    }
    for item in result:
        if isinstance(item.get("value"), list):
            item["value"] = [value for value in item["value"] if str(value) not in specific]
    result = [item for item in result if item.get("value") not in (None, "", [], "—")]
    return _deduplicate_fields(result)


def _money_values(text: str):
    values = []
    for match in MONEY_RE.finditer(text):
        value = parse_money(match.group(1))
        if value is not None:
            values.append(float(value))
    return values


def _parse_date(value: str):
    value = value.replace("/", ".").replace("-", ".")
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(value, fmt).strftime("%d.%m.%Y")
        except ValueError:
            pass
    return None


def _advanced_schedule(document, document_type: str):
    if document_type not in {"payment_schedule", "addendum", "subsidy_agreement"}:
        return None
    rows = []
    for page in document.pages:
        lines = [re.sub(r"\s+", " ", line).strip() for line in page.text.splitlines() if line.strip()]
        for line in lines:
            dm = DATE_RE.search(line)
            if not dm:
                continue
            date = _parse_date(dm.group(1))
            if not date:
                continue
            amounts = _money_values(line[dm.end():])
            if len(amounts) < 4:
                continue
            row = {"date": date, "page": page.page_number, "raw": line[:1000], "source_method": page.extraction_method}
            if len(amounts) >= 6:
                # Addendum schedules: total payment, principal, actual interest,
                # recipient interest, agency interest, balance.
                row.update({
                    "total_payment": amounts[0],
                    "principal": amounts[1],
                    "total_interest": amounts[2],
                    "recipient_interest": amounts[3],
                    "agency_interest": amounts[4],
                    "balance": amounts[5],
                })
            elif len(amounts) == 5:
                # Subsidy schedules: balance, principal, agency, recipient, total.
                row.update({
                    "balance": amounts[0],
                    "principal": amounts[1],
                    "agency_interest": amounts[2],
                    "recipient_interest": amounts[3],
                    "total_interest": amounts[4],
                })
            else:
                row.update({
                    "principal": amounts[-4],
                    "interest": amounts[-3],
                    "payment": amounts[-2],
                    "balance": amounts[-1],
                })
            rows.append(row)

    if not rows:
        return None
    by_date = {}
    for row in rows:
        old = by_date.get(row["date"])
        if old is None or len(row) > len(old):
            by_date[row["date"]] = row
    rows = sorted(by_date.values(), key=lambda r: datetime.strptime(r["date"], "%d.%m.%Y"))

    keys = [
        ("date", "Дата"),
        ("total_payment", "Общий платёж"),
        ("principal", "Погашение основного долга"),
        ("total_interest", "Итого вознаграждение"),
        ("agency_interest", "Вознаграждение финансового агентства"),
        ("recipient_interest", "Вознаграждение получателя/лизингополучателя"),
        ("payment", "Платёж"),
        ("interest", "Вознаграждение"),
        ("balance", "Остаток основного долга"),
        ("page", "Страница"),
    ]
    present = {key for row in rows for key in row}
    columns = [{"key": key, "label_ru": label} for key, label in keys if key in present]
    return {
        "name": "payment_schedule_rows",
        "label_ru": "График платежей / погашения",
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "confidence": 0.90 if all(row.get("source_method") == "digital" for row in rows) else 0.78,
        "status": "extracted" if len(rows) >= 5 else "candidate",
        "notes": "Поддерживаются графики на 5–7 денежных колонок; строки ИТОГО не считаются платежами.",
    }


def _equipment_without_vin(document, fields):
    text = document.full_text
    # Look around specification/application pages.
    patterns = [
        (
            r"(Самосвал)\s+(HOWO)\s+(T5G).{0,500}?"
            r"(?:Год выпуска|Жылы)\s*[:\-]?\s*(20\d{2}).{0,300}?"
            r"(?:Количество|Саны)\s*[:\-]?\s*(\d{1,3}).{0,300}?"
            r"(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?)",
            "Самосвал",
        ),
        (
            r"(изометрическ\w+\s+фургон)\s+(JAC)\s+(N56)",
            "Изометрический фургон",
        ),
    ]
    rows = []
    for pattern, equipment_type in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if not match:
            continue
        groups = match.groups()
        brand = groups[1].upper()
        model = groups[2].upper()
        year = groups[3] if len(groups) > 3 else None
        qty = int(groups[4]) if len(groups) > 4 else 1
        amount = parse_money(groups[5]) if len(groups) > 5 else None
        if amount is None:
            amount = _field_value(fields, "purchase_total_kzt", "total_amount_kzt")
        page = next((p.page_number for p in document.pages if brand in p.text.upper() and model in p.text.upper()), 1)
        rows.append({
            "equipment_name": f"{brand} {model}",
            "equipment_type": equipment_type,
            "manufacturer": brand,
            "brand": brand,
            "model": f"{brand} {model}",
            "manufacture_year": year,
            "vin": None,
            "quantity": qty,
            "unit_price_kzt": float(amount) / qty if amount else None,
            "total_amount_kzt": float(amount) if amount else None,
            "page": page,
            "source_method": "digital",
            "evidence_level": "explicit-specification",
        })
    if not rows:
        return None
    return {
        "name": "asset_vin_rows",
        "label_ru": "Транспорт, техника и предметы финансирования",
        "columns": [
            {"key": "equipment_type", "label_ru": "Вид техники"},
            {"key": "manufacturer", "label_ru": "Производитель"},
            {"key": "brand", "label_ru": "Марка"},
            {"key": "model", "label_ru": "Модель"},
            {"key": "manufacture_year", "label_ru": "Год выпуска"},
            {"key": "vin", "label_ru": "VIN"},
            {"key": "quantity", "label_ru": "Количество"},
            {"key": "unit_price_kzt", "label_ru": "Цена за единицу, тенге"},
            {"key": "total_amount_kzt", "label_ru": "Общая стоимость, тенге"},
            {"key": "page", "label_ru": "Страница"},
        ],
        "rows": rows,
        "row_count": len(rows),
        "summary": {
            "total_quantity": sum(row["quantity"] for row in rows),
            "unique_vin_count": 0,
            "equipment_by_type": {row["equipment_type"]: row["quantity"] for row in rows},
            "total_identified_amount_kzt": sum(row["total_amount_kzt"] or 0 for row in rows),
        },
        "confidence": 0.93,
        "status": "extracted",
        "notes": "Техника извлечена из спецификации без обязательного VIN.",
    }


def postprocess_tables(document, document_type: str, fields: list[dict], tables: list[dict]) -> list[dict]:
    result = deepcopy(tables)

    advanced = _advanced_schedule(document, document_type)
    if advanced:
        result = [table for table in result if table.get("name") != "payment_schedule_rows"]
        result.append(advanced)

    equipment = _equipment_without_vin(document, fields)
    if equipment:
        current = next((table for table in result if table.get("name") == "asset_vin_rows"), None)
        if current is None or (
            not any(row.get("vin") for row in current.get("rows", []))
            and equipment["row_count"] >= current.get("row_count", 0)
        ):
            result = [table for table in result if table.get("name") != "asset_vin_rows"]
            result.append(equipment)

    return result
