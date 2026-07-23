from __future__ import annotations

from app.services.document_reader import ReadDocument
from .base import field, find_first, generic_identifiers, normalize_contract_number, valid_contract_number


def parse(document: ReadDocument) -> list[dict]:
    fields: list[dict] = []
    doc_id = find_first(document, patterns=[r'DOC ID\s*([A-Z0-9]+)'], name='doc_id', label_ru='DOC ID')
    if doc_id:
        fields.append(doc_id)
    reg_number = find_first(document, patterns=[r'Рег\.\s*Номер:\s*([0-9/.-]+)'], name='registration_number', label_ru='Регистрационный номер')
    if reg_number:
        fields.append(reg_number)
    signatures = find_first(document, patterns=[r'(?:Подписи|Электронные подписи \(ЭЦП\))\s*(\d+)'], name='signature_count', label_ru='Количество ЭЦП', converter=int)
    if signatures:
        fields.append(signatures)
    fields.extend(generic_identifiers(document))
    return fields
