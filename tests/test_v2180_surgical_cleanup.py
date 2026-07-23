from app.services.document_reader import PageContent, ReadDocument
from app.services.safe_regression_fixes import postprocess_fields, postprocess_tables


def doc(pages):
    return ReadDocument(
        "x.pdf", len(pages), "pdf",
        [PageContent(i + 1, text, "digital", len(text), 0.99)
         for i, text in enumerate(pages)],
    )


def test_bank_guarantee_principal_and_bank_requisites():
    d = doc([
        'ЗАЯВЛЕНИЕ № OPT/2025/U/G/005305 о предоставлении банковской гарантии '
        '14.07.2025 ТОО "Тонар-Кокше" Принципал',
        'АО Банк ЦентрКредит БИК KCJBKZKX БИН 980640000093 '
        'А/щ NoKZ65125KZT 1001300224',
        'Принципал БИН/ИИН: 131140006348 ИИК KZ588562203109681293 БИК KCIBKZKX',
    ])
    fields = [{
        "name": "iin_bin_candidates",
        "value": ["131140006348", "671241000233", "780612303250"],
        "status": "candidate",
        "confidence": .55,
    }]
    fixed, _ = postprocess_fields(d, "bank_guarantee_application", fields, [])
    data = {x["name"]: x["value"] for x in fixed}
    assert data["bank_guarantee_application_number"] == "OPI/2025/U/G/005305"
    assert data["principal_iin_bin"] == "131140006348"
    assert data["bank_bic"] == "KCJBKZKX"
    assert data["bank_iban"] == "KZ65125KZT1001300224"
    candidates = next(x for x in fixed if x["name"] == "iin_bin_candidates")
    assert "131140006348" not in candidates["value"]
    assert "671241000233" not in candidates["value"]


def test_archimedes_removes_damaged_contract_and_candidates():
    d = doc([
        "Дополнительное соглашение №1 от 15 сентября 2023 "
        "к договору AG4/2022/U/L/113039. Архимедес Казахстан. "
        "Транш 18 076 464 дата 02.11.2022, "
        "транш 14 808 528 дата 09.01.2023.",
        "Лизингодатель БИН 020140001503 ИИК KZ678562203 116347262. "
        "Лизингополучатель БИН 080240011774 ИИК KZ778562203 105641968.",
    ])
    fields = [
        {"name": "lease_contract_number", "value": "AС4/2022", "status": "extracted", "confidence": .66},
        {"name": "tranche_numbers", "value": ["AG4/2022/0/L/1"], "status": "candidate", "confidence": .68},
        {"name": "tranche_amounts_kzt", "value": ["18076464.00"], "status": "candidate", "confidence": .68},
    ]
    fixed, _ = postprocess_fields(d, "addendum", fields, [])
    data = {x["name"]: x["value"] for x in fixed}
    assert data["base_contract_number"] == "AG4/2022/U/L/113039"
    assert data["lessor_iban"] == "KZ678562203116347262"
    assert data["lessee_iban"] == "KZ778562203105641968"
    assert not any(x.get("value") == "AС4/2022" for x in fixed)
    assert not any(x["name"] in {"tranche_numbers", "tranche_amounts_kzt"} for x in fixed)


def test_credit_line_guarantors_and_candidate_cleanup():
    d = doc([
        'гарантия юридического лица ТОО «KAZFITTING» БИН 121040012832 '
        '№OPK/2025/W/P/02145 от 03.09.2025. '
        'гарантия юридического лица ТОО «REM-ZHOL.KZ» БИН 190440009133 '
        '№OPK/2025/W/P/02142 от 03.09.2025. '
        'гарантия физического лица Кулькова Нина Алексеевна, ИИН 631203400836 '
        '№OPK/2025/W/P/02147 от 03.09.2025. '
        'Фонд Даму БИН 970840000277.'
    ])
    fields = [{
        "name": "iin_bin_candidates",
        "value": ["121040012832", "190440009133", "631203400836", "970840000277"],
        "status": "candidate",
        "confidence": .55,
    }]
    fixed, _ = postprocess_fields(d, "credit_line_agreement", fields, [])
    tables = postprocess_tables(d, "credit_line_agreement", fixed, [], None)
    table = next(t for t in tables if t["name"] == "guarantor_rows")
    assert table["row_count"] == 3
    names = {row["guarantor_name"] for row in table["rows"]}
    assert "REM-ZHOL.KZ" in names
    assert "Кулькова Нина Алексеевна" in names
    candidates = next(x for x in fixed if x["name"] == "iin_bin_candidates")
    assert candidates["value"] == []
