from types import SimpleNamespace

from app.parsers.base import field
from app.services.insurance_gps import apply_insurance_gps
from app.services.safe_regression_fixes import postprocess_fields, postprocess_tables


def page(text, number=1, method="digital"):
    return SimpleNamespace(page_number=number, text=text, extraction_method=method, quality=0.99)


def document(*texts):
    pages = [page(text, i + 1) for i, text in enumerate(texts)]
    return SimpleNamespace(pages=pages, full_text="\n".join(texts), filename="sample.pdf", page_count=len(pages))


def values(fields):
    return {item["name"]: item.get("value") for item in fields}


def test_clause_number_is_not_lease_advance():
    doc = document("ДОГОВОР ФИНАНСОВОГО ЛИЗИНГА № AQ5/2026/U/S/045767\n4.10. Страхование предмета лизинга")
    fields = [field(name="advance_payment_kzt", label_ru="Аванс", value="4.10", page=1, quote="4.10. Страхование", confidence=.8, extraction_method="digital")]
    result, _ = postprocess_fields(doc, "lease_contract", fields, [])
    assert "advance_payment_kzt" not in values(result)


def test_lease_insurance_clause_does_not_create_policy_table():
    doc = document("ДОГОВОР ФИНАНСОВОГО ЛИЗИНГА\nЛизингополучатель обязан оформить КАСКО и страхование в пути")
    fields, tables = apply_insurance_gps(doc, "lease_contract", [], [])
    assert not any(item["name"].startswith("insurance_") for item in fields)
    assert not any(table.get("name") == "insurance_rows" for table in tables)


def test_mcompany_addendum_uses_title_date_and_drops_rasul_attachment():
    doc = document(
        "Дополнительное соглашение №1 к Заявлению о присоединении № OPA/2025/U/L/027729 от 09.06.2025 г.\n"
        "г. Астана 22.12.2025 г.\nТОО «MCompany Group», БИН 201240028328",
        "ИЗМЕНЕНИЯ И ДОПОЛНЕНИЯ №1\nИП «РАСУЛ»",
    )
    fields = [field(name="addendum_date", label_ru="Дата", value="09.06.2025", page=1, quote="09.06.2025", confidence=.9, extraction_method="digital")]
    result, _ = postprocess_fields(doc, "addendum", fields, [])
    got = values(result)
    assert got["addendum_date"] == "22.12.2025"
    assert got["base_contract_date"] == "09.06.2025"
    assert "lessee_name" not in got


def test_ambiguous_guarantee_number_is_not_confirmed_twice(monkeypatch):
    doc = document("СОГЛАШЕНИЕ ОБ ОТКРЫТИИ КЛ")
    fake = {
        "name": "guarantor_rows", "rows": [
            {"guarantor_name": "A", "guarantee_number": "X/1"},
            {"guarantor_name": "B", "guarantee_number": "X/1"},
            {"guarantor_name": "C", "guarantee_number": "X/2"},
        ], "row_count": 3,
    }
    monkeypatch.setattr("app.services.safe_regression_fixes._guarantor_table", lambda _doc: fake)
    tables = postprocess_tables(doc, "credit_line_agreement", [], [])
    table = next(t for t in tables if t["name"] == "guarantor_rows")
    assert [row["guarantee_number"] for row in table["rows"]] == ["X/2"]
    assert "X/1" in table["notes"]
