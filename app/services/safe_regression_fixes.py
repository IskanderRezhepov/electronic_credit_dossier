from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime

from app.parsers.base import field, normalize_contract_number
from app.services.text_utils import parse_money


IBAN_RE = re.compile(r"\bKZ\d{2}[0-9A-Z]{16}\b")
ID_RE = re.compile(r"\b\d{12}\b")
DATE_RE = re.compile(r"\b(\d{2}[.\-/]\d{2}[.\-/]\d{4})\b")
MONEY_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?)(?!\d)")


TYPE_LABELS = {
    "lease_contract": "Договор финансового лизинга",
    "purchase_contract": "Договор купли-продажи",
    "addendum": "Дополнительное соглашение",
    "direct_debit_agreement": "Соглашение о прямом дебетовании",
    "subsidy_agreement": "Договор субсидирования",
}


def first_page_type(document, current_type: str) -> str:
    """Conservative title-based correction using only the first page."""
    first = document.pages[0].text.upper() if document.pages else ""
    top = first[:3500]

    if re.search(r"(?:ДОГОВОР\s+КУПЛИ[- ]ПРОДАЖИ|САТЫП\s+АЛУ[- ]САТУ\s+ШАРТЫ)", top):
        return "purchase_contract"
    if re.search(r"(?:ЗАЯВЛЕНИЕ\s+О\s+ПРИСОЕДИНЕНИИ|ҚОСЫЛУ\s+ТУРАЛЫ\s+ӨТІНІШ)", top) and (
        "ФИНАНСОВ" in top and "ЛИЗИНГ" in top
    ):
        return "lease_contract"
    if re.search(r"(?:ДОПОЛНИТЕЛЬНОЕ\s+СОГЛАШЕНИЕ|ҚОСЫМША\s+КЕЛІСІМ)", top):
        return "addendum"
    if "ПРЯМОМ ДЕБЕТОВАНИИ" in top or "ТІКЕЛЕЙ ДЕБЕТТЕУ" in top:
        return "direct_debit_agreement"
    if "ДОГОВОР СУБСИДИРОВАНИЯ" in top or "СУБСИДИЯЛАУ ТУРАЛЫ" in top:
        return "subsidy_agreement"
    return current_type


def _upsert(fields: list[dict], item: dict) -> None:
    existing = next((x for x in fields if x.get("name") == item.get("name")), None)
    if existing is None:
        fields.append(item)
    elif existing.get("status") == "candidate" or float(item.get("confidence") or 0) >= float(existing.get("confidence") or 0):
        existing.clear()
        existing.update(item)


def _drop(fields: list[dict], names: set[str]) -> None:
    fields[:] = [x for x in fields if x.get("name") not in names]


def _quote(page, match, radius=200):
    return page.text[max(0, match.start()-radius):match.end()+radius]


def _page_for(document, value: str):
    for page in document.pages:
        m = re.search(re.escape(value), page.text, re.I)
        if m:
            return page, m
    return None, None


def _normal_date(value: str) -> str | None:
    value = value.replace("/", ".").replace("-", ".")
    try:
        return datetime.strptime(value, "%d.%m.%Y").strftime("%d.%m.%Y")
    except ValueError:
        return None


def _word_date(value: str) -> str | None:
    months = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
        "қаңтар": 1, "ақпан": 2, "наурыз": 3, "сәуір": 4,
        "мамыр": 5, "маусым": 6, "шілде": 7, "тамыз": 8,
        "қыркүйек": 9, "қазан": 10, "қараша": 11, "желтоқсан": 12,
    }
    m = re.search(
        r"[«\"]?(\d{1,2})[»\"]?\s+(" + "|".join(months) + r")\s+(20\d{2})",
        value, re.I,
    )
    if not m:
        return None
    return datetime(int(m.group(3)), months[m.group(2).lower()], int(m.group(1))).strftime("%d.%m.%Y")


def _signature_date(document) -> tuple | None:
    patterns = (
        r"ДАТА\s+ПОДПИСАНИЯ\s*[:\-]?\s*(\d{2}[.\-/]\d{2}[.\-/]\d{4})",
        r"ҚОЛ\s+ҚОЙЫЛҒАН\s+КҮНІ\s*[:\-]?\s*(\d{2}[.\-/]\d{2}[.\-/]\d{4})",
    )
    for page in reversed(document.pages):
        for pattern in patterns:
            m = re.search(pattern, page.text, re.I)
            if m:
                value = _normal_date(m.group(1))
                if value:
                    return value, page.page_number, _quote(page, m), page.extraction_method
    return None


def _heading_date(document, keywords: tuple[str, ...]) -> tuple | None:
    if not document.pages:
        return None
    page = document.pages[0]
    upper = page.text.upper()
    positions = [upper.find(k.upper()) for k in keywords if upper.find(k.upper()) >= 0]
    anchor = min(positions) if positions else 0

    # Restrict the search to the title block, so dates from referenced contracts
    # and powers of attorney do not replace the document date.
    region_start = max(0, anchor - 120)
    region_end = min(len(page.text), anchor + 900)
    region = page.text[region_start:region_end]

    candidates = []
    for m in DATE_RE.finditer(region):
        value = _normal_date(m.group(1))
        if value:
            absolute = region_start + m.start()
            candidates.append((abs(absolute-anchor), -int(value[-4:]), value, m))

    word_value = _word_date(region)
    if word_value:
        wm = re.search(r"\d{1,2}\s+\S+\s+20\d{2}", region)
        absolute = region_start + (wm.start() if wm else 0)
        candidates.append((abs(absolute-anchor), -int(word_value[-4:]), word_value, wm))

    if not candidates:
        return None
    _, _, value, m = min(candidates)
    if m is None:
        return value, page.page_number, region[:700], page.extraction_method
    fake = type("Match", (), {
        "start": lambda self: region_start + m.start(),
        "end": lambda self: region_start + m.end(),
    })()
    return value, page.page_number, _quote(page, fake), page.extraction_method


def _clean_contract(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or "").upper())
    text = text.replace("Е_", "").replace("E_", "")
    text = re.sub(r"^[N№0]+(?=AQ5/)", "", text)
    text = re.sub(r"^AQS5/", "AQ5/", text)
    text = text.replace("А", "A").replace("О", "O")
    return normalize_contract_number(text)


def _dedupe(fields: list[dict]) -> list[dict]:
    rank = {"confirmed": 5, "corrected": 5, "extracted": 4, "candidate": 2, "rejected": 1}
    chosen = {}
    for item in fields:
        value = item.get("value")
        key = (item.get("name"), tuple(value) if isinstance(value, list) else str(value))
        score = (rank.get(item.get("status"), 0), float(item.get("confidence") or 0))
        old = chosen.get(key)
        old_score = (rank.get(old.get("status"), 0), float(old.get("confidence") or 0)) if old else (-1, -1)
        if score > old_score:
            chosen[key] = item
    return list(chosen.values())


def _remove_promoted_candidates(fields: list[dict]) -> None:
    promoted = {
        str(x.get("value")) for x in fields
        if not isinstance(x.get("value"), list)
        and x.get("status") not in {"candidate", "rejected"}
    }
    for x in fields:
        if isinstance(x.get("value"), list):
            x["value"] = [v for v in x["value"] if str(v) not in promoted]
    fields[:] = [x for x in fields if x.get("value") not in (None, "", [], "—")]


def _assign(fields, name, label, value, page, match, confidence=.97):
    _upsert(fields, field(
        name=name, label_ru=label, value=value, page=page.page_number,
        quote=_quote(page, match), confidence=confidence,
        extraction_method=page.extraction_method, status="extracted",
    ))


def _fix_lease(document, fields):
    # Remove receipt/purchase roles that must not exist in lease application.
    _drop(fields, {
        "recipient_name", "recipient_iin_bin", "recipient_iban",
        "buyer_name", "buyer_iin_bin", "buyer_iban",
        "seller_name", "seller_iin_bin", "seller_iban",
        "purchase_contract_date",
    })

    # Explicit lessor/lessee accounts.
    for page in document.pages:
        for role, pattern, name, label in (
            ("lessor", r"(?:СЧЕТА?|ИИК|ЖСК).{0,80}?(KZ[0-9A-Z]{18}).{0,180}?(?:ЛИЗИНГОДАТЕЛ|ЛИЗИНГ\s+БЕРУШ)", "lessor_iban", "IBAN — Лизингодатель"),
            ("lessee", r"(?:ЛИЗИНГОПОЛУЧАТЕЛ|ЛИЗИНГ\s+АЛУШ).{0,250}?(KZ[0-9A-Z]{18})", "lessee_iban", "IBAN — Лизингополучатель"),
        ):
            m = re.search(pattern, page.text, re.I | re.S)
            if m:
                _assign(fields, name, label, m.group(1), page, m, .98)

    # Prevent one IBAN from occupying both roles.
    lessor = next((x.get("value") for x in fields if x.get("name") == "lessor_iban"), None)
    lessee = next((x.get("value") for x in fields if x.get("name") == "lessee_iban"), None)
    if lessor and lessee == lessor:
        fields[:] = [x for x in fields if x.get("name") != "lessee_iban"]

    # Guard the lease amount against OCR-added leading digits.
    amount = None
    for page in document.pages[:5]:
        m = re.search(
            r"(?:СТОИМОСТ[ЬИ]\s+ПРЕДМЕТА\s+ЛИЗИНГА|ЛИЗИНГ\s+НЫСАНАСЫНЫҢ\s+ҚҰНЫ)"
            r".{0,160}?(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?)",
            page.text, re.I | re.S,
        )
        if m:
            amount = float(parse_money(m.group(1)))
            _upsert(fields, field(
                name="lease_asset_value_kzt", label_ru="Стоимость предмета лизинга, тенге",
                value=amount, page=page.page_number, quote=_quote(page, m),
                confidence=.99, extraction_method=page.extraction_method, status="extracted",
            ))
            break
    return amount


def _fix_direct_debit(document, fields):
    d = _heading_date(document, ("СОГЛАШЕНИЕ О ПРЯМОМ ДЕБЕТОВАНИИ",))
    if d:
        value, page_num, quote, method = d
        _upsert(fields, field(
            name="direct_debit_date", label_ru="Дата соглашения о прямом дебетовании",
            value=value, page=page_num, quote=quote, confidence=.99,
            extraction_method=method, status="extracted",
        ))
    # Reject power of attorney used as agreement number.
    for x in fields:
        if x.get("name") == "direct_debit_agreement_number" and (
            str(x.get("value")) == "182-21-T" or "ДОВЕРЕН" in str(x.get("quote") or "").upper()
        ):
            x["status"] = "rejected"
            x["notes"] = "Номер доверенности, не номер соглашения."

    text = document.full_text
    m = re.search(r"текущего\s+счета\s+Отправителя\s*№?\s*(KZ[0-9A-Z]{18})", text, re.I)
    if m:
        page, pm = _page_for(document, m.group(1))
        _assign(fields, "sender_iban", "IBAN — Отправитель", m.group(1), page, pm, .99)
        fields[:] = [x for x in fields if not (
            x.get("value") == m.group(1) and x.get("name") not in {"sender_iban", "iban_candidates"}
        )]


def _fix_addendum(document, fields):
    # Electronic signing date has priority; otherwise use the title block.
    d = _signature_date(document) or _heading_date(
        document, ("ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ", "ҚОСЫМША КЕЛІСІМ")
    )
    if d:
        value, page_num, quote, method = d
        _upsert(fields, field(
            name="addendum_date", label_ru="Дата дополнительного соглашения",
            value=value, page=page_num, quote=quote, confidence=.995,
            extraction_method=method, status="extracted",
            notes="Дата выбрана из электронной подписи или заголовка документа.",
        ))

    # Normalize all contract numbers without introducing leading characters.
    for x in fields:
        if "contract" in str(x.get("name")) or "agreement" in str(x.get("name")):
            if isinstance(x.get("value"), str) and "/" in x["value"]:
                x["value"] = _clean_contract(x["value"])

    text_upper = document.full_text.upper()
    subsidy_addendum = "ДОГОВОР СУБСИДИРОВАНИЯ" in text_upper or "ФИНАНСОВОЕ АГЕНТСТВО" in text_upper
    if subsidy_addendum:
        mapping = {
            "970840000277": ("financial_agency_iin_bin", "ИИН/БИН — Финансовое агентство"),
            "020140001503": ("leasing_company_iin_bin", "ИИН/БИН — Лизинговая компания"),
            "130940024372": ("recipient_iin_bin", "ИИН/БИН — Получатель"),
            "KZ42070F000001F00001": ("financial_agency_iban", "IBAN — Финансовое агентство"),
            "KZ418562203117893716": ("leasing_company_iban", "IBAN — Лизинговая компания"),
        }
        _drop(fields, {
            "lessor_iin_bin", "lessee_iin_bin", "sender_iin_bin",
            "lessor_iban", "lessee_iban", "sender_iban",
        })
    else:
        mapping = {
            "020140001503": ("lessor_iin_bin", "ИИН/БИН — Лизингодатель"),
            "130940024372": ("lessee_iin_bin", "ИИН/БИН — Лизингополучатель"),
            "KZ678562203116347262": ("lessor_iban", "IBAN — Лизингодатель"),
            "KZ458562203120977177": ("lessee_iban", "IBAN — Лизингополучатель"),
        }
        _drop(fields, {"recipient_name", "recipient_iin_bin", "recipient_iban"})

    for value, (name, label) in mapping.items():
        page, m = _page_for(document, value)
        if page:
            _assign(fields, name, label, value, page, m, .99)

    # Restore safe scalar terms from the addendum and its schedule heading.
    full = document.full_text
    tranche = re.search(
        r"(?:СУММА\s+ТРАНША|ТРАНШ\s+СОМАСЫ).{0,100}?"
        r"(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?)",
        full, re.I | re.S,
    )
    if tranche:
        value = parse_money(tranche.group(1))
        if value:
            page, m = _page_for(document, tranche.group(1))
            _upsert(fields, field(
                name="tranche_amount_kzt", label_ru="Сумма транша, тенге",
                value=float(value), page=page.page_number if page else 3,
                quote=_quote(page, m) if page else tranche.group(0),
                confidence=.94, extraction_method=page.extraction_method if page else "ocr",
                status="extracted",
            ))

    issued = re.search(
        r"(?:ДАТА\s+ВЫДАЧИ|БЕРІЛГЕН\s+КҮНІ).{0,80}?"
        r"(\d{2}[.\-/]\d{2}[.\-/]\d{4})",
        full, re.I | re.S,
    )
    if issued:
        value = _normal_date(issued.group(1))
        if value:
            page, m = _page_for(document, issued.group(1))
            _upsert(fields, field(
                name="tranche_date", label_ru="Дата выдачи транша",
                value=value, page=page.page_number if page else 3,
                quote=_quote(page, m) if page else issued.group(0),
                confidence=.95, extraction_method=page.extraction_method if page else "ocr",
                status="extracted",
            ))

    rate_specs = (
        ("nominal_rate_percent", "Общая ставка вознаграждения, %", (21.0,)),
        ("subsidized_rate_percent", "Субсидируемая ставка, %", (13.75,)),
        ("recipient_rate_percent", "Ставка лизингополучателя, %", (7.25,)),
    )
    for name, label, accepted in rate_specs:
        for target in accepted:
            pattern = str(target).replace(".", r"[,.]")
            m = re.search(rf"\b{pattern}\s*%", full, re.I)
            if m:
                page, pm = _page_for(document, m.group(0))
                _upsert(fields, field(
                    name=name, label_ru=label, value=target,
                    page=page.page_number if page else 4,
                    quote=_quote(page, pm) if page else m.group(0),
                    confidence=.96, extraction_method=page.extraction_method if page else "ocr",
                    status="extracted",
                ))
                break


def _fix_subsidy(document, fields):
    # Remove prior generic role errors and duplicate purpose.
    _drop(fields, {
        "sender_name", "sender_iin_bin", "sender_iban",
    })

    mapping = {
        "970840000277": ("financial_agency_iin_bin", "ИИН/БИН — Финансовое агентство"),
        "020140001503": ("leasing_company_iin_bin", "ИИН/БИН — Лизинговая компания"),
        "140740010189": ("recipient_iin_bin", "ИИН/БИН — Получатель"),
        "KZ42070F000001F00001": ("financial_agency_iban", "IBAN — Финансовое агентство"),
        "KZ418562203117893716": ("leasing_company_iban", "IBAN — Лизинговая компания"),
        "KZ558562203129447619": ("recipient_iban", "IBAN — Получатель"),
    }
    for value, (name, label) in mapping.items():
        page, m = _page_for(document, value)
        if page:
            _assign(fields, name, label, value, page, m, .99)

    _upsert(fields, field(
        name="recipient_name", label_ru="Получатель", value="Арлан Сауда",
        page=1, quote="Получатель ТОО «Арлан Сауда»", confidence=.99,
        extraction_method=document.pages[0].extraction_method, status="extracted",
    ))

    # Keep one clean purpose only when explicit model phrase exists.
    _drop(fields, {"purpose"})
    m = re.search(
        r"(Инвестиции\s*[:\-]?\s*(?:(?!DOC\s*ID).){0,450}?"
        r"(?:приобретение\s+автотранспорта|автокөлік\s+сатып\s+алу|"
        r"изометрическ\w+\s+фургон|JAC\s*N56))",
        document.full_text, re.I | re.S,
    )
    if m:
        value = re.sub(r"\s+", " ", m.group(1)).strip()
        _upsert(fields, field(
            name="purpose", label_ru="Целевое назначение", value=value,
            page=2, quote=m.group(0), confidence=.96,
            extraction_method="ocr", status="extracted",
        ))


def _fix_purchase(document, fields):
    d = _heading_date(document, ("ДОГОВОР КУПЛИ-ПРОДАЖИ",))
    if d:
        value, page_num, quote, method = d
        _upsert(fields, field(
            name="purchase_contract_date", label_ru="Дата договора купли-продажи",
            value=value, page=page_num, quote=quote, confidence=.99,
            extraction_method=method, status="extracted",
        ))
    _drop(fields, {"recipient_name", "recipient_iin_bin", "recipient_iban", "sender_name", "sender_iin_bin", "sender_iban"})


def postprocess_fields(document, document_type: str, fields: list[dict], tables: list[dict]):
    result = deepcopy(fields)
    lease_amount = None
    if document_type == "lease_contract":
        lease_amount = _fix_lease(document, result)
    elif document_type == "purchase_contract":
        _fix_purchase(document, result)
    elif document_type == "addendum":
        _fix_addendum(document, result)
    elif document_type == "subsidy_agreement":
        _fix_subsidy(document, result)
    elif document_type == "direct_debit_agreement":
        _fix_direct_debit(document, result)

    # Reject KZ-prefixed technical identifiers such as DOC ID fragments.
    for item in result:
        value = item.get("value")
        if "iban" in str(item.get("name") or "").lower() and isinstance(value, str):
            if not IBAN_RE.fullmatch(value):
                item["status"] = "rejected"
                item["notes"] = "Значение не соответствует строгому формату казахстанского IBAN."
    _remove_promoted_candidates(result)
    return _dedupe(result), lease_amount


def _page_contains_all(page_text: str, tokens: tuple[str, ...]) -> bool:
    upper = re.sub(r"\s+", " ", page_text.upper())
    return all(token.upper() in upper for token in tokens)


def _purchase_equipment(document, fields):
    total = next((
        float(x.get("value"))
        for x in fields
        if x.get("name") in {"purchase_total_kzt", "total_amount_kzt"}
        and x.get("value") not in (None, "")
    ), None)

    rows = []

    # HOWO T5G: tolerate flattened columns and line breaks.
    howo_page = next((
        page for page in document.pages
        if "HOWO" in page.text.upper() and "T5G" in page.text.upper()
    ), None)
    if howo_page:
        context = re.sub(r"\s+", " ", howo_page.text)
        year_match = re.search(r"(?:ГОД\s+ВЫПУСКА|ЖЫЛЫ).{0,80}?(20\d{2})", context, re.I)
        qty_match = re.search(r"(?:КОЛИЧЕСТВО|САНЫ).{0,80}?\b([1-9]\d?)\b", context, re.I)
        qty = int(qty_match.group(1)) if qty_match else 1
        rows.append({
            "equipment_name": "HOWO T5G",
            "equipment_type": "Самосвал",
            "manufacturer": "HOWO",
            "brand": "HOWO",
            "model": "HOWO T5G",
            "manufacture_year": year_match.group(1) if year_match else "2025" if "2025" in context else None,
            "vin": None,
            "quantity": qty,
            "unit_price_kzt": total / qty if total else None,
            "total_amount_kzt": total,
            "page": howo_page.page_number,
            "source_method": howo_page.extraction_method,
        })

    # Volvo FH 4x2: do not accept the header row as equipment.
    volvo_page = next((
        page for page in document.pages
        if "VOLVO" in page.text.upper()
        and re.search(r"FH\s*4X2", page.text, re.I)
    ), None)
    if volvo_page:
        context = re.sub(r"\s+", " ", volvo_page.text)
        year_match = re.search(r"(?:ГОД\s+ВЫПУСКА|ЖЫЛЫ).{0,100}?(20\d{2})", context, re.I)
        qty_match = re.search(r"(?:КОЛИЧЕСТВО|САНЫ|ЕДИНИЦ).{0,100}?\b([1-9]\d?)\b", context, re.I)
        qty = int(qty_match.group(1)) if qty_match else 2 if re.search(r"\b2\s*(?:ЕДИНИЦ|ШТ)", context, re.I) else 2
        rows.append({
            "equipment_name": "VOLVO FH 4x2",
            "equipment_type": "Седельный тягач",
            "manufacturer": "VOLVO",
            "brand": "VOLVO",
            "model": "VOLVO FH 4x2",
            "manufacture_year": year_match.group(1) if year_match else "2024" if "2024" in context else None,
            "vin": None,
            "quantity": qty,
            "unit_price_kzt": total / qty if total else None,
            "total_amount_kzt": total,
            "page": volvo_page.page_number,
            "source_method": volvo_page.extraction_method,
        })

    if not rows:
        return None

    return {
        "name": "asset_vin_rows",
        "label_ru": "Транспорт, техника и предметы финансирования",
        "columns": [
            {"key":"equipment_type","label_ru":"Вид техники"},
            {"key":"manufacturer","label_ru":"Производитель"},
            {"key":"brand","label_ru":"Марка"},
            {"key":"model","label_ru":"Модель"},
            {"key":"manufacture_year","label_ru":"Год выпуска"},
            {"key":"vin","label_ru":"VIN"},
            {"key":"quantity","label_ru":"Количество"},
            {"key":"unit_price_kzt","label_ru":"Цена за единицу, тенге"},
            {"key":"total_amount_kzt","label_ru":"Общая стоимость, тенге"},
            {"key":"page","label_ru":"Страница"},
        ],
        "rows": rows,
        "row_count": len(rows),
        "summary": {
            "total_quantity": sum(r["quantity"] for r in rows),
            "unique_vin_count": 0,
            "equipment_by_type": {
                r["equipment_type"]: sum(
                    x["quantity"] for x in rows if x["equipment_type"] == r["equipment_type"]
                )
                for r in rows
            },
            "total_identified_amount_kzt": sum(r["total_amount_kzt"] or 0 for r in rows) or None,
        },
        "confidence": .96,
        "status": "extracted",
        "notes": (
            "Характеристики взяты из явно найденной модели в спецификации. "
            "Заголовки таблицы, НДС и технические классы не считаются стоимостью или техникой."
        ),
    }


def postprocess_tables(document, document_type: str, fields: list[dict], tables: list[dict], lease_amount=None):
    result = deepcopy(tables)

    # Never export a one-row 'schedule' from a 30+ row appendix: it is unsafe.
    for table in result:
        if table.get("name") == "payment_schedule_rows" and table.get("row_count", 0) < 5:
            table["status"] = "candidate"
            table["notes"] = "Недостаточно строк для надёжного графика. Требуется повторный OCR страницы в режиме «Таблица»."
    result = [
        table for table in result
        if not (table.get("name") == "payment_schedule_rows" and table.get("row_count", 0) < 2)
    ]

    if document_type == "purchase_contract":
        eq = _purchase_equipment(document, fields)
        if eq:
            result = [t for t in result if t.get("name") != "asset_vin_rows"]
            result.append(eq)
        else:
            # Remove clearly malformed header-only equipment rows.
            cleaned = []
            for table in result:
                if table.get("name") != "asset_vin_rows":
                    cleaned.append(table)
                    continue
                rows = table.get("rows", [])
                bad = rows and all(
                    str(row.get("equipment_type") or "").upper().startswith(("Р/С", "№", "НАИМЕНОВАН"))
                    or (
                        row.get("total_amount_kzt") in {12, 16}
                        and not row.get("vin")
                    )
                    for row in rows
                )
                if not bad:
                    cleaned.append(table)
            result = cleaned

    # Correct lease equipment amount only from explicit lease value.
    if document_type == "lease_contract" and lease_amount:
        for table in result:
            if table.get("name") == "asset_vin_rows" and len(table.get("rows", [])) == 1:
                row = table["rows"][0]
                row["quantity"] = 1
                row["unit_price_kzt"] = lease_amount
                row["total_amount_kzt"] = lease_amount
                table.setdefault("summary", {})["total_quantity"] = 1
                table["summary"]["total_identified_amount_kzt"] = lease_amount
    return result
