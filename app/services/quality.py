from __future__ import annotations

import re


def review_fields(document_type: str, fields: list[dict]) -> list[dict]:
    warnings: list[dict] = []
    by_name = {item['name']: item for item in fields}

    for item in fields:
        value = item.get('value')
        if item.get('confidence', 0) < 0.65:
            warnings.append({'severity': 'medium', 'field': item['label_ru'], 'message': 'Низкая уверенность OCR; требуется сверка с оригиналом.'})
        if item['name'].endswith('_number') and isinstance(value, str):
            if not re.search(r'\d', value) or len(value) < 5:
                warnings.append({'severity': 'high', 'field': item['label_ru'], 'message': f'Подозрительный номер: {value}'})

    if document_type == 'purchase_contract':
        for required in ['purchase_contract_number', 'total_amount_kzt']:
            if required not in by_name:
                warnings.append({'severity': 'high', 'field': required, 'message': 'Ключевое поле не извлечено.'})
    elif document_type == 'acceptance_act':
        if 'act_number' not in by_name:
            warnings.append({'severity': 'high', 'field': 'Номер акта', 'message': 'Номер акта не извлечён; не подставляется случайное слово.'})
        amount = by_name.get('act_total_amount_kzt', {}).get('value')
        try:
            if amount is not None and float(amount) < 10000:
                warnings.append({'severity': 'high', 'field': 'Общая стоимость', 'message': 'Сумма выглядит нереалистично малой и отклонена как возможная OCR-ошибка.'})
        except (TypeError, ValueError):
            pass
    return warnings
