
from pathlib import Path

from app.parsers.base import find_first
from app.services.document_reader import PageContent, ReadDocument
from app.services.text_utils import parse_money


def make_document(text: str, filename: str = "test.pdf") -> ReadDocument:
    return ReadDocument(
        filename=filename,
        page_count=1,
        source_type="pdf",
        pages=[
            PageContent(
                page_number=1,
                text=text,
                extraction_method="digital",
                quality=0.99,
                char_count=len(text),
            )
        ],
    )


def test_find_first_skips_none_converter_result():
    document = make_document(
        "Общая цена Оборудования определяется и оплачивается покупателем."
    )
    result = find_first(
        document,
        patterns=[
            r"Общая цена Оборудования[^\\d]{0,100}(\\d[\\d\\s]*(?:[,.]\\d{1,2})?)"
        ],
        name="total_amount_kzt",
        label_ru="Общая стоимость",
        converter=parse_money,
    )
    assert result is None


def test_money_pattern_reads_real_amount():
    document = make_document(
        "Общая цена Оборудования составляет 135 235 386 тенге."
    )
    result = find_first(
        document,
        patterns=[
            r"Общая цена Оборудования\\s+составляет\\s+(\\d[\\d\\s]*(?:[,.]\\d{1,2})?)\\s+тенге"
        ],
        name="total_amount_kzt",
        label_ru="Общая стоимость",
        converter=parse_money,
    )
    assert result["value"] == "135235386.00"
