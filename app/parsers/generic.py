
from __future__ import annotations

from .base import common_fields, field, find_all_regex
from app.services.document_reader import ReadDocument


def parse(document: ReadDocument) -> list[dict]:
    fields = common_fields(document)

    vins = find_all_regex(document, r"\b[A-HJ-NPR-Z0-9]{17}\b")
    if vins:
        fields.append(
            field(
                name="vin_candidates",
                label_ru="Найденные VIN",
                value=vins,
                page=None,
                quote=None,
                confidence=0.75 if document.used_ocr else 0.92,
                extraction_method="mixed" if document.used_ocr else "digital",
            )
        )

    ibans = find_all_regex(document, r"\bKZ\d{18}\b")
    if ibans:
        fields.append(
            field(
                name="iban_candidates",
                label_ru="Найденные банковские счета",
                value=ibans,
                page=None,
                quote=None,
                confidence=0.76 if document.used_ocr else 0.92,
                extraction_method="mixed" if document.used_ocr else "digital",
            )
        )

    return fields
