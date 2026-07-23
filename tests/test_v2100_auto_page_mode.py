from app.services.document_reader import PageContent


def test_page_content_has_analysis_profile():
    page = PageContent(
        page_number=1,
        text="text",
        extraction_method="ocr",
        char_count=4,
        quality=0.8,
        analysis_profile="auto-fast",
    )
    assert page.analysis_profile == "auto-fast"
