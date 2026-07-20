
from __future__ import annotations

import re
from typing import Callable, Iterable

from app.services.document_reader import ReadDocument
from app.services.text_utils import parse_money, quote_around


def field(
    *,
    name: str,
    label_ru: str,
    value,
    page: int | None,
    quote: str | None,
    confidence: float,
    extraction_method: str,
    value_type: str = "direct",
) -> dict:
    if hasattr(value, "__str__") and value.__class__.__name__ == "Decimal":
        value = str(value)

    return {
        "name": name,
        "label_ru": label_ru,
        "value": value,
        "page": page,
        "quote": quote,
        "confidence": confidence,
        "extraction_method": extraction_method,
        "value_type": value_type,
    }


def find_first(
    document: ReadDocument,
    *,
    patterns: Iterable[str],
    name: str,
    label_ru: str,
    converter: Callable | None = None,
    confidence: float = 0.94,
) -> dict | None:
    for page in document.pages:
        for pattern in patterns:
            match = re.search(pattern, page.text, re.IGNORECASE | re.DOTALL)
            if not match:
                continue

            raw = match.group(1) if match.groups() else match.group(0)
            value = converter(raw) if converter else raw.strip()

            return field(
                name=name,
                label_ru=label_ru,
                value=value,
                page=page.page_number,
                quote=quote_around(page.text, match.start(), match.end()),
                confidence=confidence if page.extraction_method == "digital" else min(confidence, 0.82),
                extraction_method=page.extraction_method,
            )
    return None


def find_all_regex(document: ReadDocument, pattern: str) -> list[str]:
    values: set[str] = set()
    for page in document.pages:
        values.update(re.findall(pattern, page.text, re.IGNORECASE))
    return sorted(value.strip() if isinstance(value, str) else str(value) for value in values)


def common_fields(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []

    definitions = [
        (
            [r"DOC ID\s*([A-Z0-9]+)"],
            "doc_id",
            "DOC ID",
            None,
        ),
        (
            [
                r"(?:№|N)\s*([A-ZА-Я0-9][A-ZА-Я0-9/_-]{4,})",
                r"Номер документа[:\s]+([A-ZА-Я0-9/_-]+)",
            ],
            "document_number_candidate",
            "Номер документа",
            None,
        ),
        (
            [
                r"\b(\d{2}\.\d{2}\.\d{4})\b",
                r"«(\d{1,2})»\s+[а-яё]+\s+(\d{4})",
            ],
            "document_date_candidate",
            "Дата документа",
            None,
        ),
        (
            [r"Рег\.\s*Номер:\s*([0-9/.-]+)"],
            "registration_number",
            "Регистрационный номер",
            None,
        ),
        (
            [r"Рег\.\s*Дата:\s*(\d{2}\.\d{2}\.\d{4})"],
            "registration_date",
            "Регистрационная дата",
            None,
        ),
        (
            [r"(?:Подписи|Электронные подписи \(ЭЦП\))\s*(\d+)"],
            "signature_count",
            "Количество ЭЦП",
            int,
        ),
    ]

    for patterns, name, label, converter in definitions:
        item = find_first(
            document,
            patterns=patterns,
            name=name,
            label_ru=label,
            converter=converter,
        )
        if item:
            fields.append(item)

    ids = find_all_regex(document, r"\b\d{12}\b")
    if ids:
        fields.append(
            field(
                name="iin_bin_candidates",
                label_ru="Найденные ИИН/БИН",
                value=ids,
                page=None,
                quote=None,
                confidence=0.78 if document.used_ocr else 0.90,
                extraction_method="mixed" if document.used_ocr else "digital",
            )
        )

    amounts = find_all_regex(
        document,
        r"\b\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{2})\b",
    )
    unique_amounts = []
    for raw in amounts:
        parsed = parse_money(raw)
        if parsed is not None:
            unique_amounts.append(str(parsed))

    if unique_amounts:
        fields.append(
            field(
                name="money_candidates",
                label_ru="Найденные денежные суммы",
                value=sorted(set(unique_amounts)),
                page=None,
                quote=None,
                confidence=0.72 if document.used_ocr else 0.86,
                extraction_method="mixed" if document.used_ocr else "digital",
            )
        )

    return fields
