from __future__ import annotations

import re
from pathlib import Path

from app.services.document_reader import ReadDocument
from app.services.text_utils import parse_money
from .base import (
    date_value, field, filename_number, find_all_regex, find_first, generic_identifiers,
    normalize_contract_number, valid_contract_number,
)
from .generic import parse as parse_generic


def _append(fields: list[dict], item: dict | None) -> None:
    if item:
        fields.append(item)



def _money_values_near(text: str, start: int, radius: int = 700) -> list:
    """Return parsed monetary values around a heading, preserving order."""
    window = text[start:start + radius]
    result = []
    for match in re.finditer(r"\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})", window):
        value = parse_money(match.group(0))
        if value is not None:
            result.append((value, match.group(0), start + match.start(), start + match.end()))
    return result


def _purchase_total_fallback(document: ReadDocument) -> dict | None:
    """
    Recover a purchase-contract total from OCR/Kazakh wording or from a clear
    30% + 70% payment split. No value is created unless supported by text.
    """
    heading_patterns = (
        r"ШАРТТЫ[ҢН]\s+ЖАЛПЫ\s+ҚҰНЫ",
        r"ШАРТТЫН\s+ЖАЛПЫ\s+КУНЫ",
        r"ОБЩАЯ\s+СТОИМОСТЬ",
        r"ОБЩАЯ\s+ЦЕНА",
        r"ВСЕГО\s+ПО\s+НАСТОЯЩЕМУ\s+ДОГОВОРУ",
    )
    candidates = []
    for page in document.pages:
        upper = page.text.upper()
        for pattern in heading_patterns:
            for heading in re.finditer(pattern, upper, re.I):
                for value, raw, s, e in _money_values_near(page.text, heading.start(), 500):
                    if value >= 10000:
                        candidates.append((value, page, s, e, "explicit_total"))

        # Strong fallback: two payment portions that explicitly sum to 100%.
        percent_amounts = []
        for match in re.finditer(
            r"(\d{1,3})\s*%[^0-9]{0,180}?"
            r"(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2}))",
            page.text,
            re.I | re.S,
        ):
            pct = int(match.group(1))
            amount = parse_money(match.group(2))
            if amount is not None and 0 < pct <= 100:
                percent_amounts.append((pct, amount, match))
        for i, left in enumerate(percent_amounts):
            for right in percent_amounts[i + 1:]:
                if left[0] + right[0] == 100:
                    total = left[1] + right[1]
                    candidates.append(
                        (
                            total,
                            page,
                            left[2].start(),
                            right[2].end(),
                            "payment_split",
                        )
                    )

    if not candidates:
        return None

    # Prefer explicit totals; within the same evidence type prefer the amount
    # occurring most often, then the largest plausible value.
    ranked = sorted(
        candidates,
        key=lambda item: (
            item[4] == "explicit_total",
            sum(1 for candidate in candidates if candidate[0] == item[0]),
            item[0],
        ),
        reverse=True,
    )
    value, page, start, end, evidence = ranked[0]
    return field(
        name="total_amount_kzt",
        label_ru="Общая стоимость договора, тенге",
        value=value,
        page=page.page_number,
        quote=page.text[max(0, start - 180):min(len(page.text), end + 180)],
        confidence=0.84 if page.extraction_method == "digital" else 0.76,
        extraction_method=page.extraction_method,
        value_type="calculated" if evidence == "payment_split" else "direct",
        status="extracted",
        notes=(
            "Рассчитано как сумма явно указанных долей платежа, составляющих 100%."
            if evidence == "payment_split"
            else "Извлечено из контекста общей стоимости договора."
        ),
    )


def _schedule_principal_fallback(document: ReadDocument) -> dict | None:
    """
    Recover the original principal only from a clearly structured first row:
    date + repeated principal component + final remaining balance.

    The previous heuristic could choose an unrelated large amount. This version
    deliberately returns None when the row is ambiguous.
    """
    date_pattern = re.compile(r"\b\d{2}[.\-/]\d{2}[.\-/]\d{2,4}\b")
    money_pattern = re.compile(
        r"\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})"
    )

    for page in document.pages[:2]:
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        for line_index, line in enumerate(lines):
            date_match = date_pattern.search(line)
            if not date_match:
                continue

            # OCR may wrap one table row onto two or three lines.
            row = " ".join(lines[line_index:line_index + 3])
            values = []
            for match in money_pattern.finditer(row[date_match.end():]):
                parsed = parse_money(match.group(0))
                if parsed is not None and parsed > 0:
                    values.append(parsed)

            if len(values) < 4:
                continue

            # The balance is normally the final large amount in the row.
            balance = values[-1]
            if balance < 1_000_000:
                continue

            preceding = values[:-1]
            # In repayment schedules the second monetary column after the date
            # is normally principal repayment. OCR may duplicate the interest
            # column, so frequency alone is not reliable.
            principal_payment = values[1] if len(values) >= 3 else None
            if principal_payment is None or not (0 < principal_payment < balance and balance / principal_payment >= 5):
                frequencies = {}
                for value in preceding:
                    frequencies[value] = frequencies.get(value, 0) + 1
                repeated = [
                    value for value, count in frequencies.items()
                    if count >= 2 and value < balance and balance / value >= 5
                ]
                if not repeated:
                    continue
                principal_payment = max(repeated, key=lambda value: frequencies[value])
            total = principal_payment + balance

            # A plausible first-period principal should be a small fraction of
            # the opening balance; reject aggressive or ambiguous calculations.
            ratio = principal_payment / total
            if not (0.005 <= ratio <= 0.15):
                continue

            return field(
                name="loan_amount_kzt",
                label_ru="Сумма займа / транша, тенге",
                value=total,
                page=page.page_number,
                quote=row[:900],
                confidence=0.74 if page.extraction_method != "digital" else 0.84,
                extraction_method=page.extraction_method,
                value_type="calculated",
                status="candidate",
                notes=(
                    "Рассчитано по первой строке графика: повторяющаяся часть "
                    "погашения основного долга плюс конечный остаток. Требуется сверка."
                ),
            )
    return None


def _filename_act_references(document: ReadDocument) -> tuple[dict | None, dict | None]:
    """
    Extract act number and linked purchase-contract number from an informative
    filename. The parser stops before a trailing date and does not treat O/О as 0.
    """
    stem = Path(document.filename).stem
    normalized = (
        stem.replace("–", "-")
        .replace("—", "-")
        .replace("\\", "/")
    )
    act = None
    linked = None

    act_patterns = (
        r"(?:АКТ(?:Ы)?|АКТІСІ)[^№#\n]{0,120}?(?:№|N(?:O)?|#)\s*(\d{1,4})(?=\D|$)",
        r"(?:№|N(?:O)?|#)\s*(\d{1,4})\s*(?:К|К\s+ДОГОВОРУ|АКТ)",
    )
    for pattern in act_patterns:
        match = re.search(pattern, normalized, re.I)
        if match:
            act = field(
                name="act_number",
                label_ru="Номер акта",
                value=match.group(1),
                page=None,
                quote=f"Имя файла: {document.filename}",
                confidence=0.84,
                extraction_method="filename",
                status="candidate",
                notes="Извлечено из имени файла; требуется сверка с текстом.",
            )
            break

    # Prefer a number immediately following "договор ... №".
    linked_patterns = (
        r"(?:ДОГОВОР[А-ЯЁІҢҒҮҰҚӨҺ\s-]{0,80}?)(?:№|N(?:O)?|#)\s*"
        r"([A-ZА-Я0-9]+(?:[-_/][A-ZА-Я0-9]+){2,6})"
        r"(?=\s+(?:ОТ|К|ДЛЯ)|$)",
        r"(?:ЛИЗИНГ[А-ЯЁІҢҒҮҰҚӨҺ\s-]{0,40}?)(?:№|N(?:O)?|#)\s*"
        r"([A-ZА-Я0-9]+(?:[-_/][A-ZА-Я0-9]+){2,6})"
        r"(?=\s+(?:ОТ|К|ДЛЯ)|$)",
    )
    for pattern in linked_patterns:
        match = re.search(pattern, normalized, re.I)
        if not match:
            continue
        value = normalize_contract_number(match.group(1))
        # OCR/filename confusion: leading Cyrillic/Latin O before digits is
        # usually the "№" artefact, not part of the contract number.
        value = re.sub(r"^[OО](?=\d)", "", value)
        if valid_contract_number(value):
            linked = field(
                name="linked_purchase_contract",
                label_ru="Связанный договор купли-продажи",
                value=value,
                page=None,
                quote=f"Имя файла: {document.filename}",
                confidence=0.84,
                extraction_method="filename",
                status="candidate",
                notes="Полный номер извлечён из имени файла; требуется сверка.",
            )
            break

    return act, linked


def _normalize_addendum_contract_candidate(raw: str) -> str | None:
    compact = re.sub(r"\s+", "", raw.upper())
    compact = compact.replace("\\", "/").replace("|", "/")
    compact = compact.replace("А", "A").replace("С", "C").replace("Л", "L")
    compact = re.sub(r"[/_-]+", "/", compact)

    # Common OCR form: AGSH2022/L/L/113039, where 'SH' represents a damaged 4.
    match = re.search(
        r"A[GCS](?:4|SH|SН|H)?/?(20\d{2})/?([UL])/?([UL])/?(\d{5,8})",
        compact,
    )
    if match:
        year, first, second, tail = match.groups()
        # Financial-lease identifiers in these files use U/L. OCR often reads U as L.
        first = "U" if first in {"U", "L"} else first
        second = "L"
        return f"AG4/{year}/{first}/{second}/{tail}"

    normal = normalize_contract_number(raw)
    return normal if valid_contract_number(normal) else None


def _contract_candidates(document: ReadDocument, pages: int = 2) -> list[tuple[str, int, str, float, str]]:
    """Collect and rank contract-number candidates near legal-document headings."""
    candidates: list[tuple[str, int, str, float, str]] = []

    patterns = [
        r"(?:ДОГОВОР(?:У|А)?|ШАРТЫНА|ШАРТЫ)\s+(?:ФИНАНСОВОГО\s+ЛИЗИНГА|ҚАРЖЫЛЫҚ\s+ЛИЗИНГ)[^№N]{0,140}(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,50})",
        r"(?:№|N)\s*([A-ZА-Я]{1,6}\d*(?:[/_-][A-ZА-Я0-9]+){2,7})",
        r"\b([A-Z]{2,6}\d{6,}[A-Z0-9]{2,})\b",
    ]

    for page in document.pages[:pages]:
        for pattern in patterns:
            for match in re.finditer(pattern, page.text, re.I | re.S):
                value = normalize_contract_number(match.group(1))
                if not valid_contract_number(value):
                    continue

                separator_count = value.count("/") + value.count("-")
                score = 0.65 + min(0.20, separator_count * 0.04)
                if "ЛИЗИНГ" in page.text[max(0, match.start() - 150):match.end() + 80].upper():
                    score += 0.08

                candidates.append(
                    (
                        value,
                        page.page_number,
                        page.text[max(0, match.start() - 120):match.end() + 120],
                        min(score, 0.96),
                        page.extraction_method,
                    )
                )

    # Remove duplicates, keeping the strongest candidate.
    best: dict[str, tuple[str, int, str, float, str]] = {}
    for item in candidates:
        current = best.get(item[0])
        if current is None or item[3] > current[3]:
            best[item[0]] = item

    return sorted(
        best.values(),
        key=lambda item: (
            item[3],
            item[0].count("/") + item[0].count("-"),
            len(item[0]),
        ),
        reverse=True,
    )


def _best_lease_number(document: ReadDocument) -> dict | None:
    candidates = _contract_candidates(document, pages=3)
    if not candidates:
        return None

    value, page_number, quote, confidence, method = candidates[0]
    return field(
        name="lease_contract_number",
        label_ru="Номер договора лизинга",
        value=value,
        page=page_number,
        quote=quote,
        confidence=confidence,
        extraction_method=method,
    )



def _schedule_number_from_filename(document: ReadDocument) -> dict | None:
    """
    Extract a contract number from names such as:
    F-210018188_AG2-2022-U-L-113039 от 28 октября 2022 года.pdf
    The technical F-number and trailing date must not become part of the value.
    """
    stem = Path(document.filename).stem.upper()

    patterns = [
        r"(AG\d{1,3})[-_/](\d{4})[-_/]([A-ZА-Я])[-_/]([A-ZА-Я])[-_/](\d{4,8})",
        r"([A-ZА-Я]{1,5}\d{0,3})[-_/](\d{4})[-_/]([A-ZА-Я])[-_/]([A-ZА-Я])[-_/](\d{4,8})",
    ]

    for pattern in patterns:
        match = re.search(pattern, stem, re.I)
        if not match:
            continue

        value = "/".join(match.groups())
        value = normalize_contract_number(value)
        if not valid_contract_number(value):
            continue

        return field(
            name="lease_contract_number",
            label_ru="Номер договора лизинга",
            value=value,
            page=None,
            quote=f"Имя файла: {document.filename}",
            confidence=0.68,
            extraction_method="filename",
            status="candidate",
            notes="Извлечено из имени файла; требуется сверка с текстом документа.",
        )

    return None


def _identifier_groups(values: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for value in values:
        groups.setdefault(value[:3], []).append(value)
    return {
        key: sorted(set(group))
        for key, group in groups.items()
    }


def _select_probable_vins(values: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """
    OCR frequently returns both VIN and chassis numbers as 17-character codes.
    Group by WMI/prefix and use the largest coherent group for vehicle count.
    """
    groups = _identifier_groups(values)
    if not groups:
        return [], {}

    ranked = sorted(
        groups.items(),
        key=lambda item: (len(item[1]), item[0]),
        reverse=True,
    )
    selected = ranked[0][1]
    return selected, groups


def _party(document: ReadDocument, role: str, name: str, label_ru: str) -> dict | None:
    """Extract party names from the contract preamble and requisites, avoiding incidental role mentions."""
    role_upper = role.upper()
    patterns_by_role = {
        'ПРОДАВЕЦ': [
            r'((?:ТОО|АО|ИП|Товарищество с ограниченной ответственностью|Акционерное общество|Индивидуальный предприниматель)\s*[«"]?[^\n,;]{3,120}?[»"]?)\s*,?\s*(?:в дальнейшем\s+)?именуем(?:ое|ый|ая)\s*[«"]ПРОДАВЕЦ[»"]',
            r'ПРОДАВЕЦ\s*[:\-]\s*((?:ТОО|АО|ИП)?\s*[«"]?[^\n,;]{3,120})',
        ],
        'ПОКУПАТЕЛЬ': [
            r'((?:Дочерняя компания[^\n]{0,80})?(?:ТОО|АО|ИП|Товарищество с ограниченной ответственностью|Акционерное общество)\s*[«"]?[^\n,;]{3,160}?[»"]?)\s*,?[^\n]{0,120}?далее именуем(?:ое|ый|ая)\s*[«"]Покупатель[»"]',
            r'ПОКУПАТЕЛЬ\s*[:\-]\s*((?:ТОО|АО|ИП)?\s*[«"]?[^\n,;]{3,120})',
        ],
        'ЛИЗИНГОПОЛУЧАТЕЛЬ': [
            r'((?:ТОО|АО|ИП|Индивидуальный предприниматель|Товарищество с ограниченной ответственностью)\s*[«"]?[^\n,;]{3,140}?[»"]?)\s*,?\s*(?:именуем(?:ое|ый|ая)\s+далее\s*[–-]?\s*)?[«"]ЛИЗИНГОПОЛУЧАТЕЛЬ[»"]',
            r'ЛИЗИНГОПОЛУЧАТЕЛЬ\s*[:\-]\s*((?:ТОО|АО|ИП)?\s*[«"]?[^\n,;]{3,120})',
        ],
    }
    patterns = patterns_by_role.get(role_upper, [])
    return find_first(document, patterns=patterns, name=name, label_ru=label_ru, confidence=0.9, pages=4)


def parse_purchase_contract(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    number = find_first(document, patterns=[
        r'ДОГОВОР\s+КУПЛИ[-\s]ПРОДАЖИ(?:\s+ТОВАРА)?.{0,180}?(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,50})',
        r'(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,50}).{0,180}?ДОГОВОР\s+КУПЛИ[-\s]ПРОДАЖИ',
        r'ДОГОВОР\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,50})\s+КУПЛИ[-\s]ПРОДАЖИ',
        r'ДОГОВОР\s+КУПЛИ[-\s]ПРОДАЖИ.{0,220}?\b([A-Z]{2,6}\d{6,}[A-Z0-9]{2,})\b',
    ], name='purchase_contract_number', label_ru='Номер договора купли-продажи',
       converter=normalize_contract_number, validator=valid_contract_number, pages=3)
    if not number:
        number = find_first(document, patterns=[
            r'ДОГОВОР\s+КУПЛИ[-\s]ПРОДАЖИ\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{5,49})',
        ], name='purchase_contract_number', label_ru='Номер договора купли-продажи',
           converter=normalize_contract_number, validator=valid_contract_number, pages=3)
    if not number:
        number = filename_number(document, r'(?:ДКП|купли-продажи).*?([A-ZА-Я0-9]+(?:[/_-][A-ZА-Я0-9]+){1,5}|[A-ZА-Я]{2,}[A-ZА-Я0-9]{6,})',
                                 'purchase_contract_number', 'Номер договора купли-продажи')
    _append(fields, number)

    # Дата договора извлекается из трёх групп: день, месяц словом, год.
    if not any(f['name'] == 'purchase_contract_date' for f in fields):
        for page in document.pages[:2]:
            m = re.search(r'(?:г\.?\s*Алматы|Алматы\s*қ\.?).{0,80}?[«"]?(\d{1,2})[»"]?\s*(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s*(\d{4})', page.text, re.I | re.S)
            if m:
                fields.append(field(name='purchase_contract_date', label_ru='Дата договора купли-продажи',
                                    value=date_value(*m.groups()), page=page.page_number,
                                    quote=page.text[max(0, m.start()-100):m.end()+100],
                                    confidence=page.quality if page.extraction_method != 'digital' else 0.98,
                                    extraction_method=page.extraction_method))
                break

    purchase_total = find_first(document, patterns=[
        r'Общая (?:стоимость|цена)(?: настоящего)? (?:Договора|Оборудования)\s*(?:составляет|:)\s*(\d[\d\s]*(?:[,.]\d{1,2})?)\s*(?:тенге|KZT)',
        r'Цена Оборудования[^\n]{0,100}?составляет\s*(\d[\d\s]*(?:[,.]\d{1,2})?)\s*тенге',
        r'БАРЛЫҒЫ\s*/\s*ИТОГО\s*[:\-]?\s*(\d[\d\s]*(?:[,.]\d{1,2})?)',
        r'(?:ОБЩАЯ|ПОЛНАЯ)\s+(?:СТОИМОСТЬ|ЦЕНА)[^\d]{0,120}(\d[\d\s]*(?:[,.]\d{1,2})?)\s*(?:ТЕНГЕ|KZT)',
        r'НА\s+ОБЩУЮ\s+СУММУ[^\d]{0,100}(\d[\d\s]*(?:[,.]\d{1,2})?)\s*(?:ТЕНГЕ|KZT)',
    ], name='total_amount_kzt', label_ru='Общая стоимость договора, тенге', converter=parse_money)
    if not purchase_total:
        purchase_total = _purchase_total_fallback(document)
    _append(fields, purchase_total)
    _append(fields, find_first(document, patterns=[r'в том числе НДС\s*(\d+)%'], name='vat_percent', label_ru='НДС, %', converter=int))
    _append(fields, find_first(document, patterns=[r'поставк[аи].{0,80}?в течение\s*(\d+)\s*\([^)]*\)\s*(?:рабочих|календарных)'], name='delivery_term_days', label_ru='Срок поставки, дней', converter=int))
    _append(fields, find_first(document, patterns=[r'в течение\s*(\d+)\s*\([^)]*\)\s*месяцев'], name='warranty_months', label_ru='Гарантия, месяцев', converter=int))
    _append(fields, find_first(document, patterns=[r'или\s*([\d\s]+)\s*\([^)]*\)\s*км'], name='warranty_km', label_ru='Гарантия, км', converter=lambda v: int(re.sub(r'\s+', '', v))))
    _append(fields, find_first(document, patterns=[r'пеню в размере\s*([\d,.]+)%'], name='penalty_percent_daily', label_ru='Пеня, % в день', converter=lambda v: float(v.replace(',', '.'))))
    _append(fields, _party(document, 'ПРОДАВЕЦ', 'seller_name', 'Продавец'))
    _append(fields, _party(document, 'ПОКУПАТЕЛЬ', 'buyer_name', 'Покупатель'))
    _append(fields, _party(document, 'ЛИЗИНГОПОЛУЧАТЕЛЬ', 'lessee_name', 'Лизингополучатель'))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)


def parse_lease_contract(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    _append(fields, find_first(document, patterns=[
        r'(?:Заявление о присоединении\s*\(Договор лизинга\)|Договор финансового лизинга)\s*(?:№|N)\s*([A-ZА-Я0-9./_-]{5,40})',
    ], name='lease_contract_number', label_ru='Номер договора лизинга', converter=normalize_contract_number, validator=valid_contract_number, pages=2))
    _append(fields, find_first(document, patterns=[r'Стоимость Предмета лизинга составляет\s*([\d\s]+[,.]\d{1,2})'], name='lease_asset_value_kzt', label_ru='Стоимость предмета лизинга, тенге', converter=parse_money))
    _append(fields, find_first(document, patterns=[r'размере\s*([\d,.]+)%[^\n]{0,40}годовых'], name='interest_rate_percent', label_ru='Ставка вознаграждения, %', converter=lambda v: float(v.replace(',', '.'))))
    _append(fields, find_first(document, patterns=[r'составляет\s*(\d+)\s*\([^)]*\)\s*месяц'], name='lease_term_months', label_ru='Срок лизинга, месяцев', converter=int))
    _append(fields, find_first(document, patterns=[r'авансов(?:ый|ого) платеж.{0,80}?([\d\s]+[,.]\d{1,2})'], name='advance_payment_kzt', label_ru='Авансовый платёж, тенге', converter=parse_money))
    _append(fields, find_first(document, patterns=[r'Продавец\s*[-–:]\s*(?:ТОО|АО|ИП)?\s*[«"]?([^\n.;]{3,80})'], name='seller_name', label_ru='Продавец'))
    _append(fields, find_first(document, patterns=[r'(?:Индивидуальный предприниматель|ТОО|АО)\s*[«"]([^»"]+)[»"].{0,80}?ИИН\s*(\d{12})'], name='lessee_name', label_ru='Лизингополучатель'))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)


def parse_acceptance_act(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    filename_act, filename_linked = _filename_act_references(document)

    # A single PDF may contain a package of several acts. Capture all explicit
    # act numbers instead of forcing one document-level number.
    act_number_candidates = []
    act_patterns = [
        r'АКТ\s+ПРИ[ЕЁ]МА[-\s]ПЕРЕДАЧИ(?:\s+ТОВАРА)?.{0,80}?(?:№|N(?:O)?|#)\s*([A-ZА-Я0-9./_-]{1,30})',
        r'ҚАБЫЛДАУ-ӨТКІЗУ\s+АКТІСІ.{0,80}?(?:№|N)\s*([A-ZА-Я0-9./_-]{1,30})',
        r'(?:№\s*)?(\d{1,4})\s+АКТ\s+ПРИЕМА[-\s]ПЕРЕДАЧИ',
    ]
    for pattern in act_patterns:
        for raw in find_all_regex(document, pattern):
            value = normalize_contract_number(raw)
            if re.search(r'\d', value) and len(value) <= 30:
                act_number_candidates.append(value)

    act_number_candidates = sorted(set(act_number_candidates))

    filename_upper = Path(document.filename).stem.upper()
    repeated_act_headers = sum(
        page.text.upper().count('АКТ ПРИЕМА-ПЕРЕДАЧИ')
        + page.text.upper().count('ҚАБЫЛДАУ-ӨТКІЗУ АКТІСІ')
        for page in document.pages
    )
    is_act_package = (
        'АКТЫ ПРИЕМА' in filename_upper
        or 'АКТЫ ПРИЁМА' in filename_upper
        or (repeated_act_headers >= 3 and document.page_count >= 4)
        or len(act_number_candidates) > 1
    )

    if is_act_package:
        fields.append(
            field(
                name='act_package_detected',
                label_ru='Пакет актов',
                value='Да',
                page=None,
                quote=f'Имя файла: {document.filename}',
                confidence=0.88,
                extraction_method='calculated',
                status='extracted',
                notes='Документ содержит несколько актов; единый номер пакета может отсутствовать.',
            )
        )

    if len(act_number_candidates) > 1:
        fields.append(
            field(
                name='act_numbers',
                label_ru='Номера актов в пакете',
                value=act_number_candidates,
                page=None,
                quote=None,
                confidence=0.72 if document.used_ocr else 0.94,
                extraction_method='mixed' if document.used_ocr else 'digital',
                status='candidate',
                notes='PDF содержит несколько актов; значения необходимо сверить постранично.',
            )
        )

    act_number = find_first(document, patterns=[
        r'(?:№\s*)?(\d{1,4})\s+АКТ\s+ПРИЕМА[-\s]ПЕРЕДАЧИ',
        r'АКТ\s+ПРИ[ЕЁ]МА[-\s]ПЕРЕДАЧИ(?:\s+ТОВАРА)?.{0,80}?(?:№|N(?:O)?|#)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{0,30})',
        r'ҚАБЫЛДАУ-ӨТКІЗУ\s+АКТІСІ.{0,80}?(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{0,30})',
    ], name='act_number', label_ru='Номер акта', converter=normalize_contract_number,
       validator=lambda v: bool(re.search(r'\d', v) and len(v) <= 30), pages=2)
    if not act_number:
        act_number = filename_act
    if not is_act_package and len(act_number_candidates) <= 1:
        _append(fields, act_number)
    linked_contract = find_first(document, patterns=[
        r'Договор[ау]? купли[-\s]продажи.{0,120}?(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{3,40})',
    ], name='linked_purchase_contract', label_ru='Связанный договор купли-продажи',
       converter=normalize_contract_number, validator=valid_contract_number, pages=3)
    if filename_linked and (
        not linked_contract
        or len(str(filename_linked["value"]).split("/"))
           + len(str(filename_linked["value"]).split("-"))
           > len(str(linked_contract["value"]).split("/"))
             + len(str(linked_contract["value"]).split("-"))
    ):
        linked_contract = filename_linked
    _append(fields, linked_contract)
    # Prefer the last monetary amount on the total row; do not concatenate quantity with amount.
    act_total = find_first(document, patterns=[
        r'(?:БАРЛЫҒЫ\s*/\s*)?ИТОГО\s*[:\-]?\s*\d{1,4}\s+([\d ]{5,}[,.]\d{2})',
        r'(?:БАРЛЫҒЫ\s*/\s*)?ИТОГО\s*[:\-]?\s*([\d ]{5,}[,.]\d{2})',
        r'Общая стоимость[^\d]{0,80}([\d ]{5,}[,.]\d{2})',
    ], name='act_total_amount_kzt', label_ru='Общая стоимость по акту, тенге', converter=parse_money)
    _append(fields, act_total)
    all_codes = find_all_regex(document, r'\b[A-HJ-NPR-Z0-9]{17}\b')
    if all_codes:
        probable_vins, identifier_groups = _select_probable_vins(all_codes)
        preview = probable_vins[:20]
        group_sizes = {prefix: len(values) for prefix, values in identifier_groups.items()}

        notes = (
            f"Найдено {len(all_codes)} 17-значных идентификаторов. "
            f"Группы по префиксам: {group_sizes}. "
            f"Для расчёта количества выбрана крупнейшая однородная группа: {len(probable_vins)}."
        )
        if len(probable_vins) > 20:
            notes += " В интерфейсе показаны первые 20; полный список доступен в JSON/Excel."

        fields.append(
            field(
                name='asset_vins',
                label_ru='Вероятные VIN по акту',
                value=preview,
                page=None,
                quote=None,
                confidence=0.66 if document.used_ocr else 0.90,
                extraction_method='mixed' if document.used_ocr else 'digital',
                status='candidate',
                notes=notes,
            )
        )
        fields.append(
            field(
                name='asset_identifier_groups',
                label_ru='Группы 17-значных идентификаторов',
                value=group_sizes,
                page=None,
                quote=None,
                confidence=0.70,
                extraction_method='calculated',
                status='candidate',
                notes='Помогает отличить VIN от номеров шасси и других кодов.',
            )
        )
        fields.append(
            field(
                name='asset_count_calculated',
                label_ru='Расчётное количество единиц',
                value=len(probable_vins),
                page=None,
                quote='Размер крупнейшей однородной группы 17-значных идентификаторов',
                confidence=0.82,
                extraction_method='calculated',
                value_type='calculated',
                status='candidate',
                notes='Требуется сверить с колонкой количества или строкой Итого.',
            )
        )
    _append(fields, find_first(
        document,
        patterns=[
            r'Наименование Товара.{0,260}?(?:\n|\|)\s*(?:1\s+)?([A-ZА-ЯӘІҢҒҮҰҚӨҺ][^\n|]{3,90}?)(?=\s+[A-HJ-NPR-Z0-9]{17}|\s+\d{1,3}\s+[\d ]{5,})',
            r'(?:Марка|Модель|Наименование)\s*[:\-]\s*([^\n|]{3,100})',
        ],
        name='asset_name',
        label_ru='Наименование имущества',
        validator=lambda value: not bool(re.match(r'^\d', value.strip())),
    ))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)


def parse_payment_schedule(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    lease_number = _best_lease_number(document)
    if not lease_number:
        lease_number = _schedule_number_from_filename(document)
    _append(fields, lease_number)
    loan_amount = find_first(
        document,
        patterns=[
            r'(?:Сумма\s+(?:займа|транша)|Қарыз(?:дың)?\s+сомасы)'
            r'\s*[:/\-]?\s*(\d[\d\s]*(?:[,.]\d{1,2})?)',
        ],
        name='loan_amount_kzt',
        label_ru='Сумма займа / транша, тенге',
        converter=parse_money,
        pages=2,
    )
    if not loan_amount:
        loan_amount = _schedule_principal_fallback(document)
    _append(fields, loan_amount)
    _append(fields, find_first(document, patterns=[r'Дата\s+выдачи\s*[:/\-]?\s*(\d{2}[.-]\d{2}[.-]\d{2,4})'], name='issue_date', label_ru='Дата выдачи', pages=2))
    _append(fields, find_first(document, patterns=[r'Дата погашения займа\s*[:/]\s*(\d{2}[.-]\d{2}[.-]\d{2,4})'], name='maturity_date', label_ru='Дата погашения', pages=2))
    _append(fields, find_first(document, patterns=[r'Ставка вознаграждения.*?(\d{1,2}[,.]\d+)%'], name='interest_rate_percent', label_ru='Ставка вознаграждения, %', converter=lambda v: float(v.replace(',', '.'))))
    _append(fields, find_first(document, patterns=[r'ИТОГО\s*[:\s]+[\d\s]+[,.]\d{2}\s+([\d\s]+[,.]\d{2})\s+([\d\s]+[,.]\d{2})'], name='total_principal_kzt', label_ru='Итого основной долг, тенге', converter=parse_money))
    _append(fields, find_first(document, patterns=[r'Итого основного долга\s*([\d\s]+[,.]\d{2})'], name='total_principal_kzt', label_ru='Итого основной долг, тенге', converter=parse_money))
    _append(fields, find_first(document, patterns=[r'Итого процентов\s*([\d\s]+[,.]\d{2})'], name='total_interest_kzt', label_ru='Итого вознаграждение, тенге', converter=parse_money))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)


def parse_addendum(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    _append(fields, find_first(document, patterns=[
        r'ДОПОЛНИТЕЛЬНОЕ\s+СОГЛАШЕНИЕ\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{0,30})',
    ], name='addendum_number', label_ru='Номер дополнительного соглашения', converter=normalize_contract_number,
       validator=lambda v: bool(re.fullmatch(r'[A-ZА-Я0-9./_-]{1,30}', v) and re.search(r'\d', v)), pages=2))
    main_contract = find_first(document, patterns=[
        r'к\s+ДОГОВОРУ\s+ФИНАНСОВОГО\s+ЛИЗИНГА\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,40})',
        r'ДОПОЛНИТЕЛЬНОЕ\s+СОГЛАШЕНИЕ.{0,220}?ДОГОВОРУ\s+ФИНАНСОВОГО\s+ЛИЗИНГА\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,40})',
        r'ҚАРЖЫЛЫҚ\s+ЛИЗИНГ\s+ШАРТЫНА.{0,120}?(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,40})',
        r'(?:№|N)\s*([A-ZА-Я]{1,6}\d*(?:[/_-][A-ZА-Я0-9]+){3,7})\s+ҚАРЖЫЛЫҚ\s+ЛИЗИНГ',
        r'(?:№|N)\s*([A-ZА-Я]{1,6}\d*\s*[/_-]\s*\d{4}\s*[/_-]\s*[A-ZА-Я]\s*[/_-]\s*[A-ZА-Я]\s*[/_-]\s*\d{4,8})',
    ], name='lease_contract_number', label_ru='Номер основного договора',
       converter=normalize_contract_number, validator=valid_contract_number, pages=2)

    # OCR may heavily damage separators and Latin letters. Search the complete
    # first-page text and canonicalise only a supported AG4/year/U/L/number form.
    if not main_contract:
        for page in document.pages[:2]:
            for match in re.finditer(
                r'A[GСC][A-ZА-Я0-9]{0,3}\s*[/_-]?\s*20\d{2}'
                r'.{0,25}?[ULЛ].{0,15}?[ULЛ].{0,25}?\d{5,8}',
                page.text,
                re.I | re.S,
            ):
                value = _normalize_addendum_contract_candidate(match.group(0))
                if value:
                    main_contract = field(
                        name='lease_contract_number',
                        label_ru='Номер основного договора',
                        value=value,
                        page=page.page_number,
                        quote=page.text[max(0, match.start()-120):match.end()+120],
                        confidence=0.72,
                        extraction_method=page.extraction_method,
                        status='candidate',
                        notes='Номер восстановлен из OCR-повреждённого написания; требуется сверка.',
                    )
                    break
            if main_contract:
                break

    if not main_contract:
        ranked = _contract_candidates(document, pages=2)
        if ranked:
            value, page_number, quote, confidence, method = ranked[0]
            main_contract = field(
                name='lease_contract_number',
                label_ru='Номер основного договора',
                value=value,
                page=page_number,
                quote=quote,
                confidence=max(0.70, confidence),
                extraction_method=method,
                status='candidate' if document.used_ocr else 'extracted',
                notes='Номер выбран из наиболее вероятных упоминаний договора.',
            )

    _append(fields, main_contract)
    for page in document.pages[:2]:
        m = re.search(r'(?:г\.?\s*Алматы|Алматы\s*қ\.?).{0,100}?[«"]?(\d{1,2})[»"]?\s*(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s*(\d{4})', page.text, re.I | re.S)
        if m:
            fields.append(field(name='addendum_date', label_ru='Дата дополнительного соглашения',
                                value=date_value(*m.groups()), page=page.page_number,
                                quote=page.text[max(0, m.start()-100):m.end()+100],
                                confidence=page.quality if page.extraction_method != 'digital' else 0.98,
                                extraction_method=page.extraction_method))
            break
    if not any(item.get('name') == 'addendum_date' for item in fields):
        _append(fields, find_first(
            document,
            patterns=[
                r'(?:от|күні|дата)?\s*[«"]?(\d{2}[.\-/]\d{2}[.\-/]\d{4})',
            ],
            name='addendum_date',
            label_ru='Дата дополнительного соглашения',
            pages=2,
            confidence=0.82,
        ))
    tranche_numbers = find_all_regex(document, r'Номер транша\s*[:/]\s*([A-ZА-Я0-9./_-]{5,40})')
    if tranche_numbers:
        fields.append(field(name='tranche_numbers', label_ru='Номера траншей', value=tranche_numbers, page=None,
                            quote=None, confidence=0.68 if document.used_ocr else 0.94,
                            extraction_method='mixed' if document.used_ocr else 'digital', status='candidate'))
    tranches = []
    for raw in find_all_regex(document, r'Сумма транша\s*[:/]\s*(\d[\d\s]*(?:[,.]\d{1,2})?)'):
        value = parse_money(raw)
        if value is not None:
            tranches.append(str(value))
    if tranches:
        fields.append(field(name='tranche_amounts_kzt', label_ru='Суммы траншей, тенге', value=tranches,
                            page=None, quote=None, confidence=0.68 if document.used_ocr else 0.92,
                            extraction_method='mixed' if document.used_ocr else 'digital', status='candidate'))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)



def _percent(raw: str) -> float:
    return float(raw.replace(',', '.'))


def parse_credit_line_agreement(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    _append(fields, find_first(document, patterns=[
        r'(?:СОГЛАШЕНИЕ\s+ОБ\s+ОТКРЫТИИ\s+(?:КЛ|КРЕДИТНОЙ\s+ЛИНИИ)).{0,80}?(?:№|N)\s*([A-ZА-Я0-9./_-]{5,50})',
        r'(?:КЖ\s+АШУ\s+ТУРАЛЫ\s+КЕЛІСІМ).{0,80}?(?:№|N)\s*([A-ZА-Я0-9./_-]{5,50})',
    ], name='credit_line_number', label_ru='Номер соглашения об открытии КЛ',
       converter=normalize_contract_number, validator=valid_contract_number, pages=2))
    _append(fields, find_first(document, patterns=[
        r'Договору\s+присоединения\s*(?:№|N)\s*([A-ZА-Я0-9./_-]{1,30})',
        r'(?:№|N)\s*([A-ZА-Я0-9./_-]{1,30})\s+Қосылу\s+шартына',
    ], name='accession_contract_number', label_ru='Номер договора присоединения',
       converter=normalize_contract_number, validator=lambda v: bool(re.search(r'\d', v)), pages=2))
    # date pattern with multiple groups needs direct processing
    if not any(x['name']=='agreement_date' for x in fields):
        for page in document.pages[:2]:
            m=re.search(r'Дата\s+подписания\s*[:\-]?\s*[«"]?(\d{1,2})[»"]?\s*(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s*(20\d{2})', page.text, re.I)
            if m:
                fields.append(field(name='agreement_date', label_ru='Дата соглашения', value=date_value(*m.groups()), page=page.page_number, quote=page.text[max(0,m.start()-100):m.end()+100], confidence=0.98 if page.extraction_method=='digital' else page.quality, extraction_method=page.extraction_method)); break
    _append(fields, find_first(document, patterns=[r'(?:БИН|БСН)\s*(\d{12}).{0,180}?(?:далее|бұдан\s+әрі)[^\n]{0,40}(?:Заемщик|Қарыз\s+алушы)'], name='borrower_bin', label_ru='БИН — Заёмщик', pages=3))
    _append(fields, find_first(document, patterns=[r'(?:Товарищество\s+с\s+ограниченной\s+ответственностью|ТОО)\s*[«"]([^»"\n]{2,100})[»"]?.{0,220}?(?:далее|бұдан\s+әрі)[^\n]{0,40}(?:Заемщик|Қарыз\s+алушы)'], name='borrower_name', label_ru='Заёмщик', pages=3))
    _append(fields, find_first(document, patterns=[r'(?:Сумма\s+КЛ|КЖ\s+сомасы)\s*[:\-]?\s*(\d[\d \u00a0]*(?:[,.]\d{1,2})?)'], name='credit_line_amount_kzt', label_ru='Сумма кредитной линии, тенге', converter=parse_money, pages=3))
    _append(fields, find_first(document, patterns=[r'(?:Срок\s+КЛ|КЖ\s+мерзімі)\s*[:\-]?\s*(\d{1,3})\s*\('], name='credit_line_term_months', label_ru='Срок кредитной линии, месяцев', converter=int, pages=3))
    _append(fields, find_first(document, patterns=[r'(?:фиксированное\s+вознаграждение|сыйақы).{0,120}?из\s+расчета\s*(\d{1,2}(?:[,.]\d+)?)\s*%'], name='interest_rate_percent', label_ru='Ставка вознаграждения, %', converter=_percent, pages=4))
    _append(fields, find_first(document, patterns=[r'(?:ГЭСВ|СЖТМ).{0,80}?(\d{1,2}(?:[,.]\d+)?)\s*%'], name='effective_rate_percent', label_ru='ГЭСВ, %', converter=_percent, pages=4))
    _append(fields, find_first(document, patterns=[r'Сумма\s+гарантии\s+Фонда\s+составляет\s*(\d[\d \u00a0]*(?:[,.]\d{1,2})?)'], name='fund_guarantee_amount_kzt', label_ru='Сумма гарантии Фонда, тенге', converter=parse_money, pages=4))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)


def parse_cash_pledge_agreement(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    _append(fields, find_first(document, patterns=[
        r'(?:ДОГОВОР\s+ЗАЛОГА\s+ДЕНЕГ\s+НА\s+СЧЕТЕ|ШОТТАҒЫ\s+АҚШАНЫ\s+КЕПІЛГЕ\s+БЕРУ\s+ШАРТЫ).{0,100}?(?:№|N)\s*([A-ZА-Я0-9./_-]{5,50})',
    ], name='pledge_contract_number', label_ru='Номер договора залога', converter=normalize_contract_number, validator=valid_contract_number, pages=2))
    _append(fields, find_first(document, patterns=[r'(?:БИН|БСН)\s*(\d{12}).{0,160}?(?:Заемщик|Залогодатель|Қарыз\s+алушы|Кепіл\s+беруші)'], name='pledgor_bin', label_ru='БИН — Залогодатель', pages=3))
    _append(fields, find_first(document, patterns=[r'(?:в\s+сумме|сомадағы)\s*(\d[\d \u00a0]*(?:[,.]\d{1,2})?)\s*(?:тенге|теңге).{0,160}?(?:депозит|вклад|кепіл\s+заты)'], name='pledge_amount_kzt', label_ru='Сумма денежного залога, тенге', converter=parse_money, pages=3))
    _append(fields, find_first(document, patterns=[r'\b(KZ[0-9A-Z]{18})\b'], name='deposit_iban', label_ru='Счёт денежного залога', pages=4))
    _append(fields, find_first(document, patterns=[r'(?:сумм[аы]\s+банковского\s+займа|банктік\s+қарыз\s+сомасы).{0,80}?(\d[\d \u00a0]*(?:[,.]\d{1,2})?)'], name='secured_amount_kzt', label_ru='Обеспечиваемая сумма, тенге', converter=parse_money, pages=3))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)


def parse_subsidy_agreement(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    _append(fields, find_first(document, patterns=[r'Договор\s+субсидирования.{0,240}?(?:№|N)\s*([A-ZА-Я0-9./_-]{5,50})'], name='subsidy_contract_number', label_ru='Номер договора субсидирования', converter=normalize_contract_number, validator=valid_contract_number, pages=2))
    _append(fields, find_first(document, patterns=[r'договор\s+финансового\s+лизинга\s*(?:№|N)?\s*([A-ZА-Я0-9./_-]{5,50})\s+от'], name='linked_lease_contract_number', label_ru='Связанный договор финансового лизинга', converter=normalize_contract_number, validator=valid_contract_number, pages=4))
    _append(fields, find_first(document, patterns=[r'(?:Товарищество\s+с\s+ограниченной\s+ответственностью|ТОО)\s*[«"]([^»"\n]{2,100})[»"].{0,200}?(?:именуем(?:ый|ое)\s+в\s+дальнейшем\s*[«"]Получатель)'], name='recipient_name', label_ru='Получатель', pages=3))
    _append(fields, find_first(document, patterns=[r'Сумма\s+(?:кредита|микрокредита|лизинга).{0,80}?(\d[\d \u00a0]*(?:[,.]\d{1,2})?)'], name='financing_amount_kzt', label_ru='Сумма финансирования, тенге', converter=parse_money, pages=5))
    _append(fields, find_first(document, patterns=[r'Ставка\s+вознаграждения\s*(\d{1,2}(?:[,.]\d+)?)'], name='nominal_rate_percent', label_ru='Ставка вознаграждения, %', converter=_percent, pages=5))
    _append(fields, find_first(document, patterns=[r'часть\s+ставки\s+вознаграждения\s+в\s+размере\s*(\d{1,2}(?:[,.]\d+)?)'], name='subsidized_rate_percent', label_ru='Субсидируемая ставка, %', converter=_percent, pages=6))
    _append(fields, find_first(document, patterns=[r'остальную\s+часть\s+ставки\s+вознаграждения\s+в\s+размере\s*(\d{1,2}(?:[,.]\d+)?)'], name='recipient_rate_percent', label_ru='Ставка, оплачиваемая получателем, %', converter=_percent, pages=6))
    _append(fields, find_first(document, patterns=[r'Целевое\s+назначение\s*[:\-]?\s*(?:\n\s*)?([^\n]{5,180})'], name='financing_purpose', label_ru='Целевое назначение', pages=5))
    _append(fields, find_first(document, patterns=[r'Срок\s+(?:кредита|микрокредита|лизинга)\s*с\s*(\d{2}[.\-/]\d{2}[.\-/]\d{4})'], name='financing_start_date', label_ru='Начало срока финансирования', pages=5))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)

def parse_signature_receipt(document: ReadDocument) -> list[dict]:
    fields = parse_generic(document)
    _append(fields, find_first(document, patterns=[r'Тема\s+([^\n]{3,100})'], name='document_subject', label_ru='Тема документа'))
    _append(fields, find_first(document, patterns=[r'Статус\s+([^\n]{3,50})'], name='signature_status', label_ru='Статус подписания'))
    _append(fields, find_first(document, patterns=[r'Рег\.\s*Дата\s+(\d{2}\.\d{2}\.\d{4})'], name='registration_date', label_ru='Дата регистрации'))
    return _deduplicate(fields)


def parse_by_type(document: ReadDocument, doc_type: str) -> list[dict]:
    parsers = {
        'purchase_contract': parse_purchase_contract,
        'lease_contract': parse_lease_contract,
        'acceptance_act': parse_acceptance_act,
        'payment_schedule': parse_payment_schedule,
        'addendum': parse_addendum,
        'credit_line_agreement': parse_credit_line_agreement,
        'cash_pledge_agreement': parse_cash_pledge_agreement,
        'subsidy_agreement': parse_subsidy_agreement,
        'signature_receipt': parse_signature_receipt,
    }
    return parsers.get(doc_type, parse_generic)(document)


def _deduplicate(fields: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for item in fields:
        key = item['name']
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
