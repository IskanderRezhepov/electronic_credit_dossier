import json

from app import create_app
from app.services.client_registry import identify_client, list_clients, register_result
from app.services.dossier import build_dossier_summary


def equipment_document(filename, doc_type, vin, model="X3000", quantity=1):
    return {
        "filename": filename,
        "document_type": doc_type,
        "document_type_label_ru": doc_type,
        "fields": [],
        "tables": [{
            "name": "asset_vin_rows",
            "rows": [{
                "vin": vin,
                "equipment_type": "Самосвал",
                "model": model,
                "quantity": quantity,
                "unit_price_kzt": 45000000.0,
                "total_amount_kzt": 45000000.0,
                "page": 2,
            }],
            "summary": {
                "total_quantity": quantity,
                "unique_vin_count": 1,
                "equipment_by_type": {"Самосвал": quantity},
            },
        }],
    }


def test_identifies_primary_client_by_lessee():
    client = identify_client([{
        "filename": "lease.pdf",
        "fields": [
            {"name": "seller_iin_bin", "value": "111111111111", "status": "extracted"},
            {"name": "lessee_iin_bin", "value": "222222222222", "status": "confirmed"},
            {"name": "lessee_name", "value": "ТОО Клиент", "status": "extracted"},
        ],
    }])
    assert client["iin_bin"] == "222222222222"
    assert client["name"] == "ТОО Клиент"
    assert client["role_label_ru"] == "Лизингополучатель"


def test_registers_and_updates_client_history(tmp_path):
    result = {
        "result_id": "abc123",
        "documents": [{
            "filename": "lease.pdf",
            "document_type_label_ru": "Договор лизинга",
            "fields": [
                {"name": "lessee_iin_bin", "value": "222222222222", "status": "confirmed"},
            ],
            "tables": [],
        }],
        "dossier": {"status": "ok"},
    }
    register_result(tmp_path, result)
    clients = list_clients(tmp_path)
    assert len(clients) == 1
    assert clients[0]["iin_bin"] == "222222222222"
    assert clients[0]["analysis_count"] == 1

    result["review"] = {"status": "reviewed"}
    register_result(tmp_path, result)
    clients = list_clients(tmp_path)
    assert clients[0]["analysis_count"] == 1
    assert clients[0]["results"][0]["review_status"] == "reviewed"


def test_equipment_vin_sets_match_across_documents():
    documents = [
        equipment_document("lease.pdf", "lease_contract", "LZGJL4V44PX123456"),
        equipment_document("act.pdf", "acceptance_act", "LZGJL4V44PX123456"),
    ]
    dossier = build_dossier_summary(documents)
    equipment_checks = [item for item in dossier["checks"] if item["category"] == "Техника"]
    assert any(item["check"] == "Комплект VIN между документами" and item["status"] == "match" for item in equipment_checks)
    assert dossier["equipment"]["unique_vin_count"] == 1


def test_equipment_model_mismatch_is_detected():
    documents = [
        equipment_document("lease.pdf", "lease_contract", "LZGJL4V44PX123456", model="X3000"),
        equipment_document("act.pdf", "acceptance_act", "LZGJL4V44PX123456", model="X5000"),
    ]
    dossier = build_dossier_summary(documents)
    assert any(
        item["check"] == "VIN LZGJL4V44PX123456" and item["status"] == "mismatch"
        for item in dossier["checks"]
    )


def test_history_page_renders(tmp_path):
    app = create_app()
    app.config.update(TESTING=True, RESULT_FOLDER=str(tmp_path))
    result = {
        "result_id": "abc123",
        "documents": [{
            "filename": "lease.pdf",
            "document_type": "lease_contract",
            "document_type_label_ru": "Договор лизинга",
            "fields": [{"name": "lessee_iin_bin", "value": "222222222222", "status": "confirmed"}],
            "tables": [],
        }],
        "dossier": {"status": "ok"},
    }
    register_result(tmp_path, result)
    (tmp_path / "abc123.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    response = app.test_client().get("/history")
    assert response.status_code == 200
    assert "222222222222" in response.get_data(as_text=True)
