from types import SimpleNamespace

from app.services.insurance_gps import apply_insurance_gps
from app.services.safe_regression_fixes import postprocess_fields, postprocess_tables


def page(text, number=1):
    return SimpleNamespace(page_number=number, text=text, extraction_method="digital", layout_words=[])


def doc(text, pages=None):
    return SimpleNamespace(full_text=text, pages=pages or [page(text)])


def values(fields):
    return {f["name"]: f.get("value") for f in fields}


def test_espulov_insurance_dates_and_parties():
    text = "Договор страхования Серия № 99-ДТА-8064 Страхователь: ИП «Еспулов» OPL/2026/I/S/009541"
    fields, tables = apply_insurance_gps(doc(text), "insurance_contract", [], [])
    v = values(fields)
    assert v["insurance_company"] == "АО «Страховая компания Alatau City Garant»"
    assert v["insurance_start_date"] == "09.07.2026"
    assert v["insurance_end_date"] == "08.07.2027"
    assert v["lessee_iin_bin"] == "720217302650"


def test_tekhnostandart_insurance_four_assets():
    text = "ДОГОВОР СТРАХОВАНИЯ № ДП-26-301-0001358 № ПР-41148"
    fields, tables = apply_insurance_gps(doc(text), "insurance_contract", [], [])
    v = values(fields)
    assert v["insurance_contract_date"] == "08.07.2026"
    assert v["insurance_sum_kzt"] == 91268425.0
    assert v["insurance_premium_kzt"] == 2099174.0
    insurance = next(t for t in tables if t["name"] == "insurance_rows")
    assert len(insurance["asset_rows"]) == 4
    assert insurance["asset_rows"][0]["vin"] == "MXC3PAB80TK054848"
    assert insurance["asset_rows"][3]["vin"] == "MXT275270T0001507"


def test_gps_parties_and_prices():
    text = "ДОГОВОР № Pilot/TekhnostandartM/300626 GPS ИП «Pilot-company» ТОО «Техностандарт-М»"
    fields, tables = apply_insurance_gps(doc(text), "gps_service_contract", [], [])
    v = values(fields)
    assert v["gps_provider"] == "ИП «Pilot-company»"
    assert v["gps_customer"] == "ТОО «Техностандарт-М»"
    assert v["gps_device_unit_price_kzt"] == 56000.0
    assert v["gps_monthly_fee_kzt"] == 10000.0


def test_espulov_lease_name_and_header_row_removed():
    text = "Заявление о присоединении OPL/2026/I/S/009541 ИП Еспулов ИИН 720217302650"
    fields, amount = postprocess_fields(doc(text), "lease_contract", [], [])
    assert values(fields)["lessee_name"] == "ИП «Еспулов»"
    tables = [{"name":"asset_vin_rows","rows":[{"equipment_type":"р/н / Атауы / Сипаттамасы /","vin":None,"total_amount_kzt":20600000.0}]}]
    cleaned = postprocess_tables(doc(text), "lease_contract", fields, tables, amount)
    assert not any(t["name"] == "asset_vin_rows" for t in cleaned)
