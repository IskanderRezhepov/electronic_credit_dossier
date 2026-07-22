
from app.services.candidate_resolver import resolve_candidates
from app.services.document_reader import PageContent, ReadDocument


def make_doc(text: str) -> ReadDocument:
    return ReadDocument(
        filename="contract.pdf",
        page_count=1,
        source_type="pdf",
        pages=[PageContent(1, text, "digital", len(text), 0.99, {"digital": text})],
    )


def test_resolves_party_identifiers_from_requisites():
    document = make_doc(
        "ПРОДАВЕЦ: ТОО ANTO MOTORS БИН 241240023483 "
        "ПОКУПАТЕЛЬ: BCC Leasing БИН 020140001503 "
        "ЛИЗИНГОПОЛУЧАТЕЛЬ: ИП КОРГАНОВ ИИН 951110350798"
    )
    fields = resolve_candidates(document, [])
    values = {item["name"]: item["value"] for item in fields}
    assert values["seller_iin_bin"] == "241240023483"
    assert values["buyer_iin_bin"] == "020140001503"
    assert values["lessee_iin_bin"] == "951110350798"


def test_ambiguous_identifier_stays_candidate():
    document = make_doc("БИН 111111111111 БИН 222222222222")
    fields = resolve_candidates(document, [])
    assert any(item["name"] == "iin_bin_candidates" for item in fields)
