import json
from pathlib import Path

from app import create_app
from app.services.dossier import build_dossier_summary
from app.services.review import apply_review, field_value_for_form


def sample_result():
    return {
        "result_id": "abc123",
        "documents": [{
            "filename": "lease.pdf",
            "document_type": "lease_contract",
            "document_type_label_ru": "Договор финансового лизинга",
            "page_count": 1,
            "used_ocr": False,
            "page_methods": [],
            "classification_confidence": 0.99,
            "warnings": [],
            "tables": [],
            "fields": [
                {
                    "name": "lessee_iin_bin",
                    "label_ru": "ИИН/БИН — Лизингополучатель",
                    "value": "111111111111",
                    "page": 1,
                    "quote": "БИН 111111111111",
                    "confidence": 0.8,
                    "extraction_method": "digital",
                    "status": "candidate",
                    "notes": None,
                }
            ],
        }],
        "dossier": {},
    }


def test_apply_review_confirms_and_preserves_original():
    result, changed = apply_review(sample_result(), {
        "document_0_field_0_value": "222222222222",
        "document_0_field_0_status": "corrected",
    })
    field = result["documents"][0]["fields"][0]
    assert changed == 1
    assert field["value"] == "222222222222"
    assert field["original_value"] == "111111111111"
    assert field["original_status"] == "candidate"
    assert field["status"] == "corrected"
    assert field["review_source"] == "manual"


def test_rejected_field_is_not_used_in_dossier():
    result = sample_result()
    result["documents"][0]["fields"][0]["status"] = "rejected"
    dossier = build_dossier_summary(result["documents"])
    assert dossier["identities"] == []


def test_list_value_is_editable_as_json():
    assert field_value_for_form(["A", "B"]).startswith("[")
    result = sample_result()
    result["documents"][0]["fields"][0]["value"] = ["A", "B"]
    updated, changed = apply_review(result, {
        "document_0_field_0_value": '["C", "D"]',
        "document_0_field_0_status": "confirmed",
    })
    assert changed == 1
    assert updated["documents"][0]["fields"][0]["value"] == ["C", "D"]


def test_review_route_persists_and_regenerates_exports(tmp_path):
    app = create_app()
    app.config.update(TESTING=True, RESULT_FOLDER=str(tmp_path))
    original = sample_result()
    (tmp_path / "abc123.json").write_text(
        json.dumps(original, ensure_ascii=False),
        encoding="utf-8",
    )

    client = app.test_client()
    response = client.post("/review/abc123", data={
        "document_0_field_0_value": "222222222222",
        "document_0_field_0_status": "confirmed",
    })
    assert response.status_code == 302
    saved = json.loads((tmp_path / "abc123.json").read_text(encoding="utf-8"))
    assert saved["documents"][0]["fields"][0]["status"] == "confirmed"
    assert saved["documents"][0]["fields"][0]["value"] == "222222222222"
    assert (tmp_path / "abc123.xlsx").exists()


def test_results_page_can_be_reopened(tmp_path):
    app = create_app()
    app.config.update(TESTING=True, RESULT_FOLDER=str(tmp_path))
    (tmp_path / "abc123.json").write_text(
        json.dumps(sample_result(), ensure_ascii=False),
        encoding="utf-8",
    )
    response = app.test_client().get("/results/abc123")
    assert response.status_code == 200
    assert "Сохранить проверку" in response.get_data(as_text=True)
