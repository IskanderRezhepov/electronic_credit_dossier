from app.services.stability_guard import apply_stability_guard


def test_one_iban_keeps_most_specific_role():
    fields = [
        {
            "name": "recipient_iban",
            "value": "KZ42070F000001F00001",
            "status": "extracted",
            "confidence": 0.99,
        },
        {
            "name": "financial_agency_iban",
            "value": "KZ42070F000001F00001",
            "status": "extracted",
            "confidence": 0.95,
        },
    ]
    fixed, _ = apply_stability_guard(fields, [])
    assert any(x["name"] == "financial_agency_iban" for x in fixed)
    assert not any(x["name"] == "recipient_iban" for x in fixed)


def test_table_values_removed_from_candidates():
    fields = [{
        "name": "iin_bin_candidates",
        "value": ["121040012832", "970840000277"],
        "status": "candidate",
        "confidence": 0.55,
    }]
    tables = [{
        "name": "guarantor_rows",
        "rows": [{"iin_bin": "121040012832", "guarantee_number": "OPK/2025/W/P/02145"}],
    }]
    fixed, _ = apply_stability_guard(fields, tables)
    candidates = next(x for x in fixed if x["name"] == "iin_bin_candidates")
    assert candidates["value"] == ["970840000277"]


def test_vat_is_not_equipment_amount():
    fields = [{
        "name": "purchase_total_kzt",
        "value": 136213506,
        "status": "extracted",
    }]
    tables = [{
        "name": "asset_vin_rows",
        "rows": [{
            "equipment_type": "р/с / Наименование",
            "total_amount_kzt": 12,
        }],
        "row_count": 1,
        "status": "extracted",
    }]
    _, fixed_tables = apply_stability_guard(fields, tables)
    table = fixed_tables[0]
    assert table["rows"] == []
    assert table["status"] == "candidate"
