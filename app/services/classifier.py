
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Classification:
    key: str
    label_ru: str
    confidence: float
    matched_keywords: list[str]


DOCUMENT_TYPES = {
    "purchase_contract": {
        "label": "Договор купли-продажи для последующей передачи в финансовый лизинг",
        "keywords": [
            "договор купли-продажи",
            "для последующей передачи в финансовый лизинг",
            "продавец продает",
            "покупатель покупает",
        ],
    },
    "lease_contract": {
        "label": "Договор финансового лизинга / заявление о присоединении",
        "keywords": [
            "договор финансового лизинга",
            "заявление о присоединении",
            "лизингодатель",
            "лизингополучатель",
        ],
    },
    "acceptance_act": {
        "label": "Акт приёма-передачи",
        "keywords": [
            "акт приема-передачи",
            "қабылдау-өткізу актісі",
            "продавец передает",
            "покупатель принимает",
        ],
    },
    "payment_schedule": {
        "label": "График погашения / график платежей",
        "keywords": [
            "график погашения",
            "дата погашения основного долга",
            "остаток основного долга",
            "сумма погашения процентов",
        ],
    },
    "addendum": {
        "label": "Дополнительное соглашение",
        "keywords": [
            "дополнительное соглашение",
            "қосымша келісім",
            "дополнить абзацем",
            "остаются в неизменном виде",
        ],
    },
    "signature_receipt": {
        "label": "Квитанция о подписании",
        "keywords": [
            "квитанция о подписании",
            "тип эцп",
            "дата подписания",
            "подписал(а)",
        ],
    },
}


def classify(text: str, filename: str = "") -> Classification:
    haystack = f"{filename}\n{text}".lower()
    best_key = "unknown"
    best_hits: list[str] = []

    for key, config in DOCUMENT_TYPES.items():
        hits = [keyword for keyword in config["keywords"] if keyword in haystack]
        if len(hits) > len(best_hits):
            best_key = key
            best_hits = hits

    if best_key == "unknown" or not best_hits:
        return Classification(
            key="unknown",
            label_ru="Неизвестный тип документа",
            confidence=0.0,
            matched_keywords=[],
        )

    keyword_count = len(DOCUMENT_TYPES[best_key]["keywords"])
    confidence = min(1.0, len(best_hits) / max(2, keyword_count - 1))

    return Classification(
        key=best_key,
        label_ru=DOCUMENT_TYPES[best_key]["label"],
        confidence=round(confidence, 2),
        matched_keywords=best_hits,
    )
