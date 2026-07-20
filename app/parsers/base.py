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
    return min(base, max(0.45, page.quality))


def find_first(document: ReadDocument, *, patterns: Iterable[str], name: str, label_ru: str,
               converter: Callable | None = None, confidence: float = 0.96,
               validator: Callable | None = None) -> dict | None:
    for page in document.pages:
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
    value = value.replace('\\', '/').replace('|', '/')
    value = re.sub(r'\s+', '', value)
    value = re.sub(r'\.(?=/)', '', value)
    value = re.sub(r'/+', '/', value)
    return value.strip('.,;:')


def valid_contract_number(value: str) -> bool:
    return bool(re.search(r'\d', value) and ('/' in value or '-' in value) and len(value) >= 5)


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
        result.append(field(name='vin_candidates', label_ru='VIN', value=vins, page=None, quote=None,
                            confidence=0.7 if document.used_ocr else 0.93,
                            extraction_method='mixed' if document.used_ocr else 'digital', status='candidate'))
    ibans = find_all_regex(document, r'\bKZ\d{18}\b')
    if ibans:
        result.append(field(name='iban_candidates', label_ru='IBAN', value=ibans, page=None, quote=None,
                            confidence=0.72 if document.used_ocr else 0.93,
                            extraction_method='mixed' if document.used_ocr else 'digital', status='candidate'))
    amounts = []
    for raw in find_all_regex(document, r'\b\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{2})\b'):
        parsed = parse_money(raw)
        if parsed is not None:
            amounts.append(str(parsed))
    if amounts:
        result.append(field(name='money_candidates', label_ru='Кандидаты денежных сумм',
                            value=sorted(set(amounts)), page=None, quote=None,
                            confidence=0.65 if document.used_ocr else 0.85,
                            extraction_method='mixed' if document.used_ocr else 'digital',
                            status='candidate', notes='Не являются подтверждёнными итоговыми суммами.'))
    return result
