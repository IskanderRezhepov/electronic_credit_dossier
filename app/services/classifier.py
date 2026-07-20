from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Classification:
    key: str
    label_ru: str
    confidence: float
    matched_keywords: list[str]
    alternatives: list[dict]


DOCUMENT_TYPES = {
    'purchase_contract': {
        'label': 'Договор купли-продажи для последующей передачи в финансовый лизинг',
        'strong': ['договор купли-продажи', 'купли продажи товара', 'для последующей передачи в финансовый лизинг'],
        'support': ['продавец', 'покупатель', 'товар', 'условия поставки'],
    },
    'lease_contract': {
        'label': 'Договор финансового лизинга / заявление о присоединении',
        'strong': ['договор финансового лизинга', 'заявление о присоединении'],
        'support': ['лизингодатель', 'лизингополучатель', 'предмет лизинга', 'авансовый платеж'],
    },
    'acceptance_act': {
        'label': 'Акт приёма-передачи',
        'strong': ['акт приема-передачи', 'акт приёма-передачи', 'қабылдау-өткізу актісі'],
        'support': ['передает', 'принимает', 'итого', 'vin'],
    },
    'payment_schedule': {
        'label': 'График погашения / график платежей',
        'strong': ['график погашения', 'график платежей'],
        'support': ['остаток основного долга', 'дата погашения', 'сумма погашения процентов', 'сумма займа'],
    },
    'addendum': {
        'label': 'Дополнительное соглашение',
        'strong': ['дополнительное соглашение', 'қосымша келісім'],
        'support': ['остаются в неизменном виде', 'дополнить', 'сумма транша', 'к договору финансового лизинга'],
    },
    'signature_receipt': {
        'label': 'Квитанция о подписании',
        'strong': ['квитанция о подписании'],
        'support': ['тип эцп', 'дата подписания', 'подписал', 'doc id'],
    },
}


def _normalize(value: str) -> str:
    value = value.lower().replace('ё', 'е')
    value = re.sub(r'[^a-zа-яәіңғүұқөһ0-9]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def classify(text: str, filename: str = '') -> Classification:
    haystack = _normalize(f'{filename}\n{text}')
    scored: list[tuple[str, float, list[str]]] = []

    for key, config in DOCUMENT_TYPES.items():
        matches: list[str] = []
        score = 0.0
        for keyword in config['strong']:
            if _normalize(keyword) in haystack:
                matches.append(keyword)
                score += 3.0
        for keyword in config['support']:
            if _normalize(keyword) in haystack:
                matches.append(keyword)
                score += 1.0
        # Имя файла — дополнительный, но не решающий сигнал.
        name = filename.lower()
        if key == 'acceptance_act' and ('акт' in name or 'прием' in name):
            score += 1.0
        if key == 'payment_schedule' and ('график' in name or 'ag2' in name):
            score += 1.0
        if key == 'addendum' and ('допик' in name or 'доп' in name):
            score += 1.0
        if key == 'purchase_contract' and ('дкп' in name or 'купли' in name):
            score += 1.0
        scored.append((key, score, matches))

    scored.sort(key=lambda item: item[1], reverse=True)
    best_key, best_score, best_matches = scored[0]
    alternatives = [
        {'key': key, 'label_ru': DOCUMENT_TYPES[key]['label'], 'score': score}
        for key, score, _ in scored[1:3] if score > 0
    ]

    if best_score < 3:
        return Classification('unknown', 'Неизвестный тип документа', 0.0, best_matches, alternatives)

    second_score = scored[1][1] if len(scored) > 1 else 0
    margin = best_score - second_score
    confidence = min(0.99, 0.55 + best_score * 0.05 + max(0, margin) * 0.04)
    return Classification(best_key, DOCUMENT_TYPES[best_key]['label'], round(confidence, 2), best_matches, alternatives)
