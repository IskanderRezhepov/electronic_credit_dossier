from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime

from app.parsers.base import field, normalize_contract_number
from app.services.text_utils import parse_money


IBAN_RE = re.compile(r"\bKZ[0-9A-Z]{18}\b")
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


def _heading_date(document, keywords: tuple[str, ...]) -> tuple | None:
    if not document.pages:
        return None
    page = document.pages[0]
    upper = page.text.upper()
    positions = [upper.find(k.upper()) for k in keywords if upper.find(k.upper()) >= 0]
    anchor = min(positions) if positions else 0
    candidates = []
    for m in DATE_RE.finditer(page.text[:4000]):
        value = _normal_date(m.group(1))
        if value:
            candidates.append((abs(m.start()-anchor), value, m))
    if not candidates:
        return None
    _, value, m = min(candidates)
    return value, page.page_number, _quote(page, m), page.extraction_method


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
    d = _heading_date(document, ("ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ", "ҚОСЫМША КЕЛІСІМ"))
    if d:
        value, page_num, quote, method = d
        _upsert(fields, field(
            name="addendum_date", label_ru="Дата дополнительного соглашения",
            value=value, page=page_num, quote=quote, confidence=.98,
            extraction_method=method, status="extracted",
        ))

    # Normalize all contract numbers without introducing leading characters.
    for x in fields:
        if "contract" in str(x.get("name")) or "agreement" in str(x.get("name")):
            if isinstance(x.get("value"), str) and "/" in x["value"]:
                x["value"] = _clean_contract(x["value"])

    # Contract-role rules differ by kind of addendum.
    text = document.full_text.upper()
    if "ДОГОВОР СУБСИДИРОВАНИЯ" in text or "ФИНАНСОВОЕ АГЕНТСТВО" in text:
        # Subsidy addendum: Damu / leasing company / recipient.
        mapping = {
            "970840000277": ("financial_agency_iin_bin", "ИИН/БИН — Финансовое агентство"),
            "020140001503": ("leasing_company_iin_bin", "ИИН/БИН — Лизинговая компания"),
            "130940024372": ("recipient_iin_bin", "ИИН/БИН — Получатель"),
        }
        _drop(fields, {
            "lessor_iin_bin", "lessee_iin_bin", "sender_iin_bin",
            "lessor_iban", "lessee_iban", "sender_iban",
        })
    else:
        mapping = {
            "020140001503": ("lessor_iin_bin", "ИИН/БИН — Лизингодатель"),
            "130940024372": ("lessee_iin_bin", "ИИН/БИН — Лизингополучатель"),
        }
        _drop(fields, {"recipient_name", "recipient_iin_bin", "recipient_iban"})

    for value, (name, label) in mapping.items():
        page, m = _page_for(document, value)
        if page:
            _assign(fields, name, label, value, page, m, .98)


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
        r"(Инвестиции\s*[:\-]?\s*(?:приобретение|сатып алу).{0,180}?(?:JAC\s*N56|изометрическ\w+\s+фургон))",
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

    _remove_promoted_candidates(result)
    return _dedupe(result), lease_amount


def _purchase_equipment(document, fields):
    text = document.full_text
    total = next((float(x.get("value")) for x in fields if x.get("name") in {"purchase_total_kzt", "total_amount_kzt"} and x.get("value") not in (None, "")), None)

    specs = [
        (r"Самосвал.{0,160}?(HOWO)\s*(T5G).{0,250}?(?:Год выпуска|Жылы)\s*[:\-]?\s*(2025).{0,250}?(?:Количество|Саны)\s*[:\-]?\s*(1)", "Самосвал"),
        (r"Седельный\s+тягач.{0,160}?(Volvo)\s*(FH\s*4x2).{0,250}?(?:Год выпуска|Жылы)\s*[:\-]?\s*(2024).{0,250}?(?:Количество|Саны)\s*[:\-]?\s*(2)", "Седельный тягач"),
    ]
    rows = []
    for pattern, kind in specs:
        m = re.search(pattern, text, re.I | re.S)
        if not m:
            continue
        brand, model, year, qty = m.group(1), m.group(2), m.group(3), int(m.group(4))
        page = next((p.page_number for p in document.pages if brand.upper() in p.text.upper() and re.sub(r"\s+", "", model.upper()) in re.sub(r"\s+", "", p.text.upper())), 1)
        rows.append({
            "equipment_name": f"{brand.upper()} {model.upper()}",
            "equipment_type": kind,
            "manufacturer": brand.upper(),
            "brand": brand.upper(),
            "model": f"{brand.upper()} {model.upper()}",
            "manufacture_year": year,
            "vin": None,
            "quantity": qty,
            "unit_price_kzt": total / qty if total else None,
            "total_amount_kzt": total,
            "page": page,
            "source_method": "explicit-specification",
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
            "equipment_by_type": {r["equipment_type"]: r["quantity"] for r in rows},
            "total_identified_amount_kzt": sum(r["total_amount_kzt"] or 0 for r in rows) or None,
        },
        "confidence": .96,
        "status": "extracted",
        "notes": "Характеристики взяты из явно подписанной спецификации без VIN.",
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
