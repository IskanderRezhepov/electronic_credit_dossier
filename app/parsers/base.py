from __future__ import annotations

import re
from typing import Callable, Iterable

from app.services.document_reader import ReadDocument
from app.services.text_utils import parse_money, quote_around


def field(*, name: str, label_ru: str, value, page: int | None, quote: str | None,
          confidence: float, extraction_method: str, value_type: str = 'direct',
          status: str = 'extracted', notes: str | None = None) -> dict:
    if value.__class__.__name__ == 'Decimal':
        value = str(value)
    return {
        'name': name, 'label_ru': label_ru, 'value': value, 'page': page,
        'quote': quote, 'confidence': round(float(confidence), 2),
        'extraction_method': extraction_method, 'value_type': value_type,
        'status': status, 'notes': notes,
    }


def _page_confidence(document: ReadDocument, page_number: int, base: float) -> float:
    page = next(p for p in document.pages if p.page_number == page_number)
    if page.extraction_method == 'digital':
        return min(base, 0.99)
    return min(base, max(0.42, page.quality))


def find_first(document: ReadDocument, *, patterns: Iterable[str], name: str, label_ru: str,
               converter: Callable | None = None, confidence: float = 0.96,
               validator: Callable | None = None, pages: int | None = None) -> dict | None:
    page_iter = document.pages[:pages] if pages else document.pages
    for page in page_iter:
        for pattern in patterns:
            match = re.search(pattern, page.text, re.IGNORECASE | re.DOTALL)
            if not match:
                continue
            raw = match.group(1) if match.groups() else match.group(0)
            try:
                value = converter(raw) if converter else raw.strip()
            except Exception:
                continue
            if validator and not validator(value):
                continue
            return field(
                name=name, label_ru=label_ru, value=value, page=page.page_number,
                quote=quote_around(page.text, match.start(), match.end()),
                confidence=_page_confidence(document, page.page_number, confidence),
                extraction_method=page.extraction_method,
            )
    return None


def find_all_regex(document: ReadDocument, pattern: str) -> list[str]:
    values: set[str] = set()
    for page in document.pages:
        for value in re.findall(pattern, page.text, re.IGNORECASE):
            if isinstance(value, tuple):
                value = next((part for part in value if part), '')
            value = str(value).strip()
            if value:
                values.add(value)
    return sorted(values)


def normalize_contract_number(raw: str) -> str:
    value = raw.upper().replace('№', '').strip()
    value = value.replace('\\', '/').replace('|', '/').replace('—', '-').replace('–', '-')
    value = re.sub(r'\s+', '', value)
    value = re.sub(r'\.(?=/)', '', value)
    value = re.sub(r'/+', '/', value)
    return value.strip('.,;:')


def valid_contract_number(value: str) -> bool:
    """Reject OCR words and accept real slash/dash or compact identifiers."""
    if not (5 <= len(value) <= 50):
        return False

    upper = value.upper()
    if not re.search(r"\d", upper):
        return False

    stopwords = {
        "КОСЫМША",
        "ҚОСЫМША",
        "ПРИЛОЖЕНИЕ",
        "ДОГОВОР",
        "ШАРТ",
        "АКТ",
        "ГРАФИК",
        "ЛИЗИНГ",
    }
    if any(word in upper for word in stopwords):
        return False

    digit_count = len(re.findall(r"\d", upper))
    letter_count = len(re.findall(r"[A-ZА-ЯӘІҢҒҮҰҚӨҺ]", upper))

    if "/" in upper or "-" in upper:
        # A real contract number normally has at least two digits and more than
        # one meaningful segment.
        segments = [segment for segment in re.split(r"[/_-]+", upper) if segment]
        return digit_count >= 2 and len(segments) >= 2

    # Compact IDs such as KAZ14112024KTL.
    return bool(
        len(upper) >= 8
        and digit_count >= 4
        and letter_count >= 2
        and re.fullmatch(r"[A-ZА-ЯӘІҢҒҮҰҚӨҺ0-9]+", upper)
    )


def date_value(day: str, month: str, year: str) -> str:
    months = {
        'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
        'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12,
    }
    return f'{int(day):02d}.{months[month.lower()]:02d}.{year}'


def filename_number(document: ReadDocument, pattern: str, name: str, label_ru: str) -> dict | None:
    match = re.search(pattern, document.filename, re.I)
    if not match:
        return None
    value = normalize_contract_number(match.group(1))
    if not valid_contract_number(value):
        return None
    return field(name=name, label_ru=label_ru, value=value, page=None, quote=f'Имя файла: {document.filename}',
                 confidence=0.64, extraction_method='filename', status='candidate',
                 notes='Извлечено из имени файла; требуется сверка с документом.')


def generic_identifiers(document: ReadDocument) -> list[dict]:
    result: list[dict] = []
    ids = find_all_regex(document, r'\b\d{12}\b')
    if ids:
        result.append(field(name='iin_bin_candidates', label_ru='Кандидаты ИИН/БИН', value=ids,
                            page=None, quote=None, confidence=0.72 if document.used_ocr else 0.9,
                            extraction_method='mixed' if document.used_ocr else 'digital',
                            status='candidate', notes='Требуется определить роль каждого идентификатора.'))
    vins = find_all_regex(document, r'\b[A-HJ-NPR-Z0-9]{17}\b')
    if vins:
        result.append(field(name='vin_candidates', label_ru='Кандидаты VIN', value=vins, page=None, quote=None,
                            confidence=0.68 if document.used_ocr else 0.93,
                            extraction_method='mixed' if document.used_ocr else 'digital', status='candidate'))
    ibans = find_all_regex(document, r'\bKZ[0-9A-Z]{18}\b')
    if ibans:
        result.append(field(name='iban_candidates', label_ru='Кандидаты IBAN', value=ibans, page=None, quote=None,
                            confidence=0.7 if document.used_ocr else 0.93,
                            extraction_method='mixed' if document.used_ocr else 'digital', status='candidate'))
    doc_ids = find_all_regex(document, r'DOC\s*ID\s*([A-Z0-9]{15,40})')
    if doc_ids:
        result.append(field(name='doc_ids', label_ru='DOC ID', value=doc_ids, page=None, quote=None,
                            confidence=0.8 if document.used_ocr else 0.98,
                            extraction_method='mixed' if document.used_ocr else 'digital'))
    amounts = []
    for raw in find_all_regex(document, r'\b\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})\b'):
        parsed = parse_money(raw)
        if parsed is not None:
            amounts.append(str(parsed))
    if amounts:
        unique_amounts = sorted(set(amounts), key=lambda x: float(x))
        preview = unique_amounts[:20]
        notes = 'Не являются подтверждёнными итоговыми суммами.'
        if len(unique_amounts) > len(preview):
            notes += f' Показаны первые {len(preview)} из {len(unique_amounts)}; полный список доступен в JSON/Excel.'
        result.append(field(name='money_candidates', label_ru='Кандидаты денежных сумм',
                            value=preview, page=None, quote=None,
                            confidence=0.64 if document.used_ocr else 0.85,
                            extraction_method='mixed' if document.used_ocr else 'digital',
                            status='candidate', notes=notes))
    return result
