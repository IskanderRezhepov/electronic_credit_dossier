
from __future__ import annotations

import re
from decimal import Decimal

from app.services.document_reader import ReadDocument
from app.services.text_utils import parse_money
from .base import common_fields, field, find_all_regex, find_first
from .generic import parse as parse_generic


def parse_purchase_contract(document: ReadDocument) -> list[dict]:
    fields = parse_generic(document)
    definitions = [
        (
            [
                r"ДОГОВОР купли-продажи(?: товара)?.{0,180}?№\s*([A-ZА-Я0-9/_-]+)",
                r"№\s*([A-ZА-Я0-9/_-]+)\s+.*?ДОГОВОР купли-продажи",
            ],
            "purchase_contract_number",
            "Номер договора купли-продажи",
            None,
        ),
        (
            [
                r"Общая стоимость(?: настоящего)? Договора составляет\s*([\d\s]+[,.]\d{2})",
                r"Всего по настоящему Договору на сумму:\s*([\d\s]+[,.]\d{2})",
            ],
            "total_amount_kzt",
            "Общая стоимость договора, тенге",
            parse_money,
        ),
        (
            [r"в том числе НДС\s*(\d+)%"],
            "vat_percent",
            "НДС, %",
            int,
        ),
        (
            [
                r"поставку Товара в течение\s*(\d+)\s*\([^)]*\)\s*рабочих дней",
                r"срок поставки.{0,80}?(\d+)\s*рабоч",
            ],
            "delivery_term_workdays",
            "Срок поставки, рабочих дней",
            int,
        ),
        (
            [r"по адресу:\s*([^.\n]{15,180})"],
            "delivery_address",
            "Место поставки",
            None,
        ),
        (
            [r"в течение\s*(\d+)\s*\([^)]*\)\s*месяцев"],
            "warranty_months",
            "Гарантия, месяцев",
            int,
        ),
        (
            [r"или\s*([\d\s]+)\s*\([^)]*\)\s*км"],
            "warranty_km",
            "Гарантия, км",
            lambda value: int(re.sub(r"\s+", "", value)),
        ),
        (
            [r"пеню в размере\s*([\d,.]+)%"],
            "penalty_percent_daily",
            "Пеня, % в день",
            lambda value: float(value.replace(",", ".")),
        ),
    ]

    for patterns, name, label, converter in definitions:
        item = find_first(
            document,
            patterns=patterns,
            name=name,
            label_ru=label,
            converter=converter,
        )
        if item:
            fields.append(item)

    return fields


def parse_acceptance_act(document: ReadDocument) -> list[dict]:
    fields = parse_generic(document)
    definitions = [
        (
            [
                r"Акт приема-передачи\s*№?\s*([A-ZА-Я0-9/_-]+)",
                r"ҚАБЫЛДАУ-ӨТКІЗУ АКТІСІ.*?№\s*([A-ZА-Я0-9/_-]+)",
            ],
            "act_number",
            "Номер акта",
            None,
        ),
        (
            [
                r"к Договору купли-продажи.*?№\s*([A-ZА-Я0-9/_-]+)",
                r"Договору купли-продажи\s*№\s*([A-ZА-Я0-9/_-]+)",
            ],
            "linked_purchase_contract",
            "Связанный договор купли-продажи",
            None,
        ),
        (
            [
                r"Итого:\s*(\d+)\s+([\d\s]+[,.]\d{2})",
                r"Барлығы\s*/\s*Итого:\s*(\d+)\s+([\d\s]+[,.]\d{2})",
            ],
            "act_total_raw",
            "Итог акта",
            None,
        ),
        (
            [
                r"Общая стоимость,\s*(?:в )?KZT[^0-9]{0,60}([\d\s]+[,.]\d{2})",
                r"Итого:\s*([\d\s]+[,.]\d{2})",
            ],
            "act_total_amount_kzt",
            "Общая стоимость по акту, тенге",
            parse_money,
        ),
    ]

    for patterns, name, label, converter in definitions:
        item = find_first(
            document,
            patterns=patterns,
            name=name,
            label_ru=label,
            converter=converter,
        )
        if item:
            fields.append(item)

    vins = find_all_regex(document, r"\b[A-HJ-NPR-Z0-9]{17}\b")
    if vins:
        fields.append(
            field(
                name="asset_vins",
                label_ru="VIN по акту",
                value=vins,
                page=None,
                quote=None,
                confidence=0.72 if document.used_ocr else 0.93,
                extraction_method="mixed" if document.used_ocr else "digital",
            )
        )
        fields.append(
            field(
                name="asset_count_calculated",
                label_ru="Количество единиц по VIN",
                value=len(vins),
                page=None,
                quote="Количество уникальных VIN",
                confidence=0.88,
                extraction_method="calculated",
                value_type="calculated",
            )
        )

    return fields


def parse_payment_schedule(document: ReadDocument) -> list[dict]:
    fields = parse_generic(document)
    definitions = [
        (
            [
                r"к Договору финансового лизинга\s*№\s*([A-ZА-Я0-9/_-]+)",
                r"№\s*([A-ZА-Я0-9/_-]+)\s*от\s*\d{1,2}\s*октября",
            ],
            "lease_contract_number",
            "Номер договора лизинга",
            None,
        ),
        (
            [r"Сумма займа:\s*([\d\s]+(?:[,.]\d{2})?)"],
            "loan_amount_kzt",
            "Сумма займа, тенге",
            parse_money,
        ),
        (
            [r"Дата выдачи:\s*(\d{2}\.\d{2}\.\d{2,4})"],
            "issue_date",
            "Дата выдачи",
            None,
        ),
        (
            [r"Дата погашения займа:\s*(\d{2}\.\d{2}\.\d{2,4})"],
            "maturity_date",
            "Дата погашения",
            None,
        ),
        (
            [r"Ставка вознаграждения.*?(\d{1,2}[,.]\d+)%"],
            "interest_rate_percent",
            "Ставка вознаграждения, %",
            lambda value: float(value.replace(",", ".")),
        ),
        (
            [r"Итого основного долга\s*([\d\s]+[,.]\d{2})"],
            "total_principal_kzt",
            "Итого основной долг, тенге",
            parse_money,
        ),
        (
            [r"Итого процентов\s*([\d\s]+[,.]\d{2})"],
            "total_interest_kzt",
            "Итого вознаграждение, тенге",
            parse_money,
        ),
    ]

    for patterns, name, label, converter in definitions:
        item = find_first(
            document,
            patterns=patterns,
            name=name,
            label_ru=label,
            converter=converter,
        )
        if item:
            fields.append(item)

    return fields


def parse_addendum(document: ReadDocument) -> list[dict]:
    fields = parse_generic(document)
    definitions = [
        (
            [
                r"ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ\s*№\s*([A-ZА-Я0-9/_-]+)",
                r"ҚОСЫМША КЕЛІСІМ\s*№\s*([A-ZА-Я0-9/_-]+)",
            ],
            "addendum_number",
            "Номер дополнительного соглашения",
            None,
        ),
        (
            [
                r"к ДОГОВОРУ ФИНАНСОВОГО ЛИЗИНГА\s*№\s*([A-ZА-Я0-9/_-]+)",
                r"Договору финансового лизинга\s*№\s*([A-ZА-Я0-9/_-]+)",
            ],
            "lease_contract_number",
            "Номер основного договора",
            None,
        ),
        (
            [r"Сумма транша:\s*([\d\s]+)"],
            "tranche_amount_first_kzt",
            "Сумма первого транша, тенге",
            parse_money,
        ),
        (
            [r"Сумма транша:\s*([\d\s]+).*?Сумма транша:\s*([\d\s]+)"],
            "tranches_raw",
            "Транши",
            None,
        ),
        (
            [r"Дата выдачи:\s*(\d{2}\.\d{2}\.\d{4})"],
            "tranche_issue_date",
            "Дата выдачи транша",
            None,
        ),
    ]

    for patterns, name, label, converter in definitions:
        item = find_first(
            document,
            patterns=patterns,
            name=name,
            label_ru=label,
            converter=converter,
        )
        if item:
            fields.append(item)

    return fields


def parse_by_type(document: ReadDocument, doc_type: str) -> list[dict]:
    if doc_type == "purchase_contract":
        return parse_purchase_contract(document)
    if doc_type == "acceptance_act":
        return parse_acceptance_act(document)
    if doc_type == "payment_schedule":
        return parse_payment_schedule(document)
    if doc_type == "addendum":
        return parse_addendum(document)

    return parse_generic(document)
