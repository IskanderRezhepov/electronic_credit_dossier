from types import SimpleNamespace

from app.services.insurance_gps import apply_insurance_gps
from app.services.safe_regression_fixes import postprocess_tables


def _doc(text):
    page = SimpleNamespace(page_number=1, text=text, extraction_method='digital', layout_words=[])
    return SimpleNamespace(full_text=text, pages=[page])


def _values(fields):
    return {x['name']: x.get('value') for x in fields if not isinstance(x.get('value'), list)}


def test_espulov_gps_targeted_fields():
    text = 'ДОГОВОР № Pilot/ESPULOV/090726 09 июля 2026 ИП «Pilot-company» Поставщик ИП «ЕСПУЛОВ» Заказчик GPS 56 000 1 56 000'
    fields, tables = apply_insurance_gps(_doc(text), 'gps_contract', [], [])
    v = _values(fields)
    assert v['gps_contract_number'] == 'PILOT/ESPULOV/090726'
    assert v['gps_contract_date'] == '09.07.2026'
    assert v['gps_device_unit_price_kzt'] == 56000.0
    assert v['gps_monthly_fee_kzt'] == 2500.0
    assert v['recipient_iin_bin'] == '720217302650'


def test_insurance_payment_client_and_invoice():
    text = 'Платежное поручение № 3654 от 03.07.2026 страховая премия Sinoasia B&R 900 201 счету № 5544360'
    fields, tables = apply_insurance_gps(_doc(text), 'payment_order', [], [])
    v = _values(fields)
    assert v['recipient_iin_bin'] == '161040015339'
    assert v['insurance_payment_invoice_number'] == '5544360'


def test_atm_wingle_rows():
    text = 'OPA/2026/U/S/037562 ТОО АгроТехМенеджмент БИН 161040015339 WINGLE 7 2026 VIN MX2K01PGLTB011386 MX2K01PGLTB011485 MX2K01PGLTB011408 37 508 400'
    doc = _doc(text)
    tables = postprocess_tables(doc, 'lease_contract', [], [], 37508400.0)
    table = next(t for t in tables if t['name'] == 'asset_vin_rows')
    assert len(table['rows']) == 3
    assert all(r['model'] == 'WINGLE 7' for r in table['rows'])
    assert all(r['manufacture_year'] == 2026 for r in table['rows'])
    assert sum(r['total_amount_kzt'] for r in table['rows']) == 37508400.0
