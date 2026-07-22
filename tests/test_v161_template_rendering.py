from flask import render_template

from app import create_app
from app.routes import _normalise_tables


def test_results_template_renders_structured_table():
    app = create_app()
    result = {
        "result_id": "abc123",
        "dossier": None,
        "documents": [{
            "filename": "test.pdf",
            "page_count": 1,
            "used_ocr": True,
            "warnings": [],
            "document_type": "payment_schedule",
            "document_type_label_ru": "График платежей",
            "classification_confidence": 0.99,
            "page_methods": [],
            "fields": [],
            "tables": _normalise_tables([{
                "label_ru": "Таблица графика",
                "columns": [{"key": "date", "label_ru": "Дата"}],
                "rows": [{"date": "01.01.2026"}],
                "confidence": 0.9,
                "status": "extracted",
            }]),
        }],
    }
    with app.test_request_context():
        html = render_template("results.html", result=result)
    assert "01.01.2026" in html
    assert "Таблица графика" in html


def test_normalise_tables_discards_method_like_rows():
    assert _normalise_tables([{"columns": list, "rows": dict.items}]) == []
