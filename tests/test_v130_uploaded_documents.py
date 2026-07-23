from app.parsers.specific import parse_by_type
from app.services.classifier import classify
from app.services.document_reader import PageContent, ReadDocument


def doc(text, filename="x.pdf", method="digital"):
    return ReadDocument(filename, 1, "pdf", [PageContent(1, text, method, len(text), 0.98, {method: text})])


def values(fields):
    return {item["name"]: item["value"] for item in fields}


def test_guarantee_contract_fields():
    text = """ДОГОВОР ГАРАНТИИ № AQ5/2023/W/P/03125
    ТОО «KazPromService», БИН 130940024372, далее Лизингополучатель.
    гражданка Нурумова Виктория Юрьевна, ИИН 731102400045, далее Гарант.
    по Договору финансового лизинга № AQ5/2023/U/S/221974 от 05.12.2023.
    основной долг в размере 100 222 500,00 тенге на 42 месяца."""
    c = classify(text)
    assert c.key == "guarantee_contract"
    out = values(parse_by_type(doc(text), c.key))
    assert out["guarantee_contract_number"] == "AQ5/2023/W/P/03125"
    assert out["linked_lease_contract_number"] == "AQ5/2023/U/S/221974"
    assert out["guarantor_iin"] == "731102400045"
    assert out["guarantee_amount_kzt"] == "100222500.00"


def test_bank_guarantee_application_fields():
    text = """ЗАЯВЛЕНИЕ № OPI/2025/U/G/005305 о присоединении к Договору присоединения
    ТОО «Тонар-Кокше» далее Принципал. БИН Принципала 991123450235.
    1. Вид Гарантии: платежная гарантия.
    2. Сумма Гарантии: 10 123 500,83 тенге.
    Наименование, БИН Бенефициара: РГП, БИН 080740017519.
    6. Сумма комиссионного вознаграждения: 708 645,0581."""
    c = classify(text)
    assert c.key == "bank_guarantee_application"
    out = values(parse_by_type(doc(text), c.key))
    assert out["guarantee_application_number"] == "OPI/2025/U/G/005305"
    assert out["guarantee_amount_kzt"] == "10123500.83"
    assert out["beneficiary_bin"] == "080740017519"


def test_subsidy_addendum_links_original_contract():
    text = """Дополнительное соглашение №1 к Договору субсидирования
    №AQ5/2023/U/S/221974/0001L от 28.12.2023г.
    Остальные положения Договора остаются неизменными."""
    c = classify(text)
    assert c.key == "addendum"
    out = values(parse_by_type(doc(text), c.key))
    assert out["addendum_number"] == "1"
    assert out["linked_subsidy_contract_number"] == "AQ5/2023/U/S/221974/0001L"
