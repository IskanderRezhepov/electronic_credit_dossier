from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Iterable


AMOUNT_FIELD_NAMES = {
    "lease_asset_value_kzt": "Стоимость предмета лизинга",
    "purchase_total_kzt": "Сумма договора купли-продажи",
    "total_amount_kzt": "Общая сумма документа",
    "act_total_amount_kzt": "Сумма акта",
    "financing_amount_kzt": "Сумма финансирования",
    "loan_amount_kzt": "Сумма займа / транша",
    "principal_total_kzt": "Основной долг по графику",
}


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _usable_fields(document: dict) -> Iterable[dict]:
    for item in document.get("fields", []):
        if item.get("status") in {"candidate", "rejected"}:
            continue
        validation = item.get("validation") or {}
        if validation and not validation.get("valid") and item.get("status") not in {"confirmed", "corrected"}:
            continue
        yield item


def _field_amounts(document: dict) -> list[tuple[str, Decimal, dict]]:
    values = []
    for item in _usable_fields(document):
        name = item.get("name")
        if name not in AMOUNT_FIELD_NAMES:
            continue
        value = _decimal(item.get("value"))
        if value is None or value <= 0:
            continue
        values.append((name, value, {
            "filename": document.get("filename"),
            "document_type": document.get("document_type"),
            "document_type_label_ru": document.get("document_type_label_ru"),
            "field": item.get("label_ru") or AMOUNT_FIELD_NAMES[name],
            "value": item.get("value"),
            "page": item.get("page"),
            "confidence": item.get("confidence"),
        }))
    return values


def _table_evidence(document: dict, label: str, value: Decimal, page: int | None = None) -> dict:
    return {
        "filename": document.get("filename"),
        "document_type": document.get("document_type"),
        "document_type_label_ru": document.get("document_type_label_ru"),
        "field": label,
        "value": float(value),
        "page": page,
        "confidence": None,
    }


def _compare(label: str, left_label: str, left: Decimal, right_label: str, right: Decimal,
             evidence: list[dict], tolerance: Decimal = Decimal("1")) -> dict:
    difference = right - left
    status = "match" if abs(difference) <= tolerance else "mismatch"
    return {
        "category": "Арифметика",
        "check": label,
        "status": status,
        "severity": "critical" if status == "mismatch" else "info",
        "difference_kzt": float(difference),
        "message": (
            f"{left_label} и {right_label} совпадают: {_money(left)} тенге."
            if status == "match"
            else f"{left_label}: {_money(left)}; {right_label}: {_money(right)}. "
                 f"Расхождение: {_money(difference)} тенге."
        ),
        "evidence": evidence,
    }


def _equipment_checks(document: dict, table: dict, field_amounts: list[tuple[str, Decimal, dict]]) -> list[dict]:
    rows = [row for row in table.get("rows", []) if row.get("status") != "rejected"]
    checks: list[dict] = []
    stated_total = Decimal("0")
    inferred_total = Decimal("0")
    stated_count = 0
    inferred_count = 0

    for row_index, row in enumerate(rows, start=1):
        quantity = _decimal(row.get("quantity"))
        unit = _decimal(row.get("unit_price_kzt"))
        total = _decimal(row.get("total_amount_kzt"))
        if total is not None:
            stated_total += total
            stated_count += 1
        if quantity is not None and unit is not None:
            calculated = quantity * unit
            inferred_total += calculated
            inferred_count += 1
            if total is not None:
                evidence = [_table_evidence(document, f"Строка техники {row_index}: рассчитано", calculated, row.get("page")),
                            _table_evidence(document, f"Строка техники {row_index}: указано", total, row.get("page"))]
                checks.append(_compare(
                    f"Техника, строка {row_index}: количество × цена",
                    "Расчёт", calculated, "Указанная сумма", total, evidence,
                ))

    effective_total = stated_total if stated_count else inferred_total if inferred_count else None
    if effective_total is not None:
        table_label = "Сумма строк спецификации"
        table_evidence = _table_evidence(document, table_label, effective_total)
        relevant_names = {
            "lease_asset_value_kzt", "purchase_total_kzt", "total_amount_kzt",
            "act_total_amount_kzt", "financing_amount_kzt",
        }
        for name, amount, evidence in field_amounts:
            if name not in relevant_names:
                continue
            checks.append(_compare(
                f"Спецификация ↔ {AMOUNT_FIELD_NAMES[name]}",
                table_label, effective_total, AMOUNT_FIELD_NAMES[name], amount,
                [table_evidence, evidence],
            ))
    return checks


def _schedule_checks(document: dict, table: dict, field_amounts: list[tuple[str, Decimal, dict]]) -> list[dict]:
    rows = [row for row in table.get("rows", []) if row.get("status") != "rejected"]
    checks: list[dict] = []
    principal_sum = sum((_decimal(row.get("principal")) or Decimal("0")) for row in rows)
    interest_sum = sum((_decimal(row.get("interest")) or Decimal("0")) for row in rows)
    payment_sum = sum((_decimal(row.get("payment")) or Decimal("0")) for row in rows)

    if principal_sum > 0 and payment_sum > 0:
        expected = principal_sum + interest_sum
        checks.append(_compare(
            "График: сумма платежей",
            "Основной долг + вознаграждение", expected,
            "Сумма платежей", payment_sum,
            [_table_evidence(document, "Основной долг + вознаграждение", expected),
             _table_evidence(document, "Сумма платежей", payment_sum)],
        ))

    # Balance-chain check: previous balance minus current principal should equal current balance.
    previous_balance: Decimal | None = None
    for row_index, row in enumerate(rows, start=1):
        balance = _decimal(row.get("balance"))
        principal = _decimal(row.get("principal"))
        if previous_balance is not None and principal is not None and balance is not None:
            expected_balance = previous_balance - principal
            checks.append(_compare(
                f"График, строка {row_index}: остаток долга",
                "Расчётный остаток", expected_balance,
                "Указанный остаток", balance,
                [_table_evidence(document, f"Строка {row_index}: расчётный остаток", expected_balance, row.get("page")),
                 _table_evidence(document, f"Строка {row_index}: указанный остаток", balance, row.get("page"))],
            ))
        if balance is not None:
            previous_balance = balance

    for name, amount, evidence in field_amounts:
        if name not in {"loan_amount_kzt", "financing_amount_kzt", "principal_total_kzt"}:
            continue
        if principal_sum > 0:
            checks.append(_compare(
                f"Основной долг графика ↔ {AMOUNT_FIELD_NAMES[name]}",
                "Сумма основного долга в графике", principal_sum,
                AMOUNT_FIELD_NAMES[name], amount,
                [_table_evidence(document, "Сумма основного долга в графике", principal_sum), evidence],
            ))
    return checks


def _cross_document_equipment_totals(documents: list[dict]) -> list[dict]:
    totals: list[tuple[Decimal, dict]] = []
    for document in documents:
        for table in document.get("tables", []):
            if table.get("name") != "asset_vin_rows" or table.get("status") == "rejected":
                continue
            total = Decimal("0")
            count = 0
            for row in table.get("rows", []):
                value = _decimal(row.get("total_amount_kzt"))
                if value is None:
                    quantity = _decimal(row.get("quantity"))
                    unit = _decimal(row.get("unit_price_kzt"))
                    value = quantity * unit if quantity is not None and unit is not None else None
                if value is not None:
                    total += value
                    count += 1
            if count:
                totals.append((total, _table_evidence(document, "Сумма спецификации", total)))

    if len(totals) < 2:
        return []
    values = [value for value, _ in totals]
    spread = max(values) - min(values)
    status = "match" if spread <= Decimal("1") else "mismatch"
    return [{
        "category": "Арифметика",
        "check": "Стоимость техники между документами",
        "status": status,
        "severity": "critical" if status == "mismatch" else "info",
        "difference_kzt": float(spread),
        "message": (
            f"Стоимость техники совпадает: {_money(values[0])} тенге."
            if status == "match"
            else "Стоимость техники различается: " + "; ".join(
                f"{evidence['filename']}: {_money(value)}" for value, evidence in totals
            ) + f". Максимальное расхождение: {_money(spread)} тенге."
        ),
        "evidence": [evidence for _, evidence in totals],
    }]


def build_financial_checks(documents: list[dict]) -> tuple[list[dict], dict]:
    checks: list[dict] = []
    for document in documents:
        field_amounts = _field_amounts(document)
        for table in document.get("tables", []):
            if table.get("status") == "rejected":
                continue
            if table.get("name") == "asset_vin_rows":
                checks.extend(_equipment_checks(document, table, field_amounts))
            elif table.get("name") == "payment_schedule_rows":
                checks.extend(_schedule_checks(document, table, field_amounts))
    checks.extend(_cross_document_equipment_totals(documents))

    summary = {
        "total": len(checks),
        "match": sum(check.get("status") == "match" for check in checks),
        "mismatch": sum(check.get("status") == "mismatch" for check in checks),
        "critical": sum(check.get("severity") == "critical" for check in checks),
        "largest_difference_kzt": max(
            (abs(float(check.get("difference_kzt") or 0)) for check in checks),
            default=0.0,
        ),
    }
    return checks, summary
