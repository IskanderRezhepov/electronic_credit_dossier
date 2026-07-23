from dataclasses import dataclass

from app.services.insurance_gps import apply_insurance_gps


@dataclass
class Page:
    page_number: int
    text: str
    extraction_method: str = "digital"
    quality: float = 1.0


@dataclass
class Document:
    pages: list
    filename: str = "document.pdf"
    used_ocr: bool = False

    @property
    def full_text(self):
        return "\n".join(page.text for page in self.pages)


def test_insurance_policy_fields_and_table():
    text = (
        "СТРАХОВОЙ ПОЛИС КАСКО № KASKO/2026/001\n"
        "Страховщик: АО «Надежная страховая компания»\n"
        "Период страхования с 01.08.2026 по 31.07.2027\n"
        "Страховая сумма: 27 600 000,00 тенге\n"
        "Страховая премия: 550 000,00 тенге\n"
        "Выгодоприобретатель: АО «BCC Leasing»\n"
        "VIN XUG01633HTJE02245"
    )
    document = Document([Page(1, text)], filename="полис КАСКО.pdf")
    fields, tables = apply_insurance_gps(document, "insurance_contract", [], [])
    values = {item["name"]: item["value"] for item in fields}
    assert values["insurance_type"] == "КАСКО"
    assert values["insurance_policy_number"] == "KASKO/2026/001"
    assert values["insurance_start_date"] == "01.08.2026"
    assert values["insurance_end_date"] == "31.07.2027"
    assert values["insurance_sum_kzt"] == 27600000.0
    table = next(item for item in tables if item["name"] == "insurance_rows")
    assert "XUG01633HTJE02245" in table["rows"][0]["vin"]


def test_gps_contract_fields_and_table():
    text = (
        "ДОГОВОР GPS МОНИТОРИНГА № GPS/2026/77\n"
        "Исполнитель: ТОО «Навигация KZ»\n"
        "Дата начала: 01.09.2026\n"
        "Дата окончания: 31.08.2027\n"
        "Абонентская плата: 120 000,00 тенге\n"
        "VIN MXC68B110TK257831"
    )
    document = Document([Page(1, text)], filename="GPS.pdf")
    fields, tables = apply_insurance_gps(document, "gps_service_contract", [], [])
    values = {item["name"]: item["value"] for item in fields}
    assert values["gps_contract_number"] == "GPS/2026/77"
    assert values["gps_service_fee_kzt"] == 120000.0
    assert any(item["name"] == "gps_rows" for item in tables)
