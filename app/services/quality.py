from __future__ import annotations

import re


REQUIRED_BY_TYPE = {
    'purchase_contract': ['purchase_contract_number', 'total_amount_kzt'],
    'lease_contract': ['lease_contract_number', 'lease_asset_value_kzt'],
    'acceptance_act': ['act_number', 'linked_purchase_contract'],
    'payment_schedule': ['lease_contract_number', 'loan_amount_kzt'],
    'addendum': ['lease_contract_number'],
}


def review_fields(document_type: str, fields: list[dict]) -> list[dict]:
    warnings: list[dict] = []
    by_name = {item['name']: item for item in fields}

    for item in fields:
        value = item.get('value')
        if item.get('confidence', 0) < 0.6 and item.get('status') != 'candidate':
            warnings.append({'severity': 'medium', 'field': item['label_ru'], 'message': 'Низкая уверенность OCR; требуется сверка с оригиналом.'})
        if item['name'].endswith('_number') and isinstance(value, str):
            if not re.search(r'\d', value) or len(value) < 3:
                warnings.append({'severity': 'high', 'field': item['label_ru'], 'message': f'Подозрительный номер: {value}'})
        if item.get('extraction_method') == 'filename':
            warnings.append({'severity': 'medium', 'field': item['label_ru'], 'message': 'Значение взято из имени файла, а не подтверждено текстом документа.'})

    for required in REQUIRED_BY_TYPE.get(document_type, []):
        if required not in by_name:
            warnings.append({'severity': 'high', 'field': required, 'message': 'Ключевое поле не извлечено.'})

    if document_type == 'acceptance_act':
        amount = by_name.get('act_total_amount_kzt', {}).get('value')
        try:
            if amount is not None and float(amount) < 10000:
                warnings.append({'severity': 'high', 'field': 'Общая стоимость', 'message': 'Сумма выглядит нереалистично малой.'})
        except (TypeError, ValueError):
            pass
        if 'asset_vins' in by_name and 'asset_count_calculated' not in by_name:
            warnings.append({'severity': 'medium', 'field': 'VIN', 'message': 'VIN найдены, но количество имущества не рассчитано.'})
    return warnings
