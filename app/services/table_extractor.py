from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from decimal import Decimal

from app.services.document_reader import ReadDocument
from app.services.text_utils import parse_money

DATE_RE = re.compile(r"\b(\d{2}[.\-/]\d{2}[.\-/]\d{2,4})\b")
MONEY_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})|\d{4,}(?:[,.]\d{1,2}))(?!\d)")
VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.I)


def _date(value: str) -> str | None:
    value = value.replace('/', '.').replace('-', '.')
    for fmt in ('%d.%m.%Y', '%d.%m.%y'):
        try:
            return datetime.strptime(value, fmt).strftime('%d.%m.%Y')
        except ValueError:
            pass
    return None


def _money_values(text: str) -> list[Decimal]:
    values: list[Decimal] = []
    for match in MONEY_RE.finditer(text):
        value = parse_money(match.group(1))
        if value is not None:
            values.append(Decimal(str(value)))
    return values


def _looks_like_schedule(text: str) -> bool:
    upper = text.upper()
    return any(key in upper for key in (
        'ГРАФИК ПОГАШЕНИЯ', 'ГРАФИК ПЛАТЕЖЕЙ', 'ОСНОВНОЙ ДОЛГ',
        'ВОЗНАГРАЖДЕНИЕ', 'ОСТАТОК ОСНОВНОГО ДОЛГА', 'ПЛАТЕЖ',
    ))


def _schedule_rows(document: ReadDocument) -> dict | None:
    if not _looks_like_schedule(document.full_text):
        return None

    rows: list[dict] = []
    for page in document.pages:
        lines = [re.sub(r'\s+', ' ', line).strip() for line in page.text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            date_match = DATE_RE.search(line)
            if not date_match:
                continue
            parsed_date = _date(date_match.group(1))
            if not parsed_date:
                continue

            # OCR often wraps one physical row over several lines, but stop before
            # the next dated row so values from two payments are never merged.
            row_lines = [line]
            for follow in lines[index + 1:index + 3]:
                if DATE_RE.search(follow):
                    break
                row_lines.append(follow)
            window = ' '.join(row_lines)
            amounts = _money_values(window[date_match.end():])
            # A schedule row normally contains at least principal and one more amount.
            if len(amounts) < 2:
                continue
            # Ignore years and tiny table sequence numbers accidentally captured.
            amounts = [amount for amount in amounts if amount >= Decimal('1')]
            if len(amounts) < 2:
                continue

            row: dict = {
                'date': parsed_date,
                'page': page.page_number,
                'source_method': page.extraction_method,
                'raw': window[:700],
            }
            # Map from right to left. Balances and totals are usually at row end;
            # this preserves useful values without pretending every layout is identical.
            labels = ['principal', 'interest', 'payment', 'balance']
            for label, value in zip(labels[-len(amounts):], amounts[-4:]):
                row[label] = float(value)
            rows.append(row)

    if not rows:
        return None

    # Bilingual documents often repeat the same schedule on another page.
    # Keep one strongest row per date instead of counting both copies.
    by_date: dict[str, dict] = {}
    for row in rows:
        numeric_count = sum(
            row.get(key) is not None
            for key in ('principal', 'interest', 'payment', 'balance')
        )
        strength = (
            row.get('source_method') == 'digital',
            numeric_count,
            len(row.get('raw', '')),
        )
        existing = by_date.get(row['date'])
        if existing is None:
            by_date[row['date']] = row
            row['_strength'] = strength
            continue
        if strength > existing.get('_strength', (False, 0, 0)):
            row['_strength'] = strength
            by_date[row['date']] = row

    unique = list(by_date.values())
    for row in unique:
        row.pop('_strength', None)
    unique.sort(key=lambda row: datetime.strptime(row['date'], '%d.%m.%Y'))

    # Do not publish a table when almost every row has only two unlabeled
    # monetary values. In flattened PDF text, their column meaning is unknown.
    complete_rows = sum(
        sum(row.get(key) is not None for key in ('principal', 'interest', 'payment', 'balance')) >= 4
        for row in unique
    )
    if len(unique) < 2 or complete_rows / max(len(unique), 1) < 0.60:
        return None

    principal_sum = sum(Decimal(str(row.get('principal', 0))) for row in unique)
    interest_sum = sum(Decimal(str(row.get('interest', 0))) for row in unique)
    payment_sum = sum(Decimal(str(row.get('payment', 0))) for row in unique)
    methods = Counter(row['source_method'] for row in unique)
    confidence = 0.9 if methods.get('digital', 0) == len(unique) else 0.72

    return {
        'name': 'payment_schedule_rows',
        'label_ru': 'Таблица графика платежей',
        'columns': [
            {'key': 'date', 'label_ru': 'Дата'},
            {'key': 'principal', 'label_ru': 'Основной долг'},
            {'key': 'interest', 'label_ru': 'Вознаграждение'},
            {'key': 'payment', 'label_ru': 'Платёж'},
            {'key': 'balance', 'label_ru': 'Остаток'},
            {'key': 'page', 'label_ru': 'Страница'},
        ],
        'rows': unique,
        'row_count': len(unique),
        'summary': {
            'principal_sum_kzt': float(principal_sum),
            'interest_sum_kzt': float(interest_sum),
            'payment_sum_kzt': float(payment_sum),
            'first_payment_date': unique[0]['date'],
            'last_payment_date': unique[-1]['date'],
        },
        'confidence': confidence,
        'status': 'extracted' if confidence >= 0.85 else 'candidate',
        'notes': 'Строки восстановлены по датам и денежным значениям. Для OCR-таблиц требуется выборочная сверка столбцов.',
    }


def _asset_rows(document: ReadDocument) -> dict | None:
    text = document.full_text
    upper = text.upper()
    if not any(key in upper for key in ('СПЕЦИФИКАЦИЯ', 'VIN', 'НОМЕР ШАССИ', 'НАИМЕНОВАНИЕ ТОВАРА', 'НАИМЕНОВАНИЕ ИМУЩЕСТВА')):
        return None

    rows: list[dict] = []
    for page in document.pages:
        lines = [re.sub(r'\s+', ' ', line).strip() for line in page.text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            vins = VIN_RE.findall(line)
            if not vins:
                continue
            context = ' '.join(lines[max(0, index - 2):min(len(lines), index + 3)])
            amounts = _money_values(context)
            for vin in vins:
                row = {
                    'vin': vin.upper(),
                    'page': page.page_number,
                    'source_method': page.extraction_method,
                    'raw': context[:700],
                }
                if amounts:
                    row['amount_kzt'] = float(max(amounts))
                rows.append(row)

    if not rows:
        return None

    # Deduplicate VINs, keeping the strongest occurrence.
    by_vin: dict[str, dict] = {}
    for row in rows:
        existing = by_vin.get(row['vin'])
        if existing is None or (row['source_method'] == 'digital' and existing['source_method'] != 'digital'):
            by_vin[row['vin']] = row
    unique = list(by_vin.values())
    methods = Counter(row['source_method'] for row in unique)
    confidence = 0.94 if methods.get('digital', 0) == len(unique) else 0.76

    amount_values = [Decimal(str(row['amount_kzt'])) for row in unique if row.get('amount_kzt')]
    return {
        'name': 'asset_vin_rows',
        'label_ru': 'Перечень имущества / VIN',
        'columns': [
            {'key': 'vin', 'label_ru': 'VIN / идентификатор'},
            {'key': 'amount_kzt', 'label_ru': 'Связанная сумма, тенге'},
            {'key': 'page', 'label_ru': 'Страница'},
        ],
        'rows': unique,
        'row_count': len(unique),
        'summary': {
            'unique_vin_count': len(unique),
            'max_related_amount_kzt': float(max(amount_values)) if amount_values else None,
        },
        'confidence': confidence,
        'status': 'extracted' if confidence >= 0.85 else 'candidate',
        'notes': 'VIN извлечены построчно. Связанная сумма является контекстной и не заменяет итоговую стоимость договора.',
    }


def extract_tables(document: ReadDocument, document_type: str) -> list[dict]:
    """Extract tables only for document types where column semantics are known.

    A subsidy agreement may contain a bilingual schedule appendix, but flattened
    PDF text does not preserve column positions reliably. Returning a confidently
    wrong table is worse than returning no table, so such appendices remain
    available in raw text until layout-aware extraction is implemented.
    """
    tables: list[dict] = []
    if document_type in {'payment_schedule', 'addendum'}:
        schedule = _schedule_rows(document)
        if schedule:
            tables.append(schedule)
    if document_type in {'acceptance_act', 'purchase_contract', 'lease_contract'}:
        assets = _asset_rows(document)
        if assets:
            tables.append(assets)
    return tables
