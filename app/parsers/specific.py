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

    _append(fields, find_first(document, patterns=[
        r'Общая (?:стоимость|цена)(?: настоящего)? (?:Договора|Оборудования)\s*(?:составляет|:)\s*(\d[\d\s]*(?:[,.]\d{1,2})?)\s*(?:тенге|KZT)',
        r'Цена Оборудования[^\n]{0,100}?составляет\s*(\d[\d\s]*(?:[,.]\d{1,2})?)\s*тенге',
        r'БАРЛЫҒЫ\s*/\s*ИТОГО\s*[:\-]?\s*(\d[\d\s]*(?:[,.]\d{1,2})?)',
        r'(?:ОБЩАЯ|ПОЛНАЯ)\s+(?:СТОИМОСТЬ|ЦЕНА)[^\d]{0,120}(\d[\d\s]*(?:[,.]\d{1,2})?)\s*(?:ТЕНГЕ|KZT)',
        r'НА\s+ОБЩУЮ\s+СУММУ[^\d]{0,100}(\d[\d\s]*(?:[,.]\d{1,2})?)\s*(?:ТЕНГЕ|KZT)',
    ], name='total_amount_kzt', label_ru='Общая стоимость договора, тенге', converter=parse_money))
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

    # A single PDF may contain a package of several acts. Capture all explicit
    # act numbers instead of forcing one document-level number.
    act_number_candidates = []
    act_patterns = [
        r'АКТ\s+ПРИЕМА[-\s]ПЕРЕДАЧИ(?:\s+ТОВАРА)?\s*(?:№|N)\s*([A-ZА-Я0-9./_-]{1,30})',
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
        or repeated_act_headers >= 2
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
        r'АКТ\s+ПРИЕМА[-\s]ПЕРЕДАЧИ(?:\s+ТОВАРА)?\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{0,30})',
        r'ҚАБЫЛДАУ-ӨТКІЗУ\s+АКТІСІ.{0,80}?(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{0,30})',
    ], name='act_number', label_ru='Номер акта', converter=normalize_contract_number,
       validator=lambda v: bool(re.search(r'\d', v) and len(v) <= 30), pages=2)
    if not act_number:
        m = re.search(
            r'Акт[^\n]{0,180}?(?:№|N)\s*([A-ZА-Я0-9./_-]+)',
            document.filename,
            re.I,
        )
        if m:
            act_number = field(name='act_number', label_ru='Номер акта', value=m.group(1), page=None,
                               quote=f'Имя файла: {document.filename}', confidence=0.7,
                               extraction_method='filename', status='candidate', notes='Извлечено из имени файла.')
    if not is_act_package and len(act_number_candidates) <= 1:
        _append(fields, act_number)
    _append(fields, find_first(document, patterns=[
        r'Договор[ау]? купли[-\s]продажи.{0,80}?(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{3,40})',
    ], name='linked_purchase_contract', label_ru='Связанный договор купли-продажи', converter=normalize_contract_number, validator=valid_contract_number, pages=3))
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
    _append(fields, find_first(document, patterns=[r'(?:Сумма\s+(?:займа|транша)|Қарыз(?:дың)?\s+сомасы)\s*[:/\-]?\s*(\d[\d\s]*(?:[,.]\d{1,2})?)'], name='loan_amount_kzt', label_ru='Сумма займа / транша, тенге', converter=parse_money, pages=2))
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
       validator=lambda v: bool(re.search(r'\d', v)), pages=2))
    main_contract = find_first(document, patterns=[
        r'к\s+ДОГОВОРУ\s+ФИНАНСОВОГО\s+ЛИЗИНГА\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,40})',
        r'ДОПОЛНИТЕЛЬНОЕ\s+СОГЛАШЕНИЕ.{0,220}?ДОГОВОРУ\s+ФИНАНСОВОГО\s+ЛИЗИНГА\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,40})',
        r'ҚАРЖЫЛЫҚ\s+ЛИЗИНГ\s+ШАРТЫНА.{0,120}?(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,40})',
        r'(?:№|N)\s*([A-ZА-Я]{1,6}\d*(?:[/_-][A-ZА-Я0-9]+){3,7})\s+ҚАРЖЫЛЫҚ\s+ЛИЗИНГ',
        r'(?:№|N)\s*([A-ZА-Я]{1,6}\d*\s*[/_-]\s*\d{4}\s*[/_-]\s*[A-ZА-Я]\s*[/_-]\s*[A-ZА-Я]\s*[/_-]\s*\d{4,8})',
    ], name='lease_contract_number', label_ru='Номер основного договора',
       converter=normalize_contract_number, validator=valid_contract_number, pages=2)

    # OCR may break the heading, while the same number is repeated in the body.
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
