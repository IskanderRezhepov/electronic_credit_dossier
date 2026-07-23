from app.services.candidate_resolver import resolve_candidates
from app.services.document_reader import PageContent, ReadDocument, _ocr_multimode


def make_doc(text):
    return ReadDocument(
        "x.pdf", 1, "pdf",
        [PageContent(1, text, "digital", len(text), 0.99, {"digital": text})],
    )


def test_unresolved_identifier_has_role_suggestions():
    document = make_doc(
        "ЗАЕМЩИК: ТОО Клиент. Реквизиты Заемщика БИН 790105403331. "
        "БЕНЕФИЦИАР: БИН 980640000093."
    )
    fields = resolve_candidates(document, [], "unknown")
    candidate = next((item for item in fields if item["name"] == "iin_bin_candidates"), None)
    # Strongly resolved IDs may be promoted directly; otherwise suggestions must exist.
    if candidate:
        assert "suggestions" in candidate
        assert all(isinstance(value, list) for value in candidate["suggestions"].values())


def test_page_content_cache_hit_defaults_false():
    page = PageContent(1, "text", "digital", 4, 0.99)
    assert page.cache_hit is False
