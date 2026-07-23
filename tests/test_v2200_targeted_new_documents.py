from app.services.document_reader import PageContent, ReadDocument
from app.services.safe_regression_fixes import first_page_type, postprocess_fields, postprocess_tables


def doc(pages):
    return ReadDocument("x.pdf", len(pages), "pdf", [
        PageContent(i+1, text, "digital", len(text), 0.99) for i, text in enumerate(pages)
    ])


def test_credit_line_borrower_is_not_damu():
    d = doc([
        'СОГЛАШЕНИЕ ОБ ОТКРЫТИИ КЛ № AOP/2026/I/S/008687. '
        'Индивидуальный предприниматель "KBK BETON", ИИН 030412650123, далее Заемщик. '
        'Фонд Даму БИН 970840000277.'
    ])
    fields, _ = postprocess_fields(d, "credit_line_agreement", [], [])
    data = {x["name"]: x["value"] for x in fields}
    assert data["borrower_name"] == 'ИП «KBK BETON»'
    assert data["borrower_iin_bin"] == '030412650123'
    assert data["fund_iin_bin"] == '970840000277'
    assert "principal_iin_bin" not in data


def test_changes_and_additions_becomes_addendum():
    d = doc([
        'Изменения и дополнения № 1 к Заявлению о присоединении (Договор лизинга) '
        '№ UOP/2026/1/S/008153 от 29.06.2026. Индивидуальный предприниматель «РАСУЛ», '
        'ИИН 810412402091. Комиссия 142 800,00 тенге.',
        'Лизингодатель БИН 020140001503 ИИК KZ298562203134304780. '
        'Лизингополучатель ИИК KZ078562204146574866.'
    ])
    assert first_page_type(d, "lease_contract") == "addendum"
    fields, _ = postprocess_fields(d, "addendum", [], [])
    data = {x["name"]: x["value"] for x in fields}
    assert data["addendum_number"] == "1"
    assert data["linked_lease_contract_number"] == "UOP/2026/I/S/008153"
    assert data["lessee_name"] == 'ИП «РАСУЛ»'
    assert data["lessee_iban"] == "KZ078562204146574866"
    assert data["changed_commission_kzt"] == 142800.0


def test_xcmg_equipment_from_lease_specification():
    d = doc([
        'Заявление о присоединении Договор лизинга. Стоимость Предмета лизинга 27 600 000,00 тенге.',
        'Приложение №1. Каток XCMG XS163J. Идентификатор XUG01633HTJE02245. '
        'Рабочая масса 16 000 кг. Двигатель Shangchai SC4H140.1G2. '
        'Мощность 103 кВт. Количество 1.'
    ])
    fields = [{"name":"lease_asset_value_kzt", "value":27600000.0, "status":"extracted"}]
    tables = postprocess_tables(d, "lease_contract", fields, [], 27600000.0)
    row = next(t for t in tables if t["name"] == "asset_vin_rows")["rows"][0]
    assert row["equipment_type"] == "Каток"
    assert row["model"] == "XCMG XS163J"
    assert row["vin"] == "XUG01633HTJE02245"
    assert row["engine_model"].lower().startswith("shangchai")
    assert row["working_weight_kg"] == 16000
    assert row["power_kw"] == 103
    assert row["total_amount_kzt"] == 27600000.0
