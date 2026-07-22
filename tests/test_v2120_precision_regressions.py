from app.services.precision_postprocess import _repair_contract_number
from app.services.equipment_precision import improve_equipment_tables
from app.services.document_reader import PageContent, ReadDocument


def test_repairs_bcc_contract_number_ocr():
    assert _repair_contract_number("А0О5/2023/0/8/221974") == "AQ5/2023/U/S/221974"


def test_single_vin_uses_explicit_contract_value_when_split_money_is_wrong():
    doc = ReadDocument(
        "x.pdf", 1, "pdf",
        [PageContent(
            1,
            "JAC Sunray 6C VIN MXC68B110TK257831 стоимость 15 900 000,00",
            "digital", 70, 0.99,
        )],
    )
    fields = [{
        "name": "lease_asset_value_kzt",
        "value": 15900000,
        "status": "extracted",
    }]
    tables = [{
        "name": "asset_vin_rows",
        "rows": [{
            "vin": "MXC68B110TK257831",
            "quantity": 1,
            "unit_price_kzt": 900000,
            "total_amount_kzt": 900000,
            "equipment_type": "VIN MXC68B110TK257831",
        }],
    }]
    improved = improve_equipment_tables(doc, fields, tables)
    row = improved[0]["rows"][0]
    assert row["total_amount_kzt"] == 15900000
    assert row["unit_price_kzt"] == 15900000
    assert "JAC" in row["model"]


def test_year_not_taken_from_contract_date_without_label():
    doc = ReadDocument(
        "x.pdf", 1, "pdf",
        [PageContent(
            1,
            "29.06.2026 JAC Sunray 6C VIN MXC68B110TK257831",
            "digital", 55, 0.99,
        )],
    )
    tables = [{
        "name": "asset_vin_rows",
        "rows": [{"vin": "MXC68B110TK257831", "quantity": 1, "manufacture_year": "2026"}],
    }]
    improved = improve_equipment_tables(doc, [], tables)
    assert improved[0]["rows"][0]["manufacture_year"] is None
