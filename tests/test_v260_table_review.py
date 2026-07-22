from app.services.table_review import apply_table_review


class Form(dict):
    pass


def sample_result():
    return {
        "documents": [{
            "tables": [{
                "name": "asset_vin_rows",
                "label_ru": "Техника",
                "columns": [
                    {"key": "equipment_type", "label_ru": "Вид"},
                    {"key": "quantity", "label_ru": "Количество"},
                    {"key": "unit_price_kzt", "label_ru": "Цена"},
                    {"key": "total_amount_kzt", "label_ru": "Сумма"},
                    {"key": "vin", "label_ru": "VIN"},
                ],
                "rows": [{
                    "equipment_type": "Самосвал", "quantity": 2,
                    "unit_price_kzt": 10.0, "total_amount_kzt": 20.0,
                    "vin": "LZGJL4V44PX123456",
                }],
                "status": "candidate",
            }],
        }],
    }


def test_edits_and_recalculates_equipment_row():
    form = Form({
        "table_0_0_row_0_equipment_type": "Самосвал",
        "table_0_0_row_0_quantity": "3",
        "table_0_0_row_0_unit_price_kzt": "10",
        "table_0_0_row_0_total_amount_kzt": "30",
        "table_0_0_row_0_vin": "LZGJL4V44PX123456",
        "table_0_0_new_count": "0",
        "table_0_0_status": "confirmed",
    })
    updated, changed = apply_table_review(sample_result(), form, "2026-07-22T00:00:00Z")
    table = updated["documents"][0]["tables"][0]
    assert changed >= 2
    assert table["rows"][0]["quantity"] == 3
    assert table["summary"]["total_quantity"] == 3
    assert table["row_checks"][0]["valid"] is True
    assert table["status"] == "confirmed"


def test_adds_and_deletes_rows():
    form = Form({
        "table_0_0_row_0_delete": "on",
        "table_0_0_new_count": "1",
        "table_0_0_new_0_equipment_type": "Погрузчик",
        "table_0_0_new_0_quantity": "1",
        "table_0_0_new_0_unit_price_kzt": "15000000",
        "table_0_0_new_0_total_amount_kzt": "15000000",
        "table_0_0_new_0_vin": "LZ0CC4W31P0123456",
        "table_0_0_status": "corrected",
    })
    updated, changed = apply_table_review(sample_result(), form, "2026-07-22T00:00:00Z")
    table = updated["documents"][0]["tables"][0]
    assert changed >= 3
    assert len(table["rows"]) == 1
    assert table["rows"][0]["equipment_type"] == "Погрузчик"
    assert table["rows"][0]["manual"] is True
    assert len(table["deleted_rows"]) == 1
