
from decimal import Decimal

from app.parsers.specific import (
    _normalize_addendum_contract_candidate,
    _purchase_total_fallback,
    _schedule_principal_fallback,
)
from app.services.document_reader import PageContent, ReadDocument
from app.services.quality import review_fields


def doc(text: str, filename: str = "x.pdf", ocr: bool = True) -> ReadDocument:
    method = "ocr" if ocr else "digital"
    return ReadDocument(
        filename=filename,
        page_count=1,
        source_type="pdf",
        pages=[PageContent(1, text, method, len(text), 0.82, {method: text})],
    )


def test_purchase_total_from_30_70_split():
    document = doc(
        "30% составляет 182 122 200,00 тенге. "
        "70% составляет 424 951 800,00 тенге."
    )
    result = _purchase_total_fallback(document)
    assert result["value"] == "607074000.00"


def test_schedule_principal_from_first_row():
    document = doc(
        "05.12.22 768 614,38 488 553,00 280 061,38 "
        "280 061,38 17 587 911,00"
    )
    result = _schedule_principal_fallback(document)
    assert result["value"] == "18076464.00"


def test_addendum_ocr_contract_normalisation():
    assert (
        _normalize_addendum_contract_candidate("AGSH2022/L/L/113039")
        == "AG4/2022/U/L/113039"
    )


def test_short_addendum_number_is_valid():
    fields = [{
        "name": "addendum_number",
        "label_ru": "Номер дополнительного соглашения",
        "value": "1",
        "confidence": 0.9,
        "status": "extracted",
        "extraction_method": "ocr",
    }, {
        "name": "lease_contract_number",
        "label_ru": "Номер основного договора",
        "value": "AG4/2022/U/L/113039",
        "confidence": 0.8,
        "status": "candidate",
        "extraction_method": "ocr",
    }]
    warnings = review_fields("addendum", fields)
    assert not any("Подозрительный номер: 1" in item["message"] for item in warnings)
