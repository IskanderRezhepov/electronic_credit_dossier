from types import SimpleNamespace

from app.services.insurance_gps import apply_insurance_gps
from app.services.client_registry import identify_client
from app.services.safe_regression_fixes import apply_safe_regression_fixes


class Page:
    def __init__(self, number, text):
        self.page_number = number
        self.text = text
        self.extraction_method = "digital"


def doc(text):
    return SimpleNamespace(full_text=text, pages=[Page(1, text)], filename="sample.pdf")


def values(fields):
    return {x.get("name"): x.get("value") for x in fields}


def test_kbk_beton_full_name_is_locked():
    d = doc('Индивидуальный предприниматель "KBK\nBETON", ИИН 030412650123, далее Заемщик. СОГЛАШЕНИЕ ОБ ОТКРЫТИИ КЛ')
    fields, tables = apply_safe_regression_fixes(d, "credit_line", [], [])
    assert values(fields)["borrower_name"] == "ИП «KBK BETON»"


def test_freedom_requisites_and_linked_contracts_exported():
    d = doc('ПР-41148 ДП-26-301-0001358 ТОО Техностандарт-М 020640003099')
    fields, tables = apply_insurance_gps(d, "insurance_contract", [], [])
    v = values(fields)
    assert v["beneficiary_iin_bin"] == "980640000093"
    assert v["insurance_company_iin_bin"] == "090640006849"
    assert v["insurance_company_iban"] == "KZ75551A125000184KZT"
    table = next(x for x in tables if x["name"] == "insurance_rows")
    assert "AM2/2026/U/S/039531/1" in table["rows"][0]["linked_contract"]


def test_gps_client_role_is_customer():
    documents=[{"filename":"gps.pdf","fields":[
        {"name":"gps_customer_iin_bin","value":"020640003099","status":"extracted","label_ru":"БИН заказчика"},
        {"name":"gps_customer","value":"ТОО «Техностандарт-М»","status":"extracted","label_ru":"Заказчик"},
        {"name":"recipient_iin_bin","value":"020640003099","status":"extracted","label_ru":"Получатель"},
    ]}]
    client=identify_client(documents)
    assert client["role_label_ru"] == "Заказчик"
    assert client["name"] == "ТОО «Техностандарт-М»"
