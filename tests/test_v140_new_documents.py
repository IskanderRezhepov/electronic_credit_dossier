from app.parsers.specific import parse_by_type
from app.services.classifier import classify
from app.services.document_reader import PageContent, ReadDocument


def doc(text: str, name: str = 'sample.pdf') -> ReadDocument:
    return ReadDocument(name, 1, 'pdf', [PageContent(1, text, 'digital', len(text), 0.99, {'digital': text})])


def as_map(fields):
    return {item['name']: item['value'] for item in fields}


def test_credit_line_extracts_purpose_availability_and_collateral():
    text = '''СОГЛАШЕНИЕ ОБ ОТКРЫТИИ КЛ №OPK/2025/U/S/016844 к Договору присоединения № 002
    ТОО «КАЗФИТИНГПЛАСТ», БИН 130340002716, далее Заемщик.
    Цель КЛ: Пополнение оборотных средств.
    Сумма КЛ: 500 000 000,00 тенге. Срок КЛ: 36 (Тридцать шесть) месяцев.
    Период доступности КЛ: по 03.09.2026.
    Сумма гарантии Фонда составляет 300 000 000,00 тенге.
    кадастровый номер: 09:144:005:322 и 09:142:009:323.'''
    c = classify(text)
    assert c.key == 'credit_line_agreement'
    fields = as_map(parse_by_type(doc(text), c.key))
    assert fields['credit_line_number'] == 'OPK/2025/U/S/016844'
    assert fields['credit_line_purpose'].startswith('Пополнение оборотных средств')
    assert fields['availability_end_date'] == '03.09.2026'
    assert set(fields['collateral_cadastral_numbers']) == {'09:144:005:322', '09:142:009:323'}


def test_cash_pledge_extracts_pledgor_and_amount():
    text = '''ДОГОВОР ЗАЛОГА ДЕНЕГ НА СЧЕТЕ № OPK/2026/W/P/00309
    Товарищество с ограниченной ответственностью «КАЗФИТИНГПЛАСТ», далее Залогодатель, БИН 130340002716.
    Залогодатель передает деньги во Вкладе в сумме 5 000 000,00 тенге.
    Счет KZ578562223152724575.'''
    fields = as_map(parse_by_type(doc(text), 'cash_pledge_agreement'))
    assert fields['pledge_contract_number'] == 'OPK/2026/W/P/00309'
    assert fields['pledgor_bin'] == '130340002716'
    assert fields['pledgor_name'] == 'КАЗФИТИНГПЛАСТ'
    assert float(fields['pledge_amount_kzt']) == 5000000.0
    assert fields['deposit_iban'] == 'KZ578562223152724575'


def test_subsidy_extracts_recipient_bin():
    text = '''Договор субсидирования части ставки вознаграждения № OPU/2023/U/S/018097/0001L
    Товарищество с ограниченной ответственностью «Арлан Сауда», именуемое в дальнейшем «Получатель».
    Получатель БИН 120340012345. Сумма лизинга 39 351 350,00.'''
    fields = as_map(parse_by_type(doc(text), 'subsidy_agreement'))
    assert fields['recipient_name'] == 'Арлан Сауда'
    assert fields['recipient_bin'] == '120340012345'
