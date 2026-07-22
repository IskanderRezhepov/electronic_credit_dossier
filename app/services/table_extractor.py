from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from decimal import Decimal

from app.services.document_reader import ReadDocument
from app.services.text_utils import parse_money
from app.services.layout_tables import assets_from_layout, schedule_from_layout

DATE_RE = re.compile(r"\b(\d{2}[.\-/]\d{2}[.\-/]\d{2,4})\b")
MONEY_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})|\d{4,}(?:[,.]\d{1,2}))(?!\d)")
VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.I)

EQUIPMENT_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Самосвал", ("САМОСВАЛ", "DUMP TRUCK")),
    ("Погрузчик", ("ПОГРУЗЧИК", "ФРОНТАЛЬНЫЙ ПОГРУЗЧИК", "LOADER")),
    ("Экскаватор", ("ЭКСКАВАТОР", "EXCAVATOR")),
    ("Экскаватор-погрузчик", ("ЭКСКАВАТОР-ПОГРУЗЧИК", "BACKHOE LOADER")),
    ("Автокран", ("АВТОКРАН", "АВТОМОБИЛЬНЫЙ КРАН")),
    ("Кран", ("КРАН",)),
    ("Бульдозер", ("БУЛЬДОЗЕР",)),
    ("Тягач", ("СЕДЕЛЬНЫЙ ТЯГАЧ", "ТЯГАЧ")),
    ("Грузовой автомобиль", ("ГРУЗОВОЙ АВТОМОБИЛЬ", "ГРУЗОВИК")),
    ("Фургон", ("ЦЕЛЬНОМЕТАЛЛИЧЕСКИЙ ФУРГОН", "ИЗОТЕРМИЧЕСКИЙ ФУРГОН", "ФУРГОН")),
    ("Автобус", ("АВТОБУС",)),
    ("Трактор", ("ТРАКТОР",)),
    ("Комбайн", ("КОМБАЙН",)),
    ("Прицеп", ("ПОЛУПРИЦЕП", "ПРИЦЕП")),
    ("Дробильная установка", ("ДРОБИЛЬНАЯ УСТАНОВКА", "ДРОБИЛКА")),
    ("Производственная линия", ("ПРОИЗВОДСТВЕННАЯ ЛИНИЯ", "ТЕХНОЛОГИЧЕСКАЯ ЛИНИЯ")),
    ("Станок", ("СТАНОК",)),
    ("Оборудование", ("ОБОРУДОВАНИЕ",)),
)

MODEL_PATTERNS = (
    re.compile(r"(?:МАРК[АИ]|МОДЕЛ[ЬИ]|МАРКА/МОДЕЛЬ)\s*[:\-]?\s*([A-ZА-Я0-9][A-ZА-Я0-9 ._/\-]{1,60})", re.I),
    re.compile(r"\b(?:JAC|HOWO|SHACMAN|SITRAK|XCMG|SANY|ZOOMLION|LIUGONG|SDLG|VOLVO|KOMATSU|CATERPILLAR|CAT|HYUNDAI|DOOSAN|MAN|DAF|SCANIA|KAMAZ|КАМАЗ)\s+[A-Z0-9][A-Z0-9._/\-]{1,30}\b", re.I),
)

QUANTITY_PATTERNS = (
    re.compile(r"(?:КОЛИЧЕСТВО|КОЛ-ВО|САНЫ)\s*[:\-]?\s*(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:ШТУК(?:А|И)?|ШТ\.?|ЕДИНИЦ(?:А|Ы)?|БІРЛІК)\b", re.I),
)


def _equipment_type(text: str) -> str | None:
    upper = text.upper()
    # Match the most specific/longest phrase first.
    matches: list[tuple[int, str]] = []
    for label, aliases in EQUIPMENT_TYPES:
        for alias in aliases:
            if alias in upper:
                matches.append((len(alias), label))
    return max(matches)[1] if matches else None


def _equipment_model(text: str) -> str | None:
    for pattern in MODEL_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = re.sub(r"\s+", " ", match.group(1) if match.lastindex else match.group(0)).strip(" .,:;-")
        # Stop before common next-column labels.
        value = re.split(
            r"\s+(?:VIN|ГОД ВЫПУСКА|ЦВЕТ|КОЛИЧЕСТВО|СТОИМОСТЬ|ЦЕНА|БИН|ИИН)\b",
            value,
            maxsplit=1,
            flags=re.I,
        )[0].strip()
        if 2 <= len(value) <= 65:
            return value
    return None


def _equipment_quantity(text: str) -> int | None:
    for pattern in QUANTITY_PATTERNS:
        match = pattern.search(text)
        if match:
            value = int(match.group(1))
            if 1 <= value <= 500:
                return value
    return None


def _unit_and_total_amounts(text: str, quantity: int | None) -> tuple[Decimal | None, Decimal | None]:
    amounts = [value for value in _money_values(text) if value >= Decimal("1000")]
    if not amounts:
        return None, None

    upper = text.upper()
    unit_amount = None
    total_amount = None

    # Prefer amounts immediately following explicit labels.
    for label, target in (
        (r"(?:ЦЕНА\s+ЗА\s+(?:ЕДИНИЦУ|1\s*ШТ)|ЦЕНА\s+ЕДИНИЦЫ|СТОИМОСТЬ\s+ЕДИНИЦЫ)", "unit"),
        (r"(?:ОБЩАЯ\s+СТОИМОСТЬ|СТОИМОСТЬ\s+ВСЕГО|СУММА\s+ВСЕГО|ИТОГО)", "total"),
    ):
        match = re.search(label, upper, re.I)
        if not match:
            continue
        nearby = _money_values(text[match.end():match.end() + 160])
        if nearby:
            if target == "unit":
                unit_amount = nearby[0]
            else:
                total_amount = nearby[0]

    # Conservative arithmetic fallback only when quantity is explicitly known.
    if quantity and quantity > 1:
        unique = sorted(set(amounts))
        for small in unique:
            for large in unique:
                if large > small and abs(large - small * quantity) <= Decimal("1.00"):
                    unit_amount = unit_amount or small
                    total_amount = total_amount or large
                    break
            if unit_amount and total_amount:
                break

    # With one VIN the only nearby amount can safely be shown as a related
    # amount, but not asserted as a unit price unless a label supports it.
    if quantity == 1 and total_amount is None and len(amounts) == 1:
        total_amount = amounts[0]

    return unit_amount, total_amount


def _equipment_name(text: str, equipment_type: str | None, model: str | None) -> str | None:
    if equipment_type and model:
        return f"{equipment_type} {model}".strip()
    return model or equipment_type


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
    if not any(
        key in upper
        for key in (
            "СПЕЦИФИКАЦИЯ", "VIN", "НОМЕР ШАССИ", "НАИМЕНОВАНИЕ ТОВАРА",
            "НАИМЕНОВАНИЕ ИМУЩЕСТВА", "ПРЕДМЕТ ЛИЗИНГА", "КОЛИЧЕСТВО",
        )
    ):
        return None

    rows: list[dict] = []
    for page in document.pages:
        lines = [re.sub(r"\s+", " ", line).strip() for line in page.text.splitlines() if line.strip()]
        consumed_vins: set[str] = set()

        # VIN-backed rows are the strongest source: one VIN normally means one unit.
        for index, line in enumerate(lines):
            vins = [vin.upper() for vin in VIN_RE.findall(line) if any(ch.isdigit() for ch in vin)]
            if not vins:
                continue
            context = " ".join(lines[max(0, index - 3):min(len(lines), index + 4)])
            equipment_type = _equipment_type(context)
            model = _equipment_model(context)
            for vin in vins:
                quantity = 1
                unit_amount, total_amount = _unit_and_total_amounts(context, quantity)
                row = {
                    "equipment_name": _equipment_name(context, equipment_type, model),
                    "equipment_type": equipment_type,
                    "model": model,
                    "quantity": quantity,
                    "vin": vin,
                    "unit_price_kzt": float(unit_amount) if unit_amount is not None else None,
                    "total_amount_kzt": float(total_amount) if total_amount is not None else None,
                    "page": page.page_number,
                    "source_method": page.extraction_method,
                    "raw": context[:900],
                    "evidence_level": "vin",
                }
                rows.append(row)
                consumed_vins.add(vin)

        # Rows without VIN: require both an equipment type and an explicit
        # quantity or price label. This prevents ordinary prose from becoming
        # fake equipment entries.
        for index, line in enumerate(lines):
            context = " ".join(lines[max(0, index - 2):min(len(lines), index + 3)])
            if VIN_RE.search(context):
                continue
            equipment_type = _equipment_type(context)
            if not equipment_type:
                continue
            quantity = _equipment_quantity(context)
            model = _equipment_model(context)
            unit_amount, total_amount = _unit_and_total_amounts(context, quantity)
            has_price_label = bool(re.search(
                r"\b(?:ЦЕНА|СТОИМОСТЬ|СУММА|ИТОГО)\b", context, re.I
            ))
            if quantity is None and not has_price_label:
                continue
            rows.append({
                "equipment_name": _equipment_name(context, equipment_type, model),
                "equipment_type": equipment_type,
                "model": model,
                "quantity": quantity,
                "vin": None,
                "unit_price_kzt": float(unit_amount) if unit_amount is not None else None,
                "total_amount_kzt": float(total_amount) if total_amount is not None else None,
                "page": page.page_number,
                "source_method": page.extraction_method,
                "raw": context[:900],
                "evidence_level": "specification",
            })

    if not rows:
        return None

    # Deduplicate. VIN is authoritative; otherwise use a conservative composite key.
    unique_by_key: dict[tuple, dict] = {}
    for row in rows:
        key = (
            "vin", row.get("vin")
        ) if row.get("vin") else (
            "spec",
            row.get("equipment_type"),
            row.get("model"),
            row.get("quantity"),
            row.get("unit_price_kzt"),
            row.get("total_amount_kzt"),
            row.get("page"),
        )
        existing = unique_by_key.get(key)
        if existing is None or (
            row.get("source_method") == "digital"
            and existing.get("source_method") != "digital"
        ):
            unique_by_key[key] = row
    unique = list(unique_by_key.values())

    methods = Counter(row["source_method"] for row in unique)
    vin_rows = sum(bool(row.get("vin")) for row in unique)
    confidence = 0.95 if vin_rows and methods.get("digital", 0) == len(unique) else 0.82 if vin_rows else 0.72

    known_quantities = [row["quantity"] for row in unique if isinstance(row.get("quantity"), int)]
    total_quantity = sum(known_quantities) if known_quantities else None
    by_type: Counter[str] = Counter()
    for row in unique:
        label = row.get("equipment_type") or "Не определено"
        quantity = row.get("quantity")
        by_type[label] += quantity if isinstance(quantity, int) else 1

    unit_prices = [Decimal(str(row["unit_price_kzt"])) for row in unique if row.get("unit_price_kzt") is not None]
    totals = [Decimal(str(row["total_amount_kzt"])) for row in unique if row.get("total_amount_kzt") is not None]

    return {
        "name": "asset_vin_rows",
        "label_ru": "Техника / предметы финансирования",
        "columns": [
            {"key": "equipment_type", "label_ru": "Вид техники"},
            {"key": "model", "label_ru": "Марка / модель"},
            {"key": "quantity", "label_ru": "Количество"},
            {"key": "vin", "label_ru": "VIN / идентификатор"},
            {"key": "unit_price_kzt", "label_ru": "Цена за единицу, тенге"},
            {"key": "total_amount_kzt", "label_ru": "Общая стоимость позиции, тенге"},
            {"key": "page", "label_ru": "Страница"},
        ],
        "rows": unique,
        "row_count": len(unique),
        "summary": {
            "total_quantity": total_quantity,
            "unique_vin_count": len({row["vin"] for row in unique if row.get("vin")}),
            "equipment_by_type": dict(by_type),
            "min_unit_price_kzt": float(min(unit_prices)) if unit_prices else None,
            "max_unit_price_kzt": float(max(unit_prices)) if unit_prices else None,
            "total_identified_amount_kzt": float(sum(totals)) if totals else None,
        },
        "confidence": confidence,
        "status": "extracted" if confidence >= 0.85 else "candidate",
        "notes": (
            "Количество по VIN считается как одна единица на уникальный VIN. "
            "Цена за единицу выводится только при явной подписи либо при точном "
            "совпадении общей суммы с количеством × ценой. Пустые значения не додумываются."
        ),
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
        schedule = schedule_from_layout(document) or _schedule_rows(document)
        if schedule:
            tables.append(schedule)
    if document_type in {'acceptance_act', 'purchase_contract', 'lease_contract'}:
        assets = assets_from_layout(document) or _asset_rows(document)
        if assets:
            tables.append(assets)
    return tables
