
from app.services.classifier import classify
from app.services.document_reader import _digital_text_is_usable
from app.services.table_extractor import extract_tables
from app.parsers.base import generic_identifiers
from app.services.document_reader import PageContent, ReadDocument


def make_doc(text: str, filename: str = "x.pdf", method: str = "digital"):
    return ReadDocument(
        filename=filename,
        page_count=1,
        source_type="pdf",
        pages=[PageContent(1, text, method, len(text), 0.9, {method: text})],
    )


def test_broken_hidden_ocr_is_not_accepted_as_good_digital_text():
    broken = (
        "3aJIOrO)laTeJIb TOBapHIUecTBo C 0 paUHQeHlIOH "
        "OTBeTCTBeHHOCTbIO 130340002716 " * 20
    )
    assert not _digital_text_is_usable(broken, 80)


def test_direct_debit_agreement_beats_guarantee_reference():
    text = (
        "СОГЛАШЕНИЕ О ПРЯМОМ ДЕБЕТОВАНИИ БАНКОВСКОГО СЧЕТА. "
        "Отправитель и Бенефициар. Между сторонами заключен "
        "Договор гарантии №OPA/2026/W/P/06430."
    )
    result = classify(text, "Соглашение ФЛ_08.07.2026г.pdf")
    assert result.key == "direct_debit_agreement"


def test_subsidy_appendix_is_not_published_as_false_payment_table():
    document = make_doc(
        "Договор субсидирования. График погашения. "
        "07.08.23 44 270 270,00 1 229 730,00"
    )
    assert extract_tables(document, "subsidy_agreement") == []


def test_lowercase_ocr_word_is_not_a_vin():
    document = make_doc("npezurpuuaaarenen")
    fields = generic_identifiers(document)
    assert not any(item["name"] == "vin_candidates" for item in fields)
