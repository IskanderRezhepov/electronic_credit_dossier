from app.services.page_reprocessor import merge_manual_fields, rebuild_read_document


def test_manual_fields_survive_page_reprocessing():
    new = [{"name": "contract_date", "value": "01.01.2026", "status": "extracted"}]
    old = [{"name": "borrower_iin_bin", "value": "790105403331", "status": "confirmed", "extraction_method": "manual"}]
    merged = merge_manual_fields(new, old)
    assert len(merged) == 2


def test_rebuild_read_document_uses_saved_page_texts():
    document = {
        "filename": "x.pdf", "page_count": 2, "source_type": "pdf",
        "page_texts": [{"page": 1, "text": "Первая"}, {"page": 2, "text": "Вторая"}],
        "page_methods": [{"page": 1, "method": "digital", "quality": 0.99}, {"page": 2, "method": "ocr", "quality": 0.7}],
        "page_layouts": [{"page": 1, "words": []}, {"page": 2, "words": []}],
    }
    rebuilt = rebuild_read_document(document)
    assert rebuilt.page_count == 2
    assert "Первая" in rebuilt.full_text
    assert "Вторая" in rebuilt.full_text
