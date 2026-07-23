from app.services.document_reader import PageContent, ReadDocument
from app.services.precision_postprocess import improve_fields
from app.services.source_locator import locate_value
from app.services.equipment_precision import _clean_model


def doc(text, document_type="lease_contract"):
    return ReadDocument(
        "x.pdf", 1, "pdf",
        [PageContent(1, text, "digital", len(text), 0.99)],
    )


def test_lease_contract_drops_generic_recipient():
    document = doc(
        "ТОО TARAPACK, БИН 171140009092, далее Лизингополучатель. "
        "Предоставить личную гарантию, ИИН 801115300739."
    )
    fields = [
        {"name": "recipient_name", "label_ru": "Получатель", "value": "TARAPACK", "status": "extracted"},
        {"name": "recipient_iin_bin", "label_ru": "ИИН/БИН — Получатель", "value": "801115300739", "status": "extracted"},
    ]
    result = improve_fields(document, "lease_contract", fields, [])
    names = {item["name"] for item in result}
    assert "recipient_name" not in names
    assert "recipient_iin_bin" not in names
    assert "guarantor_iin_bin" in names


def test_addendum_sender_bin_becomes_recipient():
    document = doc(
        'Отправитель ТОО "Center Leasing". Получатель 1 ТОО "KazPromService", '
        '130940024372, Казахстан.',
        "addendum",
    )
    fields = [
        {"name": "recipient_name", "label_ru": "Получатель", "value": "KazPromService", "status": "extracted"},
        {
            "name": "sender_iin_bin", "label_ru": "ИИН/БИН — Отправитель",
            "value": "130940024372", "status": "candidate",
            "quote": 'Получатель 1 ТОО "KazPromService", 130940024372',
        },
    ]
    result = improve_fields(document, "addendum", fields, [])
    assert any(
        item["name"] == "recipient_iin_bin" and item["value"] == "130940024372"
        for item in result
    )


def test_guarantee_date_comes_from_header_not_power_of_attorney():
    document = doc(
        "Договор гарантии № AQ5/2023/W/P/03125 "
        "г. Алматы, 05.12.2023. "
        "действующей на основании доверенности от 21.10.2022.",
        "guarantee_contract",
    )
    result = improve_fields(document, "guarantee_contract", [], [])
    date = next(item for item in result if item["name"] == "guarantee_contract_date")
    assert date["value"] == "05.12.2023"


def test_model_unfinished_parenthesis_is_removed():
    assert _clean_model("Hyundai Santa Fe MX5 Modern 6 seat (2.5 GDI") == (
        "Hyundai Santa Fe MX5 Modern 6 seat"
    )


def test_one_source_location_per_page():
    layouts = [{
        "page": 1,
        "words": [
            {"text": "БИН", "x0": 5, "y0": 5, "x1": 20, "y1": 15},
            {"text": "130940024372", "x0": 25, "y0": 5, "x1": 120, "y1": 15},
            {"text": "130940024372", "x0": 25, "y0": 100, "x1": 120, "y1": 110},
        ],
    }]
    assert len(locate_value(layouts, "130940024372")) == 1
