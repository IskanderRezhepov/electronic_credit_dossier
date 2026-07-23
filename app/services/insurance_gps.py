from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime

from app.parsers.base import field, normalize_contract_number
from app.services.text_utils import parse_money

DATE_TOKEN = r"(\d{2}[.\-/]\d{2}[.\-/]\d{4})"
MONEY_TOKEN = r"(\d{1,3}(?:[ \u00a0]\d{3})*(?:[,.]\d{1,2})?)"
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
        current.clear(); current.update(item)


def _assign(fields, page, name, label, value, match, confidence=.94, notes=None):
    _upsert(fields, field(name=name, label_ru=label, value=value, page=page.page_number,
        quote=_quote(page.text, match), confidence=confidence,
        extraction_method=page.extraction_method, status='extracted', notes=notes))


def _clean_party(value: str) -> str:
    value = re.sub(r'\s+', ' ', value).strip(' ,.;«»"')
    value = re.split(r'\s+(?:в лице|именуем|далее|БИН|БСН|ИИН|ЖСН|Республика|Қазақстан)\b', value, 1, flags=re.I)[0]
    return value.strip(' ,.;«»"')


def is_insurance_document(document) -> bool:
    first = document.pages[0].text.upper()[:7000] if document.pages else ''
    full = document.full_text.upper()
    title = bool(re.search(r'(?:ДОГОВОР|ПОЛИС|СЕРТИФИКАТ).{0,140}(?:СТРАХОВАН|КАСКО)', first, re.S))
    strong = sum(token in full for token in ('СТРАХОВАТЕЛ', 'СТРАХОВЩИК', 'СТРАХОВАЯ СУММА', 'СТРАХОВАЯ ПРЕМИЯ', 'ВЫГОДОПРИОБРЕТАТЕЛ'))
    return title or strong >= 3


def is_gps_document(document) -> bool:
    first = document.pages[0].text.upper()[:7000] if document.pages else ''
    full = document.full_text.upper()
    title = bool(re.search(r'(?:ДОГОВОР|АКТ|ЗАЯВКА).{0,180}(?:GPS|ГЛОНАСС|СПУТНИКОВ|МОНИТОРИНГ)', first, re.S))
    strong = sum(token in full for token in ('GPS', 'ГЛОНАСС', 'МОНИТОРИНГ ТРАНСПОРТА', 'GPS-ТРЕКЕР', 'НАВИГАЦИОННОЕ ОБОРУДОВАНИЕ'))
    return title or strong >= 2


def is_insurance_payment(document) -> bool:
    text = document.full_text.upper()
    return 'ПЛАТЕЖНОЕ ПОРУЧЕНИЕ' in text and ('СТРАХОВАЯ ПРЕМИЯ' in text or 'СТРАХОВЫЕ ПРЕМИИ' in text)


def is_gps_payment(document) -> bool:
    text = document.full_text.upper()
    return 'ПЛАТЕЖНОЕ ПОРУЧЕНИЕ' in text and ('PILOT-COMPANY' in text or 'GPS' in text or 'МОНИТОРИНГ' in text)


def insurance_type(text: str) -> str:
    upper = text.upper()
    if 'КАСКО' in upper or 'ДОБРОВОЛЬНОГО СТРАХОВАНИЯ АВТОМОБИЛЬНОГО ТРАНСПОРТА' in upper:
        return 'КАСКО / добровольное страхование транспорта'
    if 'ОГПО' in upper or 'ОБЯЗАТЕЛЬНОЕ СТРАХОВАНИЕ ГРАЖДАНСКО-ПРАВОВОЙ' in upper:
        return 'ОГПО'
    if 'ИМУЩЕСТВ' in upper:
        return 'Страхование имущества'
    if 'ТРАНСПОРТ' in upper or 'АВТОМОБИЛ' in upper:
        return 'Страхование транспорта'
    return 'Страхование'


def _status(end_date: str | None) -> tuple[str | None, int | None]:
    if not end_date: return None, None
    end = datetime.strptime(end_date, '%d.%m.%Y').date(); days = (end - date.today()).days
    if days < 0: return 'Истёк', days
    if days <= 30: return 'Скоро заканчивается', days
    return 'Действует', days


def _first_match(pages, patterns):
    for page in pages:
        for pattern in patterns:
            m = re.search(pattern, page.text, re.I | re.S)
            if m: return page, m
    return None, None


def extract_insurance_fields(document, fields: list[dict]) -> list[dict]:
    result = deepcopy(fields)
    if not is_insurance_document(document): return result
    page0 = document.pages[0]
    _upsert(result, field(name='insurance_type', label_ru='Вид страхования', value=insurance_type(document.full_text),
        page=1, quote=page0.text[:500], confidence=.96, extraction_method=page0.extraction_method, status='extracted'))

    specs = [
        ('insurance_company','Страховая компания',[r'(?:Страховщик|Сақтандырушы)\s*[:\-]?\s*((?:АО|ТОО|ИП)[^\n]{3,160})'], _clean_party, .98),
        ('insurance_holder','Страхователь',[r'(?:Страхователь|Сақтанушы)\s*[:\-]?\s*((?:АО|ТОО|ИП)[^\n]{3,160})'], _clean_party, .97),
        ('insurance_policy_number','Номер полиса / договора страхования',[
            r'(?:ДОГОВОР\s+СТРАХОВАНИЯ|Договор добровольного страхования[^\n]{0,100}|САҚТАНДЫРУ ШАРТЫ)\s*№\s*([A-ZА-Я0-9][A-ZА-Я0-9_./\-]{3,60})',
            r'(?:Серия\s*№|№)\s*([A-ZА-Я0-9]{1,8}[\-/][A-ZА-Я0-9\-/]{4,40})'], normalize_contract_number, .98),
        ('insurance_contract_date','Дата договора / полиса страхования',[
            r'(?:Дата\s+и\s+место\s+заключения|Дата\s+заключения\s+договора)\s*[^\d]{0,80}'+DATE_TOKEN,
            r'(?:город|г\.)\s*[А-ЯA-Zа-яa-z]+[^\d]{0,40}'+DATE_TOKEN], _normal_date, .96),
        ('insurance_start_date','Дата начала страхования',[
            r'(?:Срок действия Договора и страховой защиты|Срок действия договора страхования[^\n]{0,100}|действует)\s*[^\d]{0,80}(?:с|с «?)\s*'+DATE_TOKEN,
            r'(?:Сақтандыру арқылы қорғаудың мерзімі)[^\d]{0,100}(?:С|с)\s*'+DATE_TOKEN], _normal_date, .98),
        ('insurance_end_date','Дата окончания страхования',[
            r'(?:Срок действия Договора и страховой защиты|Срок действия договора страхования[^\n]{0,180}|действует)[\s\S]{0,220}?(?:по|до)\s*[«"]?'+DATE_TOKEN], _normal_date, .98),
        ('insurance_sum_kzt','Страховая сумма, тенге',[r'(?:Страховая сумма|Сақтандыру сомасы)\s*[:\-]?\s*'+MONEY_TOKEN], parse_money, .98),
        ('insurance_premium_kzt','Страховая премия, тенге',[r'(?:Страховая премия|Сақтандыру сыйлықақысы)[^\d]{0,100}'+MONEY_TOKEN], parse_money, .98),
        ('insurance_tariff_percent','Страховой тариф, %',[r'(?:Страховой тариф|Сақтандыру тарифы)\s*[:\-]?\s*(\d+(?:[,.]\d+)?)\s*%'], lambda x: float(x.replace(',','.')), .96),
        ('insurance_beneficiary','Выгодоприобретатель',[r'(?:Выгодоприобретатель|Пайда алушы)[^\n]{0,120}?((?:АО|ТОО|ИП)\s*[«"]?[^\n;]{3,120})'], _clean_party, .96),
        ('insurance_linked_contract','Связанный договор лизинга / займа',[r'(?:договор[ау]?\s+(?:залога/)?лизинга|Договор залога/займа)\s*№+\s*([A-ZА-Я0-9/._\-]{5,60})'], normalize_contract_number, .96),
    ]
    for name,label,patterns,conv,conf in specs:
        page,m = _first_match(document.pages, patterns)
        if not m: continue
        raw = m.group(1)
        try: value = conv(raw)
        except Exception: continue
        if value is None or value == '': continue
        if name.endswith('_kzt') and float(value) < 100: continue
        _assign(result,page,name,label,float(value) if name.endswith('_kzt') else value,m,conf)

    # multiple linked lease contracts
    linked = sorted(set(normalize_contract_number(x) for x in re.findall(r'\b[A-ZА-Я]{2,4}\d?/2026/[A-ZI]/S/?\d{5,}(?:/\d)?\b', document.full_text, re.I)))
    if linked:
        _upsert(result, field(name='insurance_linked_contracts', label_ru='Связанные договоры лизинга / займа', value=linked,
            page=None, quote=None, confidence=.94, extraction_method='mixed', status='extracted'))

    end_date = next((x.get('value') for x in result if x.get('name') == 'insurance_end_date'), None)
    status, days = _status(end_date)
    if status:
        _upsert(result, field(name='insurance_status', label_ru='Статус страхования', value=status, page=None, quote=None,
            confidence=.99, extraction_method='calculated', value_type='calculated', status='extracted', notes=f'До даты окончания: {days} дн.'))
        _upsert(result, field(name='insurance_days_remaining', label_ru='Дней до окончания страхования', value=days, page=None,
            quote=None, confidence=.99, extraction_method='calculated', value_type='calculated', status='extracted'))
    return result


def _gps_spec_rows(document):
    rows=[]
    for page in document.pages:
        text=page.text
        # Typical Pilot-company appendix: GPS tracker / installation and annual subscription.
        m=re.search(r'GPS[-–\s]?трекер\s+([\d ]{2,12})\s+(\d+)\s+([\d ]{2,12})', text, re.I)
        if m:
            rows.append({'item':'GPS-трекер','unit_price_kzt':parse_money(m.group(1)),'quantity':int(m.group(2)),'total_kzt':parse_money(m.group(3)),'page':page.page_number})
        a=re.search(r'Абонентская плата[^\n]{0,120}?([\d ]{2,12})\s+(\d+)\s+([\d ]{2,12})', text, re.I)
        annual=re.search(r'ИТОГО[^\n]{0,30}(?:за\s*1\s*год)?\s+([\d ]{2,12})\s+(\d+)\s+([\d ]{2,12})', text, re.I)
        if a:
            rows.append({'item':'Абонентская плата GPS','unit_price_kzt':parse_money(a.group(1)),'quantity':int(a.group(2)),'monthly_total_kzt':parse_money(a.group(3)),'annual_total_kzt':parse_money(annual.group(3)) if annual else None,'page':page.page_number})
    return rows


def extract_gps_fields(document, fields: list[dict]) -> list[dict]:
    result=deepcopy(fields)
    if not is_gps_document(document): return result
    specs=[
      ('gps_provider','Поставщик GPS / мониторинга',[r'(?:Поставщик|Исполнитель)\s*[:\-]?\s*((?:ИП|ТОО|АО)\s*[«"]?[^,\n]{3,100})'],_clean_party,.97),
      ('gps_customer','Заказчик GPS',[r'(?:Заказчик)\s*[:\-]?\s*((?:ИП|ТОО|АО)\s*[«"]?[^,\n]{3,100})'],_clean_party,.96),
      ('gps_contract_number','Номер договора GPS',[r'ДОГОВОР\s*№\s*([A-ZА-Я][A-ZА-Я0-9_./\-]{5,60})'],normalize_contract_number,.98),
      ('gps_contract_date','Дата договора GPS',[r'(?:г\.|город)\s*[А-Яа-яA-Za-z]+\s*[«"]?'+DATE_TOKEN],_normal_date,.96),
    ]
    for name,label,patterns,conv,conf in specs:
        page,m=_first_match(document.pages,patterns)
        if m:
            try:value=conv(m.group(1))
            except Exception:continue
            if value:_assign(result,page,name,label,value,m,conf)
    rows=_gps_spec_rows(document)
    if rows:
        equipment=sum(float(r.get('total_kzt') or 0) for r in rows)
        annual=sum(float(r.get('annual_total_kzt') or 0) for r in rows)
        qty=max([int(r.get('quantity') or 0) for r in rows] or [0])
        for name,label,value in (
            ('gps_equipment_total_kzt','Стоимость GPS-оборудования, тенге',equipment or None),
            ('gps_annual_fee_kzt','Абонентская плата за год, тенге',annual or None),
            ('gps_service_fee_kzt','Общая стоимость GPS, тенге',(equipment+annual) or None),
            ('gps_device_quantity','Количество GPS-трекеров',qty or None)):
            if value is not None:
                _upsert(result,field(name=name,label_ru=label,value=value,page=rows[0]['page'],quote=None,confidence=.98,extraction_method='table',status='extracted'))
    # Contract initially one year and renews annually unless cancelled.
    start=next((x.get('value') for x in result if x.get('name')=='gps_contract_date'),None)
    if start:
        d=datetime.strptime(start,'%d.%m.%Y').date()
        try:end=d.replace(year=d.year+1)
        except ValueError:end=d.replace(year=d.year+1,day=28)
        _upsert(result,field(name='gps_start_date',label_ru='Дата начала GPS-мониторинга',value=start,page=None,quote=None,confidence=.85,extraction_method='calculated',status='candidate',notes='Договор действует с подписания.'))
        _upsert(result,field(name='gps_end_date',label_ru='Окончание первоначального срока GPS',value=end.strftime('%d.%m.%Y'),page=None,quote=None,confidence=.82,extraction_method='calculated',status='candidate',notes='Первоначальный срок — один год; далее автоматическая ежегодная пролонгация.'))
    return result


def _extract_payment_fields(document, fields):
    result=deepcopy(fields); text=document.full_text
    if not (is_insurance_payment(document) or is_gps_payment(document)): return result
    kind='insurance' if is_insurance_payment(document) else 'gps'
    page=document.pages[0]
    patterns=[
      (f'{kind}_payment_order_number','Номер платежного поручения',r'(?:ПЛАТЕЖНОЕ ПОРУЧЕНИЕ|ТӨЛЕМ ТАПСЫРМА)[\s\S]{0,80}?№\s*(\d+)',str,.98),
      (f'{kind}_payment_date','Дата платежа',r'№\s*\d+\s+от\s*'+DATE_TOKEN,_normal_date,.98),
      (f'{kind}_payment_amount_kzt','Сумма платежа, тенге',r'(?:Сумма прописью|Сомасы жазбаша)[^\d]{0,30}'+MONEY_TOKEN,parse_money,.98),
      (f'{kind}_payment_invoice_number','Номер оплаченного счёта',r'(?:счету|сч[её]ту)\s*№\s*([A-ZА-Я0-9/._\-]+)',str,.95),
    ]
    for name,label,pat,conv,conf in patterns:
        m=re.search(pat,text,re.I|re.S)
        if not m:continue
        try:v=conv(m.group(1))
        except Exception:continue
        if name.endswith('_kzt'):v=float(v)
        _assign(result,page,name,label,v,m,conf)
    return result


def _insurance_asset_rows(document):
    rows=[]
    for page in document.pages:
        text=page.text
        # Table rows with model, year, VIN, actual value and insured sum.
        for m in re.finditer(r'(?m)^\s*\d+\s+([^\n]{2,45}?)\s+(20\d{2})\s+([A-HJ-NPR-Z0-9\s]{12,22})\s+([\d ]{6,15})\s+([\d ]{6,15})\s*$',text,re.I):
            vin=re.sub(r'\s+','',m.group(3).upper())
            if len(vin)!=17: vin=None
            rows.append({'equipment':re.sub(r'\s+',' ',m.group(1)).strip(),'year':int(m.group(2)),'vin':vin,
                         'actual_value_kzt':parse_money(m.group(4)),'insurance_sum_kzt':parse_money(m.group(5)),'page':page.page_number})
    # Fallback: one row per VIN when table parsing is fragmented.
    if not rows:
        for vin in sorted(set(VIN_RE.findall(document.full_text.upper()))):
            if any(ch.isdigit() for ch in vin): rows.append({'equipment':None,'year':None,'vin':vin,'actual_value_kzt':None,'insurance_sum_kzt':None,'page':None})
    return rows


def _insurance_table(document, fields):
    if not is_insurance_document(document): return None
    values={x.get('name'):x.get('value') for x in fields if not isinstance(x.get('value'),list)}
    assets=_insurance_asset_rows(document)
    row={'insurance_type':values.get('insurance_type'),'insurance_company':values.get('insurance_company'),'policy_number':values.get('insurance_policy_number'),
         'contract_date':values.get('insurance_contract_date'),'start_date':values.get('insurance_start_date'),'end_date':values.get('insurance_end_date'),
         'insurance_sum_kzt':values.get('insurance_sum_kzt'),'insurance_premium_kzt':values.get('insurance_premium_kzt'),'tariff_percent':values.get('insurance_tariff_percent'),
         'beneficiary':values.get('insurance_beneficiary'),'linked_contract':values.get('insurance_linked_contract'),'status':values.get('insurance_status'),'days_remaining':values.get('insurance_days_remaining')}
    columns=[('insurance_type','Вид страхования'),('insurance_company','Страховая компания'),('policy_number','Номер полиса'),('contract_date','Дата договора'),('start_date','Дата начала'),('end_date','Дата окончания'),('insurance_sum_kzt','Страховая сумма, тенге'),('insurance_premium_kzt','Страховая премия, тенге'),('tariff_percent','Тариф, %'),('beneficiary','Выгодоприобретатель'),('linked_contract','Связанный договор'),('status','Статус'),('days_remaining','Дней до окончания')]
    return {'name':'insurance_rows','label_ru':'Страхование','columns':[{'key':k,'label_ru':l} for k,l in columns],'rows':[row],'row_count':1,'confidence':.96,'status':'extracted','notes':'Срок и суммы извлечены из титульной таблицы страхового договора.','asset_rows':assets}


def _gps_table(document, fields):
    if not is_gps_document(document): return None
    values={x.get('name'):x.get('value') for x in fields if not isinstance(x.get('value'),list)}
    row={'provider':values.get('gps_provider'),'customer':values.get('gps_customer'),'contract_number':values.get('gps_contract_number'),'contract_date':values.get('gps_contract_date'),'start_date':values.get('gps_start_date'),'end_date':values.get('gps_end_date'),'device_quantity':values.get('gps_device_quantity'),'equipment_total_kzt':values.get('gps_equipment_total_kzt'),'annual_fee_kzt':values.get('gps_annual_fee_kzt'),'service_fee_kzt':values.get('gps_service_fee_kzt')}
    cols=[('provider','Поставщик'),('customer','Заказчик'),('contract_number','Номер договора'),('contract_date','Дата договора'),('start_date','Дата начала'),('end_date','Первоначальный срок до'),('device_quantity','Количество трекеров'),('equipment_total_kzt','Оборудование, тенге'),('annual_fee_kzt','Абонплата за год, тенге'),('service_fee_kzt','Итого GPS, тенге')]
    return {'name':'gps_rows','label_ru':'GPS и мониторинг','columns':[{'key':k,'label_ru':l} for k,l in cols],'rows':[row],'row_count':1,'confidence':.95,'status':'extracted','notes':'Поддержан шаблон Pilot-company: оборудование, количество, годовая абонплата и автоматическая пролонгация.','specification_rows':_gps_spec_rows(document)}


def _payment_table(document, fields):
    kind='insurance' if is_insurance_payment(document) else ('gps' if is_gps_payment(document) else None)
    if not kind:return None
    vals={x.get('name'):x.get('value') for x in fields if not isinstance(x.get('value'),list)}
    row={'payment_type':'Страховая премия' if kind=='insurance' else 'GPS / мониторинг','order_number':vals.get(f'{kind}_payment_order_number'),'payment_date':vals.get(f'{kind}_payment_date'),'amount_kzt':vals.get(f'{kind}_payment_amount_kzt'),'invoice_number':vals.get(f'{kind}_payment_invoice_number')}
    cols=[('payment_type','Назначение'),('order_number','№ платежного поручения'),('payment_date','Дата'),('amount_kzt','Сумма, тенге'),('invoice_number','Счёт')]
    return {'name':'insurance_gps_payment_rows','label_ru':'Оплата страхования / GPS','columns':[{'key':k,'label_ru':l} for k,l in cols],'rows':[row],'row_count':1,'confidence':.97,'status':'extracted'}


def apply_insurance_gps(document, document_type: str, fields: list[dict], tables: list[dict]):
    result_fields=extract_insurance_fields(document,fields)
    result_fields=extract_gps_fields(document,result_fields)
    result_fields=_extract_payment_fields(document,result_fields)
    result_tables=deepcopy(tables)
    for table in (_insurance_table(document,result_fields),_gps_table(document,result_fields),_payment_table(document,result_fields)):
        if table:
            result_tables=[t for t in result_tables if t.get('name')!=table['name']]; result_tables.append(table)
    return result_fields,result_tables
