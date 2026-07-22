
from app.parsers.specific import parse_by_type
from app.services.classifier import classify
from app.services.document_reader import PageContent, ReadDocument


def doc(text, filename="x.pdf", method="digital"):
    return ReadDocument(filename, 1, "pdf", [PageContent(1, text, method, len(text), 0.98, {method: text})])


def values(fields):
    return {item["name"]: item["value"] for item in fields}


def test_credit_line_classification_and_fields():
    text = """СОГЛАШЕНИЕ ОБ ОТКРЫТИИ КЛ №OPK/2025/U/S/016844 к Договору присоединения № 002
Дата подписания: «03» сентября 2025 года
ТОО «КАЗФИТИНГПЛАСТ», БИН 130340002716 далее Заемщик
2. Сумма КЛ: 500 000 000,00 тенге. 4. Срок КЛ: 36 (Тридцать шесть) месяцев.
фиксированное вознаграждение из расчета 12,6% годовых, ГЭСВ составляет 16,5%.
Сумма гарантии Фонда составляет 300 000 000,00 тенге."""
    c=classify(text)
    assert c.key == "credit_line_agreement"
    out=values(parse_by_type(doc(text), c.key))
    assert out["credit_line_number"] == "OPK/2025/U/S/016844"
    assert out["credit_line_amount_kzt"] == "500000000.00"
    assert out["credit_line_term_months"] == 36


def test_cash_pledge_classification_and_fields():
    text = """ДОГОВОР ЗАЛОГА ДЕНЕГ НА СЧЕТЕ № OPK/2026/W/P/00309
ТОО «КАЗФИТИНГПЛАСТ», БИН 130340002716, далее Залогодатель.
в сумме 5 000 000,00 тенге, размещенные на банковском счете KZ578562223152724575, далее депозитный счет.
сумма банковского займа 500 000 000,00 тенге"""
    c=classify(text)
    assert c.key == "cash_pledge_agreement"
    out=values(parse_by_type(doc(text), c.key))
    assert out["pledge_contract_number"] == "OPK/2026/W/P/00309"
    assert out["pledge_amount_kzt"] == "5000000.00"
    assert out["deposit_iban"] == "KZ578562223152724575"


def test_subsidy_classification_and_fields():
    text = """Договор субсидирования части ставки вознаграждения № OPU/2023/U/S/018097/0001L
финансовое агентство и Товарищество с ограниченной ответственностью «Арлан Сауда» именуемый в дальнейшем «Получатель».
договор финансового лизинга № OPU/2023/U/S/018097 от 07 апреля 2023 года
Сумма кредита/микрокредита/лизинга на дату начала срока субсидирования 39 351 350,00
Ставка вознаграждения 21,75 процентов годовых.
часть ставки вознаграждения в размере 12,75 процентов оплачивает финансовое агентство, а остальную часть ставки вознаграждения в размере 9 процентов оплачивает Получатель."""
    c=classify(text)
    assert c.key == "subsidy_agreement"
    out=values(parse_by_type(doc(text), c.key))
    assert out["subsidy_contract_number"] == "OPU/2023/U/S/018097/0001L"
    assert out["financing_amount_kzt"] == "39351350.00"
    assert out["nominal_rate_percent"] == 21.75
    assert out["subsidized_rate_percent"] == 12.75
