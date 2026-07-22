from inspect import signature

from app.services.document_reader import read_document


def test_read_document_accepts_ocr_cache_dir():
    parameters = signature(read_document).parameters
    assert "ocr_cache_dir" in parameters
