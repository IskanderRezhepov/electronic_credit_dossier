from app.services.dossier import build_dossier_summary


def f(name, label, value, status="extracted"):
    return {
        "name": name, "label_ru": label, "value": value, "page": 1,
        "confidence": 0.96, "status": status,
    }


def doc(filename, doc_type, fields):
    return {
        "filename": filename,
        "document_type": doc_type,
        "document_type_label_ru": doc_type,
        "fields": fields,
    }


def test_dossier_matches_purchase_act_and_client():
    documents = [
        doc("дкп.pdf", "purchase_contract", [
            f("purchase_contract_number", "Номер ДКП", "640/BL/15-07"),
            f("total_amount_kzt", "Стоимость", "35750000.00"),
            f("buyer_iin_bin", "Покупатель", "951110350798"),
        ]),
        doc("акт.pdf", "acceptance_act", [
            f("linked_purchase_contract", "Связанный ДКП", "640/BL/15-07"),
            f("act_total_amount_kzt", "Стоимость акта", "35750000"),
            f("buyer_iin_bin", "Покупатель", "951110350798"),
        ]),
    ]
    summary = build_dossier_summary(documents)
    assert summary["counts"]["mismatch"] == 0
    assert summary["counts"]["match"] >= 3
    assert any(c["category"] == "Связи договоров" and c["status"] == "match" for c in summary["checks"])
    assert any(c["category"] == "Суммы" and c["status"] == "match" for c in summary["checks"])


def test_dossier_flags_conflicting_amount_and_identifier():
    documents = [
        doc("a.pdf", "purchase_contract", [
            f("total_amount_kzt", "Стоимость", "1000000"),
            f("buyer_iin_bin", "Покупатель", "111111111111"),
        ]),
        doc("b.pdf", "acceptance_act", [
            f("act_total_amount_kzt", "Стоимость акта", "1100000"),
            f("buyer_iin_bin", "Покупатель", "222222222222"),
        ]),
    ]
    summary = build_dossier_summary(documents)
    assert summary["status"] == "attention"
    assert summary["counts"]["mismatch"] >= 2


def test_candidate_fields_are_not_used_as_facts():
    documents = [
        doc("a.pdf", "purchase_contract", [
            f("buyer_iin_bin", "Покупатель", "111111111111", status="candidate"),
        ])
    ]
    summary = build_dossier_summary(documents)
    assert summary["identities"] == []
