
from app.parsers.specific import (
    _filename_act_references,
    _schedule_principal_fallback,
)
from app.services.document_reader import PageContent, ReadDocument


def make_doc(text="", filename="x.pdf", method="ocr"):
    return ReadDocument(
        filename=filename,
        page_count=1,
        source_type="pdf",
        pages=[PageContent(1, text, method, len(text), 0.82, {method: text})],
    )


def test_filename_act_and_linked_contract_stop_before_date():
    document = make_doc(
        filename=(
            "F-1343111332_Акт приема передачи №1 к Договору купли-продажи "
            "для последующей передачи в фин. лизинг №25-CL-28-10 "
            "от 28 октября 2022г.pdf"
        )
    )
    act, linked = _filename_act_references(document)
    assert act["value"] == "1"
    assert linked["value"] == "25-CL-28-10"


def test_schedule_fallback_uses_repeated_principal_and_final_balance():
    document = make_doc(
        "05.12.2022 768 614,38 488 553,00 280 061,38 "
        "488 553,00 17 587 911,00"
    )
    result = _schedule_principal_fallback(document)
    assert result is not None
    assert result["value"] == "18076464.00"


def test_schedule_fallback_rejects_ambiguous_row():
    document = make_doc(
        "05.12.2022 7 000 000,00 6 000 000,00 "
        "5 000 000,00 18 000 000,00"
    )
    assert _schedule_principal_fallback(document) is None
