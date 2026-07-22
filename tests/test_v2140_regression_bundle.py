from app.services.document_reader import PageContent, ReadDocument
from app.services.regression_fixes import (
    override_document_type,
    postprocess_fields,
    postprocess_tables,
)


def make_doc(text, pages=None):
    if pages is None:
        pages = [text]
    return ReadDocument(
        "x.pdf", len(pages), "pdf",
        [
            PageContent(i + 1, page, "digital", len(page), 0.99)
            for i, page in enumerate(pages)
        ],
    )


def test_addendum_wins_over_schedule_appendix():
    doc = make_doc(
        "Дополнительное соглашение №1 к Договору финансового лизинга "
        "AQ5/2023/U/S/221974/0001L. График погашения."
    )
    assert override_document_type(doc, "payment_schedule") == "addendum"


def test_direct_debit_account_is_sender_not_guarantor():
    doc = make_doc(
        "СОГЛАШЕНИЕ О ПРЯМОМ ДЕБЕТОВАНИИ БАНКОВСКОГО СЧЁТА "
        "г. Астана 08.07.2026 г. гражданин Мададов Муслим Айвасович, "
        "ИИН 910505301221. с текущего счета Отправителя "
        "№KZ358562204156123527. Наименование Бенефициара: "
        "Дочерняя компания АО «Банк ЦентрКредит» Акционерное общество "
        "«BCC Leasing», БИН 020140001503."
    )
    fields = [{
        "name": "guarantor_iban",
        "label_ru": "IBAN — Гарант",
        "value": "KZ358562204156123527",
        "status": "extracted",
        "confidence": 0.8,
    }]
    result = postprocess_fields(doc, "direct_debit_agreement", fields, [])
    assert any(
        item["name"] == "sender_iban"
        and item["value"] == "KZ358562204156123527"
        for item in result
    )
    assert not any(item["name"] == "guarantor_iban" for item in result)


def test_subsidy_roles_and_rates():
    doc = make_doc(
        "Договор субсидирования. Финансовое агентство Даму, "
        "лизинговая компания Center Leasing, Получатель ТОО «Арлан Сауда». "
        "БИН 970840000277 ИИК KZ42070F000001F00001. "
        "БИН 020140001503 ИИК KZ418562203117893716. "
        "Получатель ТОО Арлан Сауда БИН 140740010189. "
        "Ставка вознаграждения 21,75 процентов. "
        "часть ставки вознаграждения в размере 12,75 процентов оплачивает "
        "финансовое агентство, остальную часть ставки в размере 9 процентов "
        "оплачивает Получатель."
    )
    result = postprocess_fields(doc, "subsidy_agreement", [], [])
    assert any(item["name"] == "financial_agency_iban" for item in result)
    assert any(item["name"] == "leasing_company_iban" for item in result)
    assert any(
        item["name"] == "recipient_iin_bin"
        and item["value"] == "140740010189"
        for item in result
    )
    assert any(
        item["name"] == "nominal_rate_percent"
        and item["value"] == 21.75
        for item in result
    )


def test_purchase_equipment_without_vin():
    doc = make_doc(
        "Договор купли-продажи 15.07.2026. Спецификация: "
        "Самосвал HOWO T5G. Год выпуска: 2025. Количество: 1. "
        "Цена за единицу 35 750 000,00."
    )
    fields = [{
        "name": "purchase_total_kzt",
        "value": 35750000,
        "status": "extracted",
    }]
    tables = postprocess_tables(doc, "purchase_contract", fields, [])
    row = next(t for t in tables if t["name"] == "asset_vin_rows")["rows"][0]
    assert row["brand"] == "HOWO"
    assert row["model"] == "HOWO T5G"
    assert row["manufacture_year"] == "2025"
    assert row["quantity"] == 1
    assert row["total_amount_kzt"] == 35750000


def test_six_amount_schedule_row():
    doc = make_doc(
        "График погашения\n"
        "05.01.24 4 200 042,74 2 707 611,00 1 492 431,74 "
        "1 001 816,25 490 615,49 97 474 014,00"
    )
    tables = postprocess_tables(doc, "addendum", [], [])
    table = next(t for t in tables if t["name"] == "payment_schedule_rows")
    row = table["rows"][0]
    assert row["total_payment"] == 4200042.74
    assert row["principal"] == 2707611.00
    assert row["agency_interest"] == 490615.49
    assert row["balance"] == 97474014.00
