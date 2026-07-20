from __future__ import annotations

import re

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


def _party(document: ReadDocument, role: str, name: str, label_ru: str) -> dict | None:
    patterns = [
        rf'{role}[^\n:]{{0,30}}[:\-]\s*(?:ТОО|АО|ИП|Товарищество|Индивидуальный предприниматель)?\s*[«"]?([^\n,;]{{3,100}})',
        rf'далее именуем(?:ое|ый|ая)\s*[«"]{role}[»"].{{0,180}}?(?:ТОО|АО|ИП)\s*[«"]([^»"\n]{{3,100}})',
    ]
    return find_first(document, patterns=patterns, name=name, label_ru=label_ru, confidence=0.88)


def parse_purchase_contract(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    number = find_first(document, patterns=[
        r'ДОГОВОР\s+КУПЛИ[-\s]ПРОДАЖИ(?:\s+ТОВАРА)?.{0,100}?(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,40})',
        r'(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,40}).{0,120}?ДОГОВОР\s+КУПЛИ[-\s]ПРОДАЖИ',
    ], name='purchase_contract_number', label_ru='Номер договора купли-продажи',
       converter=normalize_contract_number, validator=valid_contract_number, pages=2)
    if not number:
        number = filename_number(document, r'(?:ДКП|купли-продажи).*?([A-ZА-Я0-9]+(?:[/_-][A-ZА-Я0-9]+){1,5})',
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
        r'Общая (?:стоимость|цена)(?: настоящего)? (?:Договора|Оборудования)\s*(?:составляет|:)\s*([\d\s]+[,.]\d{1,2})',
        r'БАРЛЫҒЫ\s*/\s*ИТОГО\s*([\d\s]+[,.]\d{1,2})',
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
    act_number = find_first(document, patterns=[
        r'(?:№\s*)?(\d{1,4})\s+АКТ\s+ПРИЕМА[-\s]ПЕРЕДАЧИ',
        r'АКТ\s+ПРИЕМА[-\s]ПЕРЕДАЧИ(?:\s+ТОВАРА)?\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{0,30})',
        r'ҚАБЫЛДАУ-ӨТКІЗУ\s+АКТІСІ.{0,80}?(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{0,30})',
    ], name='act_number', label_ru='Номер акта', converter=normalize_contract_number,
       validator=lambda v: bool(re.search(r'\d', v) and len(v) <= 30), pages=2)
    if not act_number:
        m = re.search(r'Акт[^\n]*?№\s*(\d+)', document.filename, re.I)
        if m:
            act_number = field(name='act_number', label_ru='Номер акта', value=m.group(1), page=None,
                               quote=f'Имя файла: {document.filename}', confidence=0.7,
                               extraction_method='filename', status='candidate', notes='Извлечено из имени файла.')
    _append(fields, act_number)
    _append(fields, find_first(document, patterns=[
        r'Договор[ау]? купли[-\s]продажи.{0,80}?(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{3,40})',
    ], name='linked_purchase_contract', label_ru='Связанный договор купли-продажи', converter=normalize_contract_number, validator=valid_contract_number, pages=3))
    _append(fields, find_first(document, patterns=[
        r'(?:БАРЛЫҒЫ\s*/\s*)?ИТОГО\s*(?:[:\-]|\d+\s+)?\s*([\d\s]+[,.]\d{1,2})',
        r'Общая стоимость[^\d]{0,80}([\d\s]+[,.]\d{1,2})',
    ], name='act_total_amount_kzt', label_ru='Общая стоимость по акту, тенге', converter=parse_money))
    vins = find_all_regex(document, r'\b[A-HJ-NPR-Z0-9]{17}\b')
    if vins:
        fields.append(field(name='asset_vins', label_ru='VIN по акту', value=vins, page=None, quote=None,
                            confidence=0.68 if document.used_ocr else 0.93,
                            extraction_method='mixed' if document.used_ocr else 'digital', status='candidate'))
        fields.append(field(name='asset_count_calculated', label_ru='Количество единиц по VIN', value=len(vins),
                            page=None, quote='Количество уникальных VIN', confidence=0.9,
                            extraction_method='calculated', value_type='calculated'))
    _append(fields, find_first(document, patterns=[r'Наименование Товара.{0,500}?\n?\s*1\s+([^\n|]{3,120})'], name='asset_name', label_ru='Наименование имущества'))
    fields.extend(generic_identifiers(document))
    return _deduplicate(fields)


def parse_payment_schedule(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    _append(fields, find_first(document, patterns=[
        r'к Договору финансового лизинга\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,40})',
        r'Қаржылық лизинг шартына.{0,80}?(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,40})',
    ], name='lease_contract_number', label_ru='Номер договора лизинга', converter=normalize_contract_number, validator=valid_contract_number, pages=2))
    _append(fields, find_first(document, patterns=[r'(?:Сумма займа|Сумма транша)\s*[:/]\s*([\d\s]+(?:[,.]\d{1,2})?)'], name='loan_amount_kzt', label_ru='Сумма займа / транша, тенге', converter=parse_money, pages=2))
    _append(fields, find_first(document, patterns=[r'Дата выдачи\s*[:/]\s*(\d{2}[.-]\d{2}[.-]\d{2,4})'], name='issue_date', label_ru='Дата выдачи', pages=2))
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
    _append(fields, find_first(document, patterns=[
        r'к\s+ДОГОВОРУ\s+ФИНАНСОВОГО\s+ЛИЗИНГА\s*(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9./_-]{4,40})',
    ], name='lease_contract_number', label_ru='Номер основного договора', converter=normalize_contract_number, validator=valid_contract_number, pages=2))
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
    for raw in find_all_regex(document, r'Сумма транша\s*[:/]\s*([\d\s]+(?:[,.]\d{1,2})?)'):
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
