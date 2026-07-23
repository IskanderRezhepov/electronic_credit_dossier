from __future__ import annotations

import re
from copy import deepcopy


ROLE_PRIORITY = {
    # Most specific contractual roles first.
    "principal_iin_bin": 160,
    "borrower_iin_bin": 150,
    "lessee_iin_bin": 150,
    "lessor_iin_bin": 150,
    "seller_iin_bin": 150,
    "buyer_iin_bin": 150,
    "financial_agency_iin_bin": 150,
    "leasing_company_iin_bin": 150,
    "beneficiary_iin_bin": 145,
    "recipient_iin_bin": 120,
    "sender_iin_bin": 120,
    "guarantor_iin_bin": 110,
    "bank_bin": 160,
    "fund_iin_bin": 220,

    "principal_iban": 160,
    "borrower_iban": 150,
    "lessee_iban": 150,
    "lessor_iban": 150,
    "seller_iban": 150,
    "buyer_iban": 150,
    "financial_agency_iban": 150,
    "leasing_company_iban": 150,
    "beneficiary_iban": 145,
    "recipient_iban": 120,
    "sender_iban": 120,
    "guarantor_iban": 110,
    "bank_iban": 160,
}

STATUS_PRIORITY = {
    "corrected": 5,
    "confirmed": 5,
    "extracted": 4,
    "candidate": 2,
    "rejected": 0,
}


def _scalar_role_field(item: dict) -> bool:
    name = str(item.get("name") or "")
    value = item.get("value")
    if isinstance(value, list) or value in (None, ""):
        return False
    return (
        name.endswith("_iban")
        or name.endswith("_iin_bin")
        or name.endswith("_bin")
        or name in {"bank_bin", "bank_iban", "fund_iin_bin"}
    )


def _choose_unique_roles(fields: list[dict]) -> list[dict]:
    best: dict[str, tuple[tuple, dict]] = {}
    passthrough: list[dict] = []

    for item in fields:
        if not _scalar_role_field(item):
            passthrough.append(item)
            continue

        value = str(item.get("value"))
        name = str(item.get("name") or "")
        score = (
            ROLE_PRIORITY.get(name, 0),
            STATUS_PRIORITY.get(str(item.get("status") or ""), 0),
            float(item.get("confidence") or 0),
        )
        current = best.get(value)
        if current is None or score > current[0]:
            best[value] = (score, item)

    return passthrough + [entry[1] for entry in best.values()]


def _used_values(fields: list[dict], tables: list[dict]) -> set[str]:
    values = {
        str(item.get("value"))
        for item in fields
        if not isinstance(item.get("value"), list)
        and item.get("status") not in {"candidate", "rejected"}
        and item.get("value") not in (None, "")
    }
    for table in tables:
        name = table.get("name")
        for row in table.get("rows", []):
            if name == "guarantor_rows":
                for key in ("iin_bin", "guarantee_number"):
                    if row.get(key) not in (None, ""):
                        values.add(str(row[key]))
            elif name == "tranche_rows":
                for key in ("tranche_number", "amount_kzt"):
                    if row.get(key) not in (None, ""):
                        values.add(str(row[key]))
            elif name == "asset_vin_rows":
                if row.get("vin"):
                    values.add(str(row["vin"]))
    return values


def _clean_candidate_lists(fields: list[dict], tables: list[dict]) -> list[dict]:
    used = _used_values(fields, tables)
    result = []
    for item in fields:
        value = item.get("value")
        if isinstance(value, list):
            cleaned = []
            for entry in value:
                text = str(entry)
                numeric = re.sub(r"\D", "", text)
                # Remove values already promoted to a field/table.
                if text in used:
                    continue
                # Also match formatted/unformatted amounts.
                if numeric and any(re.sub(r"\D", "", used_value) == numeric for used_value in used):
                    continue
                cleaned.append(entry)
            item = deepcopy(item)
            item["value"] = cleaned
            if not cleaned:
                continue
        result.append(item)
    return result


def _guard_equipment_tables(fields: list[dict], tables: list[dict]) -> list[dict]:
    document_totals = [
        float(item.get("value"))
        for item in fields
        if item.get("name") in {
            "lease_asset_value_kzt", "purchase_total_kzt",
            "financing_amount_kzt", "total_amount_kzt",
        }
        and isinstance(item.get("value"), (int, float))
        and float(item.get("value")) >= 1_000_000
    ]
    reference_total = max(document_totals) if document_totals else None

    result = deepcopy(tables)
    for table in result:
        if table.get("name") != "asset_vin_rows":
            continue
        rows = []
        for row in table.get("rows", []):
            amount = row.get("total_amount_kzt")
            equipment_type = str(row.get("equipment_type") or "")
            model = str(row.get("model") or "")
            header_like = (
                equipment_type.upper().startswith(("Р/С", "№", "НАИМЕНОВАН"))
                or model.upper().startswith(("Р/С", "№", "НАИМЕНОВАН"))
            )
            tiny_false_amount = (
                isinstance(amount, (int, float))
                and amount in {12, 16}
                and reference_total is not None
            )
            if header_like or tiny_false_amount:
                continue
            rows.append(row)

        table["rows"] = rows
        table["row_count"] = len(rows)
        if not rows:
            table["status"] = "candidate"
            table["notes"] = (
                "Автоматическая строка спецификации отклонена как заголовок "
                "таблицы или процент НДС. Требуется повторное распознавание страницы."
            )
            continue

        summary = table.setdefault("summary", {})
        summary["total_quantity"] = sum(
            int(row.get("quantity") or 0) for row in rows
        ) or None
        summary["total_identified_amount_kzt"] = sum(
            float(row.get("total_amount_kzt") or 0) for row in rows
        ) or None
        summary["unique_vin_count"] = len({
            row.get("vin") for row in rows if row.get("vin")
        })
    return result


def apply_stability_guard(fields: list[dict], tables: list[dict]) -> tuple[list[dict], list[dict]]:
    """Apply only global invariants; do not reinterpret document semantics."""
    prepared = [
        item for item in deepcopy(fields)
        if not (
            item.get("name") in {"borrower_iin_bin", "borrower_bin"}
            and str(item.get("value")) == "970840000277"
        )
    ]
    guarded_fields = _choose_unique_roles(prepared)
    guarded_tables = _guard_equipment_tables(guarded_fields, tables)
    guarded_fields = _clean_candidate_lists(guarded_fields, guarded_tables)
    return guarded_fields, guarded_tables
