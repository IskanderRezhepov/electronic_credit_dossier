from app.services.document_reader import PageContent, ReadDocument
from app.services.safe_regression_fixes import postprocess_fields, postprocess_tables
from app.services.stability_guard import apply_stability_guard


def doc(pages):
    return ReadDocument(
        "x.pdf", len(pages), "pdf",
        [PageContent(i + 1, text, "digital", len(text), 0.99)
         for i, text in enumerate(pages)],
    )


def test_kbk_borrower_is_locked_to_opening_block():
    d = doc([
        'Индивидуальный предприниматель "KBK BETON", ИИН 030412650123 '
        'далее Заемщик. Сумма КЛ 50 000 000. Фонд Даму.',
        'Фонд Даму БИН 970840000277.',
    ])
    fields = [
        {"name": "borrower_name", "value": "құрамында)", "status": "extracted", "confidence": .99},
        {"name": "borrower_iin_bin", "value": "970840000277", "status": "extracted", "confidence": .99},
    ]
    fixed, _ = postprocess_fields(d, "credit_line_agreement", fields, [])
    fixed, _ = apply_stability_guard(fixed, [])
    data = {x["name"]: x["value"] for x in fixed}
    assert data["borrower_name"] == 'ИП «KBK BETON»'
    assert data["borrower_iin_bin"] == "030412650123"
    assert data["fund_iin_bin"] == "970840000277"


def test_borrower_is_not_added_to_guarantor_table():
    d = doc([
        'ИП "KBK BETON" ИИН 030412650123 далее Заемщик.',
        'ТОО «КаспийБизнесКонсалтинг» БИН 050440000062 '
        'гарантия №AOP/2026/W/P/01151 от 26.06.2026. '
        'Кубенов Бауыржан Карбанович ИИН 720105301059 '
        'гарантия №AOP/2026/W/P/01152 от 26.06.2026.',
    ])
    tables = postprocess_tables(d, "credit_line_agreement", [], [], None)
    table = next(t for t in tables if t["name"] == "guarantor_rows")
    ids = {row["iin_bin"] for row in table["rows"]}
    assert "030412650123" not in ids
    assert "050440000062" in ids
    assert "720105301059" in ids


def test_sanzhar_name_and_xcmg_details():
    d = doc([
        'ТОО «Санж-ар» БИН 140740024684 договор лизинга. '
        'Стоимость 27 600 000.',
        'Спецификация Каток XCMG XS163J. Идентификатор XUG01633HTJE02245. '
        'Рабочая масса 16 000 кг. Двигатель Shangchai SC4H140.1G2. '
        'Мощность 103 кВт. Количество 1.',
    ])
    fields = [{"name": "lease_asset_value_kzt", "value": 27600000, "status": "extracted"}]
    fixed, amount = postprocess_fields(d, "lease_contract", fields, [])
    assert any(x["name"] == "lessee_name" and x["value"] == 'ТОО «Санж-ар»' for x in fixed)
    tables = postprocess_tables(d, "lease_contract", fixed, [], 27600000)
    row = next(t for t in tables if t["name"] == "asset_vin_rows")["rows"][0]
    assert row["equipment_identifier"] == "XUG01633HTJE02245"
    assert row["working_weight_kg"] == 16000
    assert row["power_kw"] == 103


def test_rasul_damaged_contract_candidate_removed():
    d = doc([
        'Изменения и дополнения №1 к заявлению UOP/2026/1/S/008153. '
        'ИП «РАСУЛ» ИИН 810412402091. Комиссия 142 800.',
    ])
    fields = [{
        "name": "contract_number_candidates",
        "value": ["UOP/2026/1/S/008153"],
        "status": "candidate",
        "confidence": .68,
    }]
    fixed, _ = postprocess_fields(d, "addendum", fields, [])
    assert not any(
        isinstance(x.get("value"), list) and "UOP/2026/1/S/008153" in x["value"]
        for x in fixed
    )
