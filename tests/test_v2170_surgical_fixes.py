from app.services.document_reader import PageContent, ReadDocument
from app.services.safe_regression_fixes import (
    first_page_type, postprocess_fields, postprocess_tables,
)


def doc(pages):
    return ReadDocument(
        "x.pdf", len(pages), "pdf",
        [PageContent(i + 1, text, "digital", len(text), 0.99)
         for i, text in enumerate(pages)],
    )


def test_bank_guarantee_application_classification():
    d = doc([
        "ЗАЯВЛЕНИЕ № OPI/2025/U/G/005305 о присоединении "
        "к Договору присоединения о предоставлении банковской гарантии"
    ])
    assert first_page_type(d, "unknown") == "bank_guarantee_application"


def test_invalid_doc_id_iban_is_removed_and_real_lessee_iban_kept():
    d = doc([
        "Заявление о присоединении к договору финансового лизинга",
        "банковский счет Лизингополучателя №KZ328562203156763718 "
        "Счет Лизингодателя №KZ678562203116347262",
    ])
    fields = [{
        "name": "lessee_iban", "label_ru": "IBAN — Лизингополучатель",
        "value": "KZPKXV42026000132828", "status": "candidate", "confidence": .55,
    }]
    fixed, _ = postprocess_fields(d, "lease_contract", fields, [])
    assert any(x["name"] == "lessee_iban" and x["value"] == "KZ328562203156763718" for x in fixed)
    assert not any(x.get("value") == "KZPKXV42026000132828" for x in fixed)


def test_subsidy_addendum_one_iban_one_role():
    d = doc([
        "Дополнительное соглашение №1 к Договору субсидирования",
        "Финансовое агентство БИН 970840000277 ИИК KZ42070F000001F00001 "
        "Лизинговая компания БИН 020140001503 ИИК KZ418562203117893716 "
        "Получатель БИН 130940024372",
    ])
    fields = [
        {"name": "recipient_iban", "value": "KZ42070F000001F00001", "status": "extracted", "confidence": .96},
        {"name": "recipient_iban", "value": "KZ418562203117893716", "status": "extracted", "confidence": .96},
    ]
    fixed, _ = postprocess_fields(d, "addendum", fields, [])
    roles = {(x["name"], x["value"]) for x in fixed}
    assert ("financial_agency_iban", "KZ42070F000001F00001") in roles
    assert ("leasing_company_iban", "KZ418562203117893716") in roles
    assert ("recipient_iban", "KZ42070F000001F00001") not in roles
    assert ("recipient_iban", "KZ418562203117893716") not in roles


def test_credit_line_purpose_and_accounts():
    d = doc([
        "СОГЛАШЕНИЕ ОБ ОТКРЫТИИ КЛ. Цель КЛ: Пополнение оборотных средств. "
        "Заемщик БИН 130340002716",
        "Банк БИН 980640000093 ИИК KZ65125KZT1001300224. "
        "Заемщик ИИК KZ438562203102025353",
    ])
    fixed, _ = postprocess_fields(d, "credit_line_agreement", [], [])
    data = {x["name"]: x["value"] for x in fixed}
    assert data["credit_line_purpose"] == "Пополнение оборотных средств"
    assert data["bank_iban"] == "KZ65125KZT1001300224"
    assert data["borrower_iban"] == "KZ438562203102025353"


def test_archimedes_date_contract_and_tranches():
    d = doc([
        "ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ №1 К ДОГОВОРУ ФИНАНСОВОГО ЛИЗИНГА "
        "№ AG4/2022/U/L/113039 от 28 октября 2022 г. "
        "Настоящее Дополнительное соглашение №1 от 15 сентября 2023 г. "
        "Транш AG4/2022/U/L/113039/0001L Сумма транша 18 076 464 "
        "Дата выдачи 02.11.2022. "
        "Транш AG4/2022/U/L/113039/0002L Сумма транша 14 808 528 "
        "Дата выдачи 09.01.2023. ТОО Архимедес Казахстан",
        "Лизингодатель БИН 020140001503 ИИК KZ678562203116347262 "
        "Лизингополучатель БИН 080240011774 ИИК KZ778562203105641968",
    ])
    fixed, _ = postprocess_fields(d, "addendum", [], [])
    data = {x["name"]: x["value"] for x in fixed}
    assert data["addendum_date"] == "15.09.2023"
    assert data["base_contract_number"] == "AG4/2022/U/L/113039"
    assert data["lessee_name"] == "ТОО «Архимедес Казахстан»"
    assert data["lessee_iban"] == "KZ778562203105641968"
    tables = postprocess_tables(d, "addendum", fixed, [], None)
    tranche = next(t for t in tables if t["name"] == "tranche_rows")
    assert tranche["row_count"] == 2
