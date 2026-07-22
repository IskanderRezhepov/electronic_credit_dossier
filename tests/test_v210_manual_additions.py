from werkzeug.datastructures import MultiDict

from app.services.review import apply_review


def sample_result():
    return {
        "result_id": "abc",
        "documents": [{
            "filename": "unknown.pdf",
            "document_type": "unknown",
            "document_type_label_ru": "Неизвестный тип документа",
            "classification_confidence": 0.0,
            "fields": [{
                "name": "iin_bin_candidates",
                "label_ru": "Неопределённые ИИН/БИН",
                "value": ["111111111111", "222222222222"],
                "status": "candidate",
                "page": None,
                "quote": None,
                "confidence": 0.68,
                "extraction_method": "mixed",
                "notes": None,
            }],
        }],
        "dossier": {},
    }


def test_adds_manual_bin_with_category_and_page():
    form = MultiDict([
        ("add_0_field_name", "borrower_iin_bin"),
        ("add_0_value", "790 105 403 331"),
        ("add_0_page", "11"),
        ("add_0_custom_label", ""),
        ("add_0_notes", "Найдено в реквизитах"),
        ("add_0_candidate_source", "Кандидат: 790105403331"),
    ])
    updated, changed = apply_review(sample_result(), form)
    added = updated["documents"][0]["fields"][-1]
    assert changed == 1
    assert added["name"] == "borrower_iin_bin"
    assert added["value"] == "790105403331"
    assert added["page"] == 11
    assert added["status"] == "confirmed"
    assert added["extraction_method"] == "manual"


def test_adds_custom_field():
    form = MultiDict([
        ("add_0_field_name", "custom"),
        ("add_0_custom_label", "Номер решения"),
        ("add_0_value", "R-2026-15"),
        ("add_0_page", "3"),
    ])
    updated, changed = apply_review(sample_result(), form)
    added = updated["documents"][0]["fields"][-1]
    assert changed == 1
    assert added["label_ru"] == "Номер решения"
    assert added["name"].startswith("manual_")


def test_manual_document_type_override():
    form = MultiDict([("document_0_type", "guarantee_contract")])
    updated, changed = apply_review(sample_result(), form)
    document = updated["documents"][0]
    assert changed == 1
    assert document["document_type"] == "guarantee_contract"
    assert document["classification_method"] == "manual"
