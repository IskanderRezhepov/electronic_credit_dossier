from app.services.document_reader import PageContent, ReadDocument
from app.services.safe_regression_fixes import (
    first_page_type,
    postprocess_fields,
    postprocess_tables,
)
from app.services.validators import validate_iban


def make_doc(pages):
    return ReadDocument(
        "x.pdf", len(pages), "pdf",
        [PageContent(i + 1, text, "digital", len(text), 0.99)
         for i, text in enumerate(pages)],
    )


def test_doc_id_fragment_is_not_iban():
    result = validate_iban("KZPKXV42026000132828")
    assert result["valid"] is False


def test_addendum_prefers_signature_date():
    doc = make_doc([
        "Дополнительное соглашение №1 к договору от 28.12.2023",
        "Дата подписания: 05.01.2024",
    ])
    fields, _ = postprocess_fields(doc, "addendum", [], [])
    date = next(x for x in fields if x["name"] == "addendum_date")
    assert date["value"] == "05.01.2024"


def test_scanned_addendum_terms_restored():
    doc = make_doc([
        "Дополнительное соглашение №1 03.04.2025",
        "Лизингодатель БИН 020140001503 ИИК KZ678562203116347262 "
        "Лизингополучатель БИН 130940024372 ИИК KZ458562203120977177",
        "Сумма транша 100 181 625,00 Дата выдачи 07.12.2023",
        "Общая ставка 21% субсидируемая 13,75% часть лизингополучателя 7,25%",
    ])
    fields, _ = postprocess_fields(doc, "addendum", [], [])
    values = {x["name"]: x["value"] for x in fields}
    assert values["addendum_date"] == "03.04.2025"
    assert values["tranche_amount_kzt"] == 100181625
    assert values["tranche_date"] == "07.12.2023"
    assert values["subsidized_rate_percent"] == 13.75
    assert values["lessee_iban"] == "KZ458562203120977177"


def test_volvo_header_row_is_replaced():
    doc = make_doc([
        "ДОГОВОР КУПЛИ-ПРОДАЖИ № KAZ14112024KTL 14.11.2024 "
        "Общая стоимость 136 213 506",
        "Спецификация Седельный тягач Volvo FH 4x2 "
        "Год выпуска 2024 Количество 2 единицы",
    ])
    fields = [{"name": "purchase_total_kzt", "value": 136213506, "status": "extracted"}]
    bad = [{
        "name": "asset_vin_rows",
        "rows": [{
            "equipment_type": "р/с / Наименование",
            "total_amount_kzt": 12,
        }],
        "row_count": 1,
    }]
    tables = postprocess_tables(doc, "purchase_contract", fields, bad, None)
    row = next(t for t in tables if t["name"] == "asset_vin_rows")["rows"][0]
    assert row["model"] == "VOLVO FH 4X2"
    assert row["quantity"] == 2
    assert row["unit_price_kzt"] == 68106753
    assert row["total_amount_kzt"] == 136213506


def test_howo_flattened_specification():
    doc = make_doc([
        "Договор купли-продажи Общая стоимость 35 750 000",
        "Спецификация Самосвал HOWO T5G Год выпуска 2025 Количество 1",
    ])
    fields = [{"name": "purchase_total_kzt", "value": 35750000, "status": "extracted"}]
    tables = postprocess_tables(doc, "purchase_contract", fields, [], None)
    row = next(t for t in tables if t["name"] == "asset_vin_rows")["rows"][0]
    assert row["model"] == "HOWO T5G"
    assert row["quantity"] == 1
    assert row["total_amount_kzt"] == 35750000
