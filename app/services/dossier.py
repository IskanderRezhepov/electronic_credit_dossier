from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import re


ROLE_FIELD_NAMES = {
    "lessee_iin_bin": "Лизингополучатель",
    "borrower_iin_bin": "Заёмщик",
    "buyer_iin_bin": "Покупатель",
    "seller_iin_bin": "Продавец",
    "lessor_iin_bin": "Лизингодатель",
    "guarantor_iin_bin": "Гарант",
    "principal_iin_bin": "Принципал",
    "beneficiary_iin_bin": "Бенефициар",
    "pledger_iin_bin": "Залогодатель",
    "subsidy_recipient_bin": "Получатель субсидии",
}

CONTRACT_PRIMARY_FIELDS = {
    "lease_contract_number": "Договор финансового лизинга",
    "purchase_contract_number": "Договор купли-продажи",
    "credit_line_number": "Соглашение о кредитной линии",
    "subsidy_contract_number": "Договор субсидирования",
    "pledge_contract_number": "Договор залога",
}

CONTRACT_LINK_FIELDS = {
    "linked_purchase_contract": "purchase_contract_number",
    "linked_lease_contract": "lease_contract_number",
    "main_lease_contract_number": "lease_contract_number",
    "related_lease_contract_number": "lease_contract_number",
    "linked_subsidy_contract": "subsidy_contract_number",
}

AMOUNT_GROUPS = {
    "Стоимость предмета / договора / акта": {
        "purchase": {"total_amount_kzt"},
        "lease": {"lease_asset_value_kzt"},
        "act": {"act_total_amount_kzt"},
    },
    "Сумма финансирования / транша": {
        "lease": {"financing_amount_kzt", "loan_amount_kzt"},
        "schedule": {"loan_amount_kzt", "principal_total_kzt"},
    },
}


def _normalize_number(value: object) -> str:
    text = str(value or "").upper().strip()
    text = text.replace("\\", "/").replace("|", "/").replace("—", "-").replace("–", "-")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"/+", "/", text)
    return text.strip(".,;:")


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _field_index(document: dict) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = defaultdict(list)
    for item in document.get("fields", []):
        if item.get("status") in {"candidate", "rejected"}:
            continue
        index[item.get("name", "")].append(item)
    return index


def _evidence(document: dict, item: dict) -> dict:
    return {
        "filename": document.get("filename"),
        "document_type": document.get("document_type"),
        "document_type_label_ru": document.get("document_type_label_ru"),
        "field": item.get("label_ru"),
        "value": item.get("value"),
        "page": item.get("page"),
        "confidence": item.get("confidence"),
    }


def _identity_summary(documents: list[dict]) -> tuple[list[dict], list[dict]]:
    roles: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    checks: list[dict] = []

    for document in documents:
        for item in document.get("fields", []):
            name = item.get("name")
            if name not in ROLE_FIELD_NAMES or item.get("status") in {"candidate", "rejected"}:
                continue
            value = _normalize_number(item.get("value"))
            if re.fullmatch(r"\d{12}", value):
                roles[name][value].append(_evidence(document, item))

    summaries = []
    for role_name, values in roles.items():
        all_evidence = [e for evidence in values.values() for e in evidence]
        status = "consistent" if len(values) == 1 else "conflict"
        summaries.append({
            "role": role_name,
            "label_ru": ROLE_FIELD_NAMES[role_name],
            "values": sorted(values),
            "status": status,
            "evidence": all_evidence,
        })
        if len(all_evidence) >= 2:
            checks.append({
                "category": "ИИН/БИН",
                "check": f"{ROLE_FIELD_NAMES[role_name]}: единый идентификатор",
                "status": "match" if status == "consistent" else "mismatch",
                "message": (
                    f"Во всех документах найден один идентификатор: {next(iter(values))}."
                    if status == "consistent"
                    else "Для одной роли найдены разные ИИН/БИН: " + ", ".join(sorted(values))
                ),
                "evidence": all_evidence,
            })
    return summaries, checks


def _contract_checks(documents: list[dict]) -> tuple[list[dict], list[dict]]:
    primaries: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    links: list[tuple[str, str, str, dict]] = []

    for document in documents:
        index = _field_index(document)
        for field_name in CONTRACT_PRIMARY_FIELDS:
            for item in index.get(field_name, []):
                value = _normalize_number(item.get("value"))
                if value:
                    primaries[field_name][value].append(_evidence(document, item))
        for link_name, target_name in CONTRACT_LINK_FIELDS.items():
            for item in index.get(link_name, []):
                value = _normalize_number(item.get("value"))
                if value:
                    links.append((link_name, target_name, value, _evidence(document, item)))

    registry = []
    for field_name, values in primaries.items():
        registry.append({
            "field": field_name,
            "label_ru": CONTRACT_PRIMARY_FIELDS[field_name],
            "values": sorted(values),
            "evidence": [e for group in values.values() for e in group],
        })

    checks = []
    for link_name, target_name, value, evidence in links:
        known = primaries.get(target_name, {})
        if not known:
            status = "not_enough_data"
            message = f"В досье нет основного документа для проверки номера {value}."
        elif value in known:
            status = "match"
            message = f"Связанный номер {value} совпадает с основным документом."
        else:
            status = "mismatch"
            message = f"Связанный номер {value} не совпадает с найденными основными номерами: {', '.join(sorted(known))}."
        checks.append({
            "category": "Связи договоров",
            "check": f"{link_name} → {CONTRACT_PRIMARY_FIELDS.get(target_name, target_name)}",
            "status": status,
            "message": message,
            "evidence": [evidence] + [e for group in known.values() for e in group],
        })
    return registry, checks


def _amount_checks(documents: list[dict]) -> list[dict]:
    checks: list[dict] = []
    for group_label, role_fields in AMOUNT_GROUPS.items():
        found: list[tuple[str, Decimal, dict]] = []
        for document in documents:
            index = _field_index(document)
            for role, names in role_fields.items():
                for name in names:
                    for item in index.get(name, []):
                        value = _decimal(item.get("value"))
                        if value is not None and value > 0:
                            found.append((role, value, _evidence(document, item)))
        if len(found) < 2:
            continue

        unique = sorted({value for _, value, _ in found})
        # One tenge tolerance covers harmless decimal/rounding differences.
        spread = max(unique) - min(unique)
        status = "match" if spread <= Decimal("1") else "mismatch"
        checks.append({
            "category": "Суммы",
            "check": group_label,
            "status": status,
            "message": (
                f"Суммы совпадают: {unique[0]:,.2f} тенге."
                if status == "match"
                else "Найдены разные суммы: " + ", ".join(f"{value:,.2f}" for value in unique) + " тенге."
            ),
            "evidence": [evidence for _, _, evidence in found],
        })
    return checks


def _completeness(documents: list[dict]) -> list[dict]:
    present = {doc.get("document_type") for doc in documents}
    expected = [
        ("lease_contract", "Договор финансового лизинга"),
        ("purchase_contract", "Договор купли-продажи"),
        ("acceptance_act", "Акт приёма-передачи"),
        ("payment_schedule", "График платежей"),
    ]
    return [
        {
            "document_type": key,
            "label_ru": label,
            "present": key in present,
        }
        for key, label in expected
    ]


def build_dossier_summary(documents: list[dict]) -> dict:
    identities, identity_checks = _identity_summary(documents)
    contracts, contract_checks = _contract_checks(documents)
    checks = identity_checks + contract_checks + _amount_checks(documents)

    counts = {
        "match": sum(check["status"] == "match" for check in checks),
        "mismatch": sum(check["status"] == "mismatch" for check in checks),
        "not_enough_data": sum(check["status"] == "not_enough_data" for check in checks),
    }
    return {
        "identities": identities,
        "contracts": contracts,
        "checks": checks,
        "completeness": _completeness(documents),
        "counts": counts,
        "status": "attention" if counts["mismatch"] else ("ok" if checks else "insufficient"),
    }
