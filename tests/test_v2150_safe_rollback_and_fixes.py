from app.services.document_reader import PageContent, ReadDocument
from app.services.safe_regression_fixes import (
    first_page_type, postprocess_fields, postprocess_tables,
)


def doc(text):
    return ReadDocument("x.pdf", 1, "pdf", [
        PageContent(1, text, "digital", len(text), 0.99)
    ])


def test_guibao_stays_lease():
    d = doc(
        "ЗАЯВЛЕНИЕ О ПРИСОЕДИНЕНИИ к договору финансового лизинга "
        "№ AP4/2026/U/S/056150"
    )
    assert first_page_type(d, "purchase_contract") == "lease_contract"


def test_ktl_is_purchase_contract():
    d = doc(
        "ДОГОВОР КУПЛИ-ПРОДАЖИ № KAZ14112024KTL от 14.11.2024"
    )
    assert first_page_type(d, "addendum") == "purchase_contract"


def test_direct_debit_sender_account():
    d = doc(
        "СОГЛАШЕНИЕ О ПРЯМОМ ДЕБЕТОВАНИИ БАНКОВСКОГО СЧЁТА "
        "08.07.2026 г. с текущего счета Отправителя "
        "№KZ358562204156123527"
    )
    fields = [{
        "name": "guarantor_iban", "value": "KZ358562204156123527",
        "status": "extracted", "confidence": .8,
    }]
    fixed, _ = postprocess_fields(d, "direct_debit_agreement", fields, [])
    assert any(x["name"] == "sender_iban" for x in fixed)
    assert not any(x["name"] == "guarantor_iban" for x in fixed)


def test_howo_without_vin():
    d = doc(
        "ДОГОВОР КУПЛИ-ПРОДАЖИ. Самосвал HOWO T5G. "
        "Год выпуска: 2025. Количество: 1."
    )
    fields = [{"name": "purchase_total_kzt", "value": 35750000, "status": "extracted"}]
    tables = postprocess_tables(d, "purchase_contract", fields, [], None)
    row = next(t for t in tables if t["name"] == "asset_vin_rows")["rows"][0]
    assert row["model"] == "HOWO T5G"
    assert row["quantity"] == 1
    assert row["total_amount_kzt"] == 35750000


def test_one_row_schedule_is_not_exported_as_confirmed():
    d = doc("График 05.01.2024 1 000 000 500 000 500 000 9 000 000")
    tables = [{
        "name": "payment_schedule_rows",
        "row_count": 1,
        "rows": [{"date": "05.01.2024"}],
        "status": "extracted",
    }]
    fixed = postprocess_tables(d, "addendum", [], tables, None)
    assert not any(t["name"] == "payment_schedule_rows" for t in fixed)
