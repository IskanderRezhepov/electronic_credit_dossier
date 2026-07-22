from app.services.financial_reconciliation import build_financial_checks
from app.services.table_review import _row_checks


def test_equipment_total_matches_contract_amount():
    documents = [{
        "filename": "purchase.pdf",
        "document_type": "purchase_contract",
        "document_type_label_ru": "Договор купли-продажи",
        "fields": [{
            "name": "purchase_total_kzt",
            "label_ru": "Сумма договора купли-продажи",
            "value": 90000000,
            "status": "confirmed",
        }],
        "tables": [{
            "name": "asset_vin_rows",
            "status": "confirmed",
            "rows": [{
                "equipment_type": "Самосвал",
                "quantity": 2,
                "unit_price_kzt": 45000000,
                "total_amount_kzt": 90000000,
                "page": 2,
            }],
        }],
    }]
    checks, summary = build_financial_checks(documents)
    assert any(check["check"].startswith("Спецификация") and check["status"] == "match" for check in checks)
    assert summary["mismatch"] == 0


def test_equipment_total_mismatch_is_critical():
    documents = [{
        "filename": "purchase.pdf",
        "document_type": "purchase_contract",
        "document_type_label_ru": "Договор купли-продажи",
        "fields": [{
            "name": "purchase_total_kzt",
            "label_ru": "Сумма договора купли-продажи",
            "value": 91000000,
            "status": "confirmed",
        }],
        "tables": [{
            "name": "asset_vin_rows",
            "rows": [{"quantity": 2, "unit_price_kzt": 45000000, "total_amount_kzt": 90000000}],
        }],
    }]
    checks, summary = build_financial_checks(documents)
    mismatch = next(check for check in checks if check["check"].startswith("Спецификация") and check["status"] == "mismatch")
    assert mismatch["severity"] == "critical"
    assert summary["largest_difference_kzt"] == 1000000


def test_schedule_balance_chain_check():
    checks = _row_checks("payment_schedule_rows", [
        {"principal": 100, "interest": 10, "payment": 110, "balance": 900},
        {"principal": 100, "interest": 9, "payment": 109, "balance": 800},
        {"principal": 100, "interest": 8, "payment": 108, "balance": 705},
    ])
    balance_checks = [item for item in checks if item["check"].startswith("Остаток")]
    assert len(balance_checks) == 2
    assert balance_checks[0]["valid"] is True
    assert balance_checks[1]["valid"] is False
