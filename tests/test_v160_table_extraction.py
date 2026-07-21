from app.services.document_reader import PageContent, ReadDocument
from app.services.table_extractor import extract_tables


def make_doc(text: str, filename: str = 'test.pdf', method: str = 'digital') -> ReadDocument:
    page = PageContent(page_number=1, text=text, extraction_method=method, char_count=len(text), quality=0.96)
    return ReadDocument(filename=filename, page_count=1, source_type='pdf', pages=[page])


def test_extracts_payment_schedule_rows_conservatively():
    doc = make_doc('''ГРАФИК ПОГАШЕНИЯ\nДата Основной долг Вознаграждение Платеж Остаток\n02.11.2022 488 553,00 120 000,00 608 553,00 17 587 911,00\n02.12.2022 488 553,00 115 000,00 603 553,00 17 099 358,00''')
    tables = extract_tables(doc, 'payment_schedule')
    assert tables and tables[0]['name'] == 'payment_schedule_rows'
    assert tables[0]['row_count'] == 2
    assert tables[0]['rows'][0]['date'] == '02.11.2022'
    assert tables[0]['rows'][0]['balance'] == 17587911.0


def test_extracts_unique_vins_from_asset_document():
    doc = make_doc('''СПЕЦИФИКАЦИЯ\nHyundai EX9 VIN KMFHA17HPNC063237 стоимость 23 349 000,00\nHyundai EX9 VIN KMFHA17HPNC063238 стоимость 23 349 000,00''')
    tables = extract_tables(doc, 'acceptance_act')
    assert tables and tables[0]['name'] == 'asset_vin_rows'
    assert tables[0]['summary']['unique_vin_count'] == 2
    assert {row['vin'] for row in tables[0]['rows']} == {'KMFHA17HPNC063237', 'KMFHA17HPNC063238'}


def test_does_not_create_schedule_without_schedule_context():
    doc = make_doc('Договор от 02.11.2022 на сумму 1 000 000,00 тенге')
    assert extract_tables(doc, 'unknown') == []
