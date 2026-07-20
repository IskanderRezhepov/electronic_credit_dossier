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
        'strong': ['договор купли-продажи', 'договор купли продажи', 'для последующей передачи в финансовый лизинг'],
        'support': ['продавец', 'покупатель', 'товар', 'оборудование', 'условия поставки'],
    },
    'lease_contract': {
        'label': 'Договор финансового лизинга / заявление о присоединении',
        'strong': ['договор финансового лизинга', 'заявление о присоединении', 'договор лизинга'],
        'support': ['лизингодатель', 'лизингополучатель', 'предмет лизинга', 'авансовый платеж'],
    },
    'acceptance_act': {
        'label': 'Акт приёма-передачи',
        'strong': ['акт приема-передачи', 'акт приёма-передачи', 'қабылдау-өткізу актісі', 'приемо-сдаточный акт'],
        'support': ['передает', 'принимает', 'итого', 'vin', 'наименование товара'],
    },
    'payment_schedule': {
        'label': 'График погашения / график платежей',
        'strong': ['график погашения', 'график платежей', 'график погашения основного долга'],
        'support': ['остаток основного долга', 'дата погашения', 'сумма погашения процентов', 'сумма займа', 'сумма транша'],
    },
    'addendum': {
        'label': 'Дополнительное соглашение',
        'strong': ['дополнительное соглашение', 'қосымша келісім'],
        'support': ['остаются в неизменном виде', 'дополнить', 'сумма транша', 'к договору финансового лизинга'],
    },
    'guarantee_contract': {
        'label': 'Договор гарантии / поручительства',
        'strong': ['договор гарантии', 'договор поручительства', 'личная гарантия'],
        'support': ['гарант', 'поручитель', 'обязательства по договору'],
    },
    'invoice': {
        'label': 'Счёт / счёт на оплату / счёт-фактура',
        'strong': ['счет на оплату', 'счёт на оплату', 'счет-фактура', 'счёт-фактура'],
        'support': ['поставщик', 'получатель', 'итого к оплате', 'бин'],
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
    haystack = _normalize(f'{filename}\n{text[:50000]}')
    scored: list[tuple[str, float, list[str]]] = []

    for key, config in DOCUMENT_TYPES.items():
        matches: list[str] = []
        score = 0.0
        for keyword in config['strong']:
            if _normalize(keyword) in haystack:
                matches.append(keyword)
                score += 4.0
        for keyword in config['support']:
            if _normalize(keyword) in haystack:
                matches.append(keyword)
                score += 1.0

        name = filename.lower()
        filename_signals = {
            'acceptance_act': ['акт', 'приема', 'передачи'],
            'payment_schedule': ['график', 'ag2'],
            'addendum': ['допик', 'дополнитель'],
            'purchase_contract': ['дкп', 'купли', 'продажи'],
            'lease_contract': ['дфл', 'дл_', 'лизинг'],
            'guarantee_contract': ['гарант', 'поруч'],
            'invoice': ['счет', 'invoice'],
        }
        if any(token in name for token in filename_signals.get(key, [])):
            score += 1.25
        scored.append((key, score, matches))

    scored.sort(key=lambda item: item[1], reverse=True)
    best_key, best_score, best_matches = scored[0]
    alternatives = [
        {'key': key, 'label_ru': DOCUMENT_TYPES[key]['label'], 'score': round(score, 2)}
        for key, score, _ in scored[1:4] if score > 0
    ]

    if best_score < 4:
        return Classification('unknown', 'Неизвестный тип документа', 0.0, best_matches, alternatives)

    second_score = scored[1][1] if len(scored) > 1 else 0
    margin = best_score - second_score
    confidence = min(0.99, 0.54 + best_score * 0.045 + max(0, margin) * 0.035)
    return Classification(best_key, DOCUMENT_TYPES[best_key]['label'], round(confidence, 2), best_matches, alternatives)
