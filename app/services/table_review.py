from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation


NUMERIC_KEYS = {
    "principal", "interest", "payment", "balance",
    "quantity", "unit_price_kzt", "total_amount_kzt", "page",
}
INTEGER_KEYS = {"quantity", "page"}
ALLOWED_TABLE_STATUSES = {"extracted", "candidate", "confirmed", "corrected"}


def _get(form_data, key: str, default=""):
    return form_data.get(key, default)


def _has(form_data, key: str) -> bool:
    return key in form_data


def _parse_cell(raw, key: str):
    text = str(raw or "").strip()
    if not text:
        return None
    if key == "vin":
        return "".join(text.upper().split())
    if key in NUMERIC_KEYS:
        compact = text.replace("\u00a0", "").replace(" ", "").replace(",", ".")
        try:
            number = Decimal(compact)
        except InvalidOperation:
            return text
        if key in INTEGER_KEYS and number == number.to_integral_value():
            return int(number)
        return float(number)
    return text


def _date_sort_key(value):
    text = str(value or "")
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return datetime.max


def _row_has_values(row: dict, columns: list[dict]) -> bool:
    return any(row.get(column.get("key")) not in (None, "") for column in columns)


def _equipment_summary(rows: list[dict]) -> dict:
    quantities = [row.get("quantity") for row in rows if isinstance(row.get("quantity"), int)]
    vins = {str(row.get("vin")).upper() for row in rows if row.get("vin")}
    by_type: dict[str, int] = {}
    prices = []
    totals = []
    for row in rows:
        label = str(row.get("equipment_type") or "Не определено")
        quantity = row.get("quantity") if isinstance(row.get("quantity"), int) else 1
        by_type[label] = by_type.get(label, 0) + quantity
        if isinstance(row.get("unit_price_kzt"), (int, float)):
            prices.append(float(row["unit_price_kzt"]))
        if isinstance(row.get("total_amount_kzt"), (int, float)):
            totals.append(float(row["total_amount_kzt"]))
    return {
        "total_quantity": sum(quantities) if quantities else (len(vins) or None),
        "unique_vin_count": len(vins),
        "equipment_by_type": dict(sorted(by_type.items())),
        "min_unit_price_kzt": min(prices) if prices else None,
        "max_unit_price_kzt": max(prices) if prices else None,
        "total_identified_amount_kzt": sum(totals) if totals else None,
    }


def _schedule_summary(rows: list[dict]) -> dict:
    dated = sorted((row for row in rows if row.get("date")), key=lambda row: _date_sort_key(row.get("date")))
    return {
        "principal_sum_kzt": sum(float(row.get("principal") or 0) for row in rows),
        "interest_sum_kzt": sum(float(row.get("interest") or 0) for row in rows),
        "payment_sum_kzt": sum(float(row.get("payment") or 0) for row in rows),
        "first_payment_date": dated[0].get("date") if dated else None,
        "last_payment_date": dated[-1].get("date") if dated else None,
    }


def _row_checks(table_name: str, rows: list[dict]) -> list[dict]:
    checks = []
    if table_name == "asset_vin_rows":
        for index, row in enumerate(rows):
            quantity = row.get("quantity")
            unit = row.get("unit_price_kzt")
            total = row.get("total_amount_kzt")
            if all(isinstance(value, (int, float)) for value in (quantity, unit, total)):
                expected = float(quantity) * float(unit)
                difference = round(float(total) - expected, 2)
                checks.append({
                    "row": index + 1,
                    "check": "Количество × цена единицы",
                    "valid": abs(difference) <= 1.0,
                    "difference_kzt": difference,
                    "message": "Расчёт сходится." if abs(difference) <= 1.0 else f"Расхождение {difference:,.2f} тенге.",
                })
    elif table_name == "payment_schedule_rows":
        for index, row in enumerate(rows):
            principal = row.get("principal")
            interest = row.get("interest")
            payment = row.get("payment")
            if all(isinstance(value, (int, float)) for value in (principal, interest, payment)):
                expected = float(principal) + float(interest)
                difference = round(float(payment) - expected, 2)
                checks.append({
                    "row": index + 1,
                    "check": "Платёж = основной долг + вознаграждение",
                    "valid": abs(difference) <= 1.0,
                    "difference_kzt": difference,
                    "message": "Расчёт сходится." if abs(difference) <= 1.0 else f"Расхождение {difference:,.2f} тенге.",
                })
    return checks


def _recalculate(table: dict) -> None:
    rows = table.get("rows", [])
    name = table.get("name")
    if name == "asset_vin_rows":
        table["summary"] = _equipment_summary(rows)
    elif name == "payment_schedule_rows":
        rows.sort(key=lambda row: _date_sort_key(row.get("date")))
        table["summary"] = _schedule_summary(rows)
    table["row_checks"] = _row_checks(name, rows)
    table["row_count"] = len(rows)


def apply_table_review(result: dict, form_data, now: str) -> tuple[dict, int]:
    """Edit, delete and add rows in structured tables submitted from results.html."""
    updated = deepcopy(result)
    changes = 0

    for doc_index, document in enumerate(updated.get("documents", [])):
        for table_index, table in enumerate(document.get("tables", [])):
            columns = [column for column in table.get("columns", []) if column.get("key")]
            if not columns:
                continue
            prefix = f"table_{doc_index}_{table_index}"
            original_rows = table.get("rows", [])
            kept_rows = []

            for row_index, original_row in enumerate(original_rows):
                delete_key = f"{prefix}_row_{row_index}_delete"
                if _has(form_data, delete_key):
                    table.setdefault("deleted_rows", []).append({
                        "row": deepcopy(original_row),
                        "deleted_at": now,
                        "review_source": "manual_table_delete",
                    })
                    changes += 1
                    continue

                row = deepcopy(original_row)
                row_changed = False
                for column in columns:
                    key = column["key"]
                    input_key = f"{prefix}_row_{row_index}_{key}"
                    if not _has(form_data, input_key):
                        continue
                    parsed = _parse_cell(_get(form_data, input_key), key)
                    if parsed != row.get(key):
                        row_changed = True
                        row[key] = parsed
                if row_changed:
                    row.setdefault("original_row", deepcopy(original_row))
                    row["reviewed_at"] = now
                    row["review_source"] = "manual_table_edit"
                    row["status"] = "corrected"
                    changes += 1
                kept_rows.append(row)

            # Browser sends a monotonically increasing new-row index in a hidden input.
            try:
                new_count = max(0, min(int(_get(form_data, f"{prefix}_new_count", 0) or 0), 100))
            except (TypeError, ValueError):
                new_count = 0
            for new_index in range(new_count):
                row = {}
                for column in columns:
                    key = column["key"]
                    row[key] = _parse_cell(_get(form_data, f"{prefix}_new_{new_index}_{key}"), key)
                if not _row_has_values(row, columns):
                    continue
                row.update({
                    "reviewed_at": now,
                    "review_source": "manual_table_add",
                    "status": "confirmed",
                    "source_method": "manual",
                    "manual": True,
                })
                kept_rows.append(row)
                changes += 1

            requested_status = str(_get(form_data, f"{prefix}_status", table.get("status", "candidate")))
            if requested_status in ALLOWED_TABLE_STATUSES and requested_status != table.get("status"):
                table.setdefault("original_status", table.get("status"))
                table["status"] = requested_status
                changes += 1

            if changes or kept_rows != original_rows:
                table["rows"] = kept_rows
                table["reviewed_at"] = now
                table["review_source"] = "manual_table"
                if table.get("status") == "confirmed":
                    table["confidence"] = 1.0
                _recalculate(table)

    return updated, changes
