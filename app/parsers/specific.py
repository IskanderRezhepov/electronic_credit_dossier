from __future__ import annotations

import re

from app.services.document_reader import ReadDocument
from app.services.text_utils import parse_money
from .base import field, find_all_regex, find_first, generic_identifiers, normalize_contract_number, valid_contract_number
from .generic import parse as parse_generic


def _append(fields: list[dict], item: dict | None) -> None:
    if item:
        fields.append(item)


def parse_purchase_contract(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    _append(fields, find_first(document,
        patterns=[
            r'ДОГОВОР\s+купли-продажи(?:\s+товара)?.{0,220}?№\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,30})',
            r'№\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,30}).{0,120}?ДОГОВОР\s+купли-продажи',
        ], name='purchase_contract_number', label_ru='Номер договора купли-продажи',
        converter=normalize_contract_number, validator=valid_contract_number))
    # Отдельный поиск даты с формированием значения, чтобы не брать дату доверенности.
    for page in document.pages[:2]:
        match = re.search(r'(?:Алматы|ДОГОВОР\s+купли-продажи).{0,250}?[«"]?(\d{1,2})[»"]?\s*(?:мая|мамыр)\s*(\d{4})', page.text, re.I | re.S)
        if match:
            fields.append(field(name='purchase_contract_date', label_ru='Дата договора купли-продажи',
                                value=f'{int(match.group(1)):02d}.05.{match.group(2)}', page=page.page_number,
                                quote=page.text[max(0, match.start()-120):match.end()+120],
                                confidence=page.quality if page.extraction_method != 'digital' else 0.98,
                                extraction_method=page.extraction_method))
            break
    _append(fields, find_first(document,
        patterns=[r'Общая стоимость(?: настоящего)? Договора составляет\s*([\d\s]+[,.]\d{2})',
                  r'Барлығы\s*/\s*Итого[:\s]+([\d\s]+[,.]\d{2})'],
        name='total_amount_kzt', label_ru='Общая стоимость договора, тенге', converter=parse_money))
    _append(fields, find_first(document, patterns=[r'в том числе НДС\s*(\d+)%'], name='vat_percent', label_ru='НДС, %', converter=int))
    _append(fields, find_first(document, patterns=[r'поставку Товара в течение\s*(\d+)\s*\([^)]*\)\s*рабочих дней'], name='delivery_term_workdays', label_ru='Срок поставки, рабочих дней', converter=int))
    _append(fields, find_first(document, patterns=[r'в течение\s*(\d+)\s*\([^)]*\)\s*месяцев'], name='warranty_months', label_ru='Гарантия, месяцев', converter=int))
    _append(fields, find_first(document, patterns=[r'или\s*([\d\s]+)\s*\([^)]*\)\s*км'], name='warranty_km', label_ru='Гарантия, км', converter=lambda v: int(re.sub(r'\s+', '', v))))
    _append(fields, find_first(document, patterns=[r'пеню в размере\s*([\d,.]+)%'], name='penalty_percent_daily', label_ru='Пеня, % в день', converter=lambda v: float(v.replace(',', '.'))))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)


def parse_acceptance_act(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    _append(fields, find_first(document,
        patterns=[
            r'АКТ\s+ПРИЕМА-ПЕРЕДАЧИ(?:\s+ТОВАРА)?\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{3,30})',
            r'ҚАБЫЛДАУ-ӨТКІЗУ\s+АКТІСІ.{0,80}?(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{3,30})',
        ], name='act_number', label_ru='Номер акта', converter=normalize_contract_number, validator=valid_contract_number))
    _append(fields, find_first(document,
        patterns=[r'Договору купли-продажи\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{3,30})'],
        name='linked_purchase_contract', label_ru='Связанный договор купли-продажи', converter=normalize_contract_number, validator=valid_contract_number))
    _append(fields, find_first(document,
        patterns=[r'(?:Барлығы\s*/\s*)?Итого[:\s]+(?:\d+\s+)?([\d\s]+[,.]\d{2})',
                  r'Общая стоимость[^\d]{0,80}([\d\s]+[,.]\d{2})'],
        name='act_total_amount_kzt', label_ru='Общая стоимость по акту, тенге', converter=parse_money))
    vins = find_all_regex(document, r'\b[A-HJ-NPR-Z0-9]{17}\b')
    if vins:
        fields.append(field(name='asset_vins', label_ru='VIN по акту', value=vins, page=None, quote=None,
                            confidence=0.7 if document.used_ocr else 0.93,
                            extraction_method='mixed' if document.used_ocr else 'digital', status='candidate'))
        fields.append(field(name='asset_count_calculated', label_ru='Количество единиц по VIN', value=len(vins),
                            page=None, quote='Количество уникальных VIN', confidence=0.9,
                            extraction_method='calculated', value_type='calculated'))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)


def parse_payment_schedule(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    _append(fields, find_first(document,
        patterns=[r'к Договору финансового лизинга\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,30})'],
        name='lease_contract_number', label_ru='Номер договора лизинга', converter=normalize_contract_number, validator=valid_contract_number))
    _append(fields, find_first(document, patterns=[r'Сумма займа:\s*([\d\s]+(?:[,.]\d{2})?)'], name='loan_amount_kzt', label_ru='Сумма займа, тенге', converter=parse_money))
    _append(fields, find_first(document, patterns=[r'Дата выдачи:\s*(\d{2}\.\d{2}\.\d{2,4})'], name='issue_date', label_ru='Дата выдачи'))
    _append(fields, find_first(document, patterns=[r'Дата погашения займа:\s*(\d{2}\.\d{2}\.\d{2,4})'], name='maturity_date', label_ru='Дата погашения'))
    _append(fields, find_first(document, patterns=[r'Ставка вознаграждения.*?(\d{1,2}[,.]\d+)%'], name='interest_rate_percent', label_ru='Ставка вознаграждения, %', converter=lambda v: float(v.replace(',', '.'))))
    _append(fields, find_first(document, patterns=[r'Итого основного долга\s*([\d\s]+[,.]\d{2})'], name='total_principal_kzt', label_ru='Итого основной долг, тенге', converter=parse_money))
    _append(fields, find_first(document, patterns=[r'Итого процентов\s*([\d\s]+[,.]\d{2})'], name='total_interest_kzt', label_ru='Итого вознаграждение, тенге', converter=parse_money))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)


def parse_addendum(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    _append(fields, find_first(document,
        patterns=[r'ДОПОЛНИТЕЛЬНОЕ\s+СОГЛАШЕНИЕ\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{2,30})'],
        name='addendum_number', label_ru='Номер дополнительного соглашения', converter=normalize_contract_number, validator=valid_contract_number))
    _append(fields, find_first(document,
        patterns=[r'к\s+ДОГОВОРУ\s+ФИНАНСОВОГО\s+ЛИЗИНГА\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,30})'],
        name='lease_contract_number', label_ru='Номер основного договора', converter=normalize_contract_number, validator=valid_contract_number))
    tranches = []
    for raw in find_all_regex(document, r'Сумма транша:\s*([\d\s]+(?:[,.]\d{2})?)'):
        value = parse_money(raw)
        if value is not None:
            tranches.append(str(value))
    if tranches:
        fields.append(field(name='tranche_amounts_kzt', label_ru='Суммы траншей, тенге', value=tranches,
                            page=None, quote=None, confidence=0.68 if document.used_ocr else 0.9,
                            extraction_method='mixed' if document.used_ocr else 'digital', status='candidate'))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)


def parse_by_type(document: ReadDocument, doc_type: str) -> list[dict]:
    if doc_type == 'purchase_contract':
        return parse_purchase_contract(document)
    if doc_type == 'acceptance_act':
        return parse_acceptance_act(document)
    if doc_type == 'payment_schedule':
        return parse_payment_schedule(document)
    if doc_type == 'addendum':
        return parse_addendum(document)
    return parse_generic(document)


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
