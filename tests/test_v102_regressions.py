
from inspect import signature

from app.services.candidate_resolver import resolve_candidates
from app.services.document_reader import PageContent, ReadDocument, read_document


def make_document(text: str) -> ReadDocument:
    return ReadDocument(
        filename="contract.pdf",
        page_count=1,
        source_type="pdf",
        pages=[
            PageContent(
                page_number=1,
                text=text,
                extraction_method="digital",
                char_count=len(text),
                quality=0.99,
                variants={"digital": text},
            )
        ],
    )


def test_reader_accepts_cache_parameter():
    assert "ocr_cache_dir" in signature(read_document).parameters


def test_same_identifier_is_not_assigned_to_two_roles():
    document = make_document(
        "ПОКУПАТЕЛЬ: BCC Leasing БИН 020140001503. "
        "ЛИЗИНГОПОЛУЧАТЕЛЬ: ИП КОРГАНОВ ИИН 951110350798. "
        "ПРОДАВЕЦ: ANTO MOTORS БИН 241240023483."
    )
    fields = resolve_candidates(document, [])
    role_values = {
        item["name"]: item["value"]
        for item in fields
        if item["name"] in {
            "buyer_iin_bin",
            "lessee_iin_bin",
            "seller_iin_bin",
            "lessor_iin_bin",
        }
    }

    assert role_values["buyer_iin_bin"] == "020140001503"
    assert role_values["lessee_iin_bin"] == "951110350798"
    assert role_values["seller_iin_bin"] == "241240023483"
    assert len(role_values.values()) == len(set(role_values.values()))
