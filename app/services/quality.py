
from __future__ import annotations

import re


REQUIRED_BY_TYPE = {
    "purchase_contract": [
        ("purchase_contract_number", "Номер договора купли-продажи"),
        ("total_amount_kzt", "Общая стоимость договора"),
    ],
    "lease_contract": [
        ("lease_contract_number", "Номер договора лизинга"),
        ("lease_asset_value_kzt", "Стоимость предмета лизинга"),
    ],
    "acceptance_act": [
        ("act_number", "Номер акта"),
        ("linked_purchase_contract", "Связанный договор купли-продажи"),
    ],
    "payment_schedule": [
        ("lease_contract_number", "Номер договора лизинга"),
        ("loan_amount_kzt", "Сумма займа / транша"),
    ],
    "addendum": [
        ("lease_contract_number", "Номер основного договора"),
    ],
    "credit_line_agreement": [
        ("credit_line_number", "Номер соглашения об открытии КЛ"),
        ("credit_line_amount_kzt", "Сумма кредитной линии"),
    ],
    "cash_pledge_agreement": [
        ("pledge_contract_number", "Номер договора залога"),
        ("pledge_amount_kzt", "Сумма денежного залога"),
    ],
    "subsidy_agreement": [
        ("subsidy_contract_number", "Номер договора субсидирования"),
        ("financing_amount_kzt", "Сумма финансирования"),
    ],
}


def review_fields(document_type: str, fields: list[dict]) -> list[dict]:
    warnings: list[dict] = []
    by_name = {item["name"]: item for item in fields}

    for item in fields:
        value = item.get("value")

        if item.get("confidence", 0) < 0.6 and item.get("status") != "candidate":
            warnings.append(
                {
                    "severity": "medium",
                    "field": item["label_ru"],
                    "message": "Низкая уверенность OCR; требуется сверка с оригиналом.",
                }
            )

        if item["name"].endswith("_number") and isinstance(value, str):
            short_numeric_allowed = item["name"] in {"act_number", "addendum_number"}
            invalid_short = len(value) < 3 and not (
                short_numeric_allowed and re.fullmatch(r"\d{1,2}", value)
            )
            if not re.search(r"\d", value) or invalid_short:
                warnings.append(
                    {
                        "severity": "high",
                        "field": item["label_ru"],
                        "message": f"Подозрительный номер: {value}",
                    }
                )

        if item.get("extraction_method") == "filename":
            warnings.append(
                {
                    "severity": "medium",
                    "field": item["label_ru"],
                    "message": "Значение взято из имени файла и требует подтверждения по тексту.",
                }
            )

    for required_name, required_label in REQUIRED_BY_TYPE.get(document_type, []):
        present = required_name in by_name

        # A PDF package can contain several acceptance acts. In that case
        # act_numbers is the correct document-level result.
        if (
            document_type == "acceptance_act"
            and required_name == "act_number"
            and ("act_numbers" in by_name or "act_package_detected" in by_name)
        ):
            present = True

        if not present:
            warnings.append(
                {
                    "severity": "high",
                    "field": required_label,
                    "message": "Ключевое поле не извлечено.",
                }
            )

    if document_type == "payment_schedule":
        amount = by_name.get("loan_amount_kzt")
        if amount and amount.get("value_type") == "calculated":
            warnings.append(
                {
                    "severity": "medium",
                    "field": "Сумма займа / транша",
                    "message": (
                        "Сумма рассчитана из первой строки графика и должна быть "
                        "подтверждена по договору или титульной части графика."
                    ),
                }
            )

    if document_type == "acceptance_act":
        amount = by_name.get("act_total_amount_kzt", {}).get("value")
        try:
            if amount is not None and float(amount) < 10000:
                warnings.append(
                    {
                        "severity": "high",
                        "field": "Общая стоимость по акту",
                        "message": "Сумма выглядит нереалистично малой.",
                    }
                )
        except (TypeError, ValueError):
            pass

        groups = by_name.get("asset_identifier_groups", {}).get("value")
        if isinstance(groups, dict) and len(groups) > 1:
            warnings.append(
                {
                    "severity": "medium",
                    "field": "VIN / номера шасси",
                    "message": (
                        "Найдены несколько групп 17-значных кодов. "
                        "Расчётное количество основано на крупнейшей группе и требует сверки."
                    ),
                }
            )

    return warnings
