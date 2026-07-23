from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime

from app.parsers.base import field, normalize_contract_number
from app.services.text_utils import parse_money

DATE_TOKEN = r"(\d{2}[.\-/]\d{2}[.\-/]\d{4})"
MONEY_TOKEN = r"(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?)"
VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")


def _normal_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.replace('/', '.').replace('-', '.'), '%d.%m.%Y').strftime('%d.%m.%Y')
    except ValueError:
        return None


def _quote(text: str, match, radius: int = 180) -> str:
    return text[max(0, match.start()-radius):match.end()+radius]


def _upsert(fields: list[dict], item: dict) -> None:
    current = next((x for x in fields if x.get('name') == item.get('name')), None)
    if current is None:
        fields.append(item)
        return
    old = (current.get('status') != 'candidate', float(current.get('confidence') or 0))
    new = (item.get('status') != 'candidate', float(item.get('confidence') or 0))
    if new >= old:
        current.clear()
        current.update(item)


def _assign(fields, page, name, label, value, match, confidence=.94, notes=None):
    _upsert(fields, field(
        name=name, label_ru=label, value=value, page=page.page_number,
        quote=_quote(page.text, match), confidence=confidence,
        extraction_method=page.extraction_method, status='extracted', notes=notes,
    ))


def is_insurance_document(document) -> bool:
    first = document.pages[0].text.upper()[:5000] if document.pages else ''
    full = document.full_text.upper()
    title = bool(re.search(r'(?:ДОГОВОР|ПОЛИС|СЕРТИФИКАТ).{0,100}(?:СТРАХОВАН|КАСКО)', first, re.S))
    strong = sum(token in full for token in (
        'СТРАХОВАТЕЛ', 'СТРАХОВЩИК', 'СТРАХОВАЯ СУММА',
        'СТРАХОВАЯ ПРЕМИЯ', 'ВЫГОДОПРИОБРЕТАТЕЛ',
    ))
    return title or strong >= 3


def is_gps_document(document) -> bool:
    first = document.pages[0].text.upper()[:5000] if document.pages else ''
    full = document.full_text.upper()
    title = bool(re.search(r'(?:ДОГОВОР|АКТ|ЗАЯВКА).{0,140}(?:GPS|ГЛОНАСС|СПУТНИКОВ|МОНИТОРИНГ)', first, re.S))
    strong = sum(token in full for token in (
        'GPS', 'ГЛОНАСС', 'МОНИТОРИНГ ТРАНСПОРТА',
        'ТРЕКЕР', 'НАВИГАЦИОННОЕ ОБОРУДОВАНИЕ',
    ))
    return title or strong >= 2


def insurance_type(text: str) -> str:
    upper = text.upper()
    if 'КАСКО' in upper:
        return 'КАСКО'
    if 'ОГПО' in upper or 'ОБЯЗАТЕЛЬНОЕ СТРАХОВАНИЕ ГРАЖДАНСКО-ПРАВОВОЙ' in upper:
        return 'ОГПО'
    if 'ИМУЩЕСТВ' in upper:
        return 'Страхование имущества'
    if 'ТРАНСПОРТ' in upper or 'АВТОМОБИЛ' in upper:
        return 'Страхование транспорта'
    return 'Страхование'


def _status(end_date: str | None) -> tuple[str | None, int | None]:
    if not end_date:
        return None, None
    end = datetime.strptime(end_date, '%d.%m.%Y').date()
    days = (end - date.today()).days
    if days < 0:
        return 'Истёк', days
    if days <= 30:
        return 'Скоро заканчивается', days
    return 'Действует', days


def extract_insurance_fields(document, fields: list[dict]) -> list[dict]:
    result = deepcopy(fields)
    if not is_insurance_document(document):
        return result

    page0 = document.pages[0]
    _upsert(result, field(
        name='insurance_type', label_ru='Вид страхования', value=insurance_type(document.full_text),
        page=1, quote=page0.text[:500], confidence=.92,
        extraction_method=page0.extraction_method, status='extracted',
    ))

    for page in document.pages:
        company = re.search(
            r'(?:Страховщик|Страховая компания|Сақтандырушы)\s*[:\-]?\s*'
            r'((?:АО|ТОО|ИП)?\s*[«"]?[^\n;]{3,100})', page.text, re.I,
        )
        if company:
            value = re.sub(r'\s+', ' ', company.group(1)).strip(' ,.;«»"')
            value = re.split(r'\s+(?:именуем|далее|БИН|БСН)\b', value, 1, flags=re.I)[0].strip()
            _assign(result, page, 'insurance_company', 'Страховая компания', value, company, .96)

        number = re.search(
            r'(?:Страховой полис|Полис|Договор страхования|Сертификат)\s*№?\s*'
            r'([A-ZА-Я0-9][A-ZА-Я0-9_./\-]{4,50})', page.text, re.I,
        )
        if number:
            _assign(result, page, 'insurance_policy_number', 'Номер полиса / договора страхования',
                    normalize_contract_number(number.group(1)), number, .95)

        date_specs = (
            ('insurance_start_date', 'Дата начала страхования', r'(?:Дата начала|Начало срока|Период страхования\s+с|страхование действует с)\s*[:\-]?\s*' + DATE_TOKEN),
            ('insurance_end_date', 'Дата окончания страхования', r'(?:Дата окончания|Окончание срока|действует до|\bпо)\s*[:\-]?\s*' + DATE_TOKEN),
            ('insurance_contract_date', 'Дата договора / полиса страхования', r'(?:Дата заключения|Дата выдачи|\bот)\s*[:\-]?\s*' + DATE_TOKEN),
            ('insurance_renewal_date', 'Дата пролонгации страхования', r'(?:Дата пролонгации|Следующая пролонгация|продлить до)\s*[:\-]?\s*' + DATE_TOKEN),
        )
        for name, label, pattern in date_specs:
            match = re.search(pattern, page.text, re.I)
            if match:
                value = _normal_date(match.group(1))
                if value:
                    _assign(result, page, name, label, value, match, .94)

        money_specs = (
            ('insurance_sum_kzt', 'Страховая сумма, тенге', r'(?:Страховая сумма|Сақтандыру сомасы)\s*[:\-]?\s*' + MONEY_TOKEN),
            ('insurance_premium_kzt', 'Страховая премия, тенге', r'(?:Страховая премия|Сақтандыру сыйлықақысы)\s*[:\-]?\s*' + MONEY_TOKEN),
        )
        for name, label, pattern in money_specs:
            match = re.search(pattern, page.text, re.I)
            if match:
                value = parse_money(match.group(1))
                if value is not None and float(value) >= 100:
                    _assign(result, page, name, label, float(value), match, .96)

        beneficiary = re.search(r'(?:Выгодоприобретатель|Пайда алушы)\s*[:\-]?\s*([^\n;]{3,120})', page.text, re.I)
        if beneficiary:
            value = re.sub(r'\s+', ' ', beneficiary.group(1)).strip(' ,.;')
            _assign(result, page, 'insurance_beneficiary', 'Выгодоприобретатель', value, beneficiary, .93)

    end_date = next((x.get('value') for x in result if x.get('name') == 'insurance_end_date'), None)
    status, days = _status(end_date)
    if status:
        _upsert(result, field(
            name='insurance_status', label_ru='Статус страхования', value=status,
            page=None, quote=None, confidence=.99, extraction_method='calculated',
            value_type='calculated', status='extracted',
            notes=f'До даты окончания: {days} дн.',
        ))
        _upsert(result, field(
            name='insurance_days_remaining', label_ru='Дней до окончания страхования', value=days,
            page=None, quote=None, confidence=.99, extraction_method='calculated',
            value_type='calculated', status='extracted',
        ))
    return result


def extract_gps_fields(document, fields: list[dict]) -> list[dict]:
    result = deepcopy(fields)
    if not is_gps_document(document):
        return result
    for page in document.pages:
        provider = re.search(
            r'(?:Исполнитель|Поставщик|Оператор|Компания)\s*[:\-]?\s*'
            r'((?:ТОО|АО|ИП)?\s*[«"]?[^\n;]{3,100})', page.text, re.I,
        )
        if provider:
            value = re.sub(r'\s+', ' ', provider.group(1)).strip(' ,.;«»"')
            _assign(result, page, 'gps_provider', 'Поставщик GPS / мониторинга', value, provider, .92)
        number = re.search(
            r'(?:Договор|Заявка|Акт)\s*№?\s*([A-ZА-Я0-9][A-ZА-Я0-9_./\-]{4,50})',
            page.text, re.I,
        )
        if number:
            _assign(result, page, 'gps_contract_number', 'Номер договора GPS',
                    normalize_contract_number(number.group(1)), number, .93)
        for name, label, pattern in (
            ('gps_start_date', 'Дата начала GPS-мониторинга', r'(?:Дата начала|Начало оказания услуг|действует с)\s*[:\-]?\s*' + DATE_TOKEN),
            ('gps_end_date', 'Дата окончания GPS-мониторинга', r'(?:Дата окончания|Окончание срока|действует до)\s*[:\-]?\s*' + DATE_TOKEN),
        ):
            match = re.search(pattern, page.text, re.I)
            if match:
                value = _normal_date(match.group(1))
                if value:
                    _assign(result, page, name, label, value, match, .92)
        fee = re.search(r'(?:Стоимость услуг|Абонентская плата|Ежемесячная плата)\s*[:\-]?\s*' + MONEY_TOKEN, page.text, re.I)
        if fee:
            value = parse_money(fee.group(1))
            if value is not None and float(value) >= 100:
                _assign(result, page, 'gps_service_fee_kzt', 'Стоимость GPS-услуг, тенге', float(value), fee, .94)
    return result


def _insurance_table(document, fields):
    if not is_insurance_document(document):
        return None
    values = {x.get('name'): x.get('value') for x in fields if not isinstance(x.get('value'), list)}
    vins = sorted(set(v for v in VIN_RE.findall(document.full_text.upper()) if any(ch.isdigit() for ch in v)))
    row = {
        'insurance_type': values.get('insurance_type'),
        'insurance_company': values.get('insurance_company'),
        'policy_number': values.get('insurance_policy_number'),
        'start_date': values.get('insurance_start_date'),
        'end_date': values.get('insurance_end_date'),
        'renewal_date': values.get('insurance_renewal_date'),
        'insurance_sum_kzt': values.get('insurance_sum_kzt'),
        'insurance_premium_kzt': values.get('insurance_premium_kzt'),
        'beneficiary': values.get('insurance_beneficiary'),
        'vin': ', '.join(vins[:10]) or None,
        'status': values.get('insurance_status'),
        'days_remaining': values.get('insurance_days_remaining'),
    }
    columns = [
        ('insurance_type', 'Вид страхования'), ('insurance_company', 'Страховая компания'),
        ('policy_number', 'Номер полиса'), ('start_date', 'Дата начала'),
        ('end_date', 'Дата окончания'), ('renewal_date', 'Дата пролонгации'),
        ('insurance_sum_kzt', 'Страховая сумма, тенге'),
        ('insurance_premium_kzt', 'Страховая премия, тенге'),
        ('beneficiary', 'Выгодоприобретатель'), ('vin', 'VIN / идентификаторы'),
        ('status', 'Статус'), ('days_remaining', 'Дней до окончания'),
    ]
    return {
        'name': 'insurance_rows', 'label_ru': 'Страхование',
        'columns': [{'key': key, 'label_ru': label} for key, label in columns],
        'rows': [row], 'row_count': 1, 'confidence': .92, 'status': 'extracted',
        'notes': 'Даты и суммы требуют визуальной сверки с полисом.',
    }


def _gps_table(document, fields):
    if not is_gps_document(document):
        return None
    values = {x.get('name'): x.get('value') for x in fields if not isinstance(x.get('value'), list)}
    vins = sorted(set(v for v in VIN_RE.findall(document.full_text.upper()) if any(ch.isdigit() for ch in v)))
    row = {
        'provider': values.get('gps_provider'),
        'contract_number': values.get('gps_contract_number'),
        'start_date': values.get('gps_start_date'),
        'end_date': values.get('gps_end_date'),
        'service_fee_kzt': values.get('gps_service_fee_kzt'),
        'vin': ', '.join(vins[:10]) or None,
    }
    columns = [
        ('provider', 'Поставщик'), ('contract_number', 'Номер договора'),
        ('start_date', 'Дата начала'), ('end_date', 'Дата окончания'),
        ('service_fee_kzt', 'Стоимость услуг, тенге'), ('vin', 'VIN / техника'),
    ]
    return {
        'name': 'gps_rows', 'label_ru': 'GPS и мониторинг',
        'columns': [{'key': key, 'label_ru': label} for key, label in columns],
        'rows': [row], 'row_count': 1, 'confidence': .88, 'status': 'extracted',
        'notes': 'Поддерживаются договоры, заявки и акты с GPS/ГЛОНАСС/мониторингом.',
    }


def apply_insurance_gps(document, document_type: str, fields: list[dict], tables: list[dict]):
    result_fields = extract_insurance_fields(document, fields)
    result_fields = extract_gps_fields(document, result_fields)
    result_tables = deepcopy(tables)
    for table in (_insurance_table(document, result_fields), _gps_table(document, result_fields)):
        if table:
            result_tables = [t for t in result_tables if t.get('name') != table['name']]
            result_tables.append(table)
    return result_fields, result_tables
