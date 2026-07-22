from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from .document_reader import PageContent, ReadDocument
from .text_utils import parse_money

DATE_RE = re.compile(r"\b\d{2}[.\-/]\d{2}[.\-/]\d{2,4}\b")
VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.I)
MONEY_TOKEN_RE = re.compile(r"^-?\d[\d\s\u00a0]*(?:[,.]\d{1,2})?$")

SCHEDULE_HEADERS = {
    'date': ('ДАТА', 'КҮНІ'),
    'principal': ('ОСНОВНОЙ ДОЛГ', 'ПОГАШЕНИЕ ОСНОВНОГО', 'НЕГІЗГІ БОРЫШ'),
    'interest': ('ВОЗНАГРАЖДЕНИЕ', 'СЫЙАҚЫ'),
    'payment': ('ПЛАТЕЖ', 'ИТОГО', 'ТӨЛЕМ', 'ЖИЫНЫ'),
    'balance': ('ОСТАТОК', 'ҚАЛДЫҚ'),
}

ASSET_HEADERS = {
    'equipment': ('НАИМЕНОВАНИЕ', 'ПРЕДМЕТ ЛИЗИНГА', 'ТЕХНИКА', 'ТОВАР'),
    'model': ('МАРКА', 'МОДЕЛЬ'),
    'quantity': ('КОЛИЧЕСТВО', 'КОЛ-ВО', 'САНЫ'),
    'vin': ('VIN', 'ШАССИ', 'СЕРИЙНЫЙ НОМЕР'),
    'unit_price_kzt': ('ЦЕНА ЗА ЕДИНИЦУ', 'ЦЕНА ЕДИНИЦЫ', 'СТОИМОСТЬ ЕДИНИЦЫ'),
    'total_amount_kzt': ('ОБЩАЯ СТОИМОСТЬ', 'СУММА', 'ИТОГО'),
}


def _lines(page: PageContent, tolerance: float | None = None) -> list[dict]:
    words = sorted(page.layout_words or [], key=lambda w: (float(w['y0']), float(w['x0'])))
    if not words:
        return []
    heights = [max(1.0, float(w['y1']) - float(w['y0'])) for w in words]
    tol = tolerance or max(3.0, sorted(heights)[len(heights)//2] * 0.65)
    groups: list[list[dict]] = []
    for word in words:
        cy = (float(word['y0']) + float(word['y1'])) / 2
        target = None
        for group in reversed(groups[-4:]):
            gy = sum((float(item['y0']) + float(item['y1'])) / 2 for item in group) / len(group)
            if abs(cy - gy) <= tol:
                target = group
                break
        if target is None:
            groups.append([word])
        else:
            target.append(word)
    lines=[]
    for group in groups:
        group=sorted(group,key=lambda w:float(w['x0']))
        lines.append({
            'words':group,
            'text':' '.join(str(w['text']) for w in group),
            'y':sum((float(w['y0'])+float(w['y1']))/2 for w in group)/len(group),
        })
    return sorted(lines,key=lambda l:l['y'])


def _header_positions(lines: list[dict], headers: dict[str, tuple[str,...]]) -> tuple[int, dict[str,float]] | None:
    best=None
    for idx,line in enumerate(lines):
        joined=' '.join(lines[j]['text'] for j in range(idx,min(idx+3,len(lines)))).upper()
        positions={}
        for key,aliases in headers.items():
            candidates=[]
            for j in range(idx,min(idx+3,len(lines))):
                for word in lines[j]['words']:
                    wt=str(word['text']).upper()
                    if any(alias == wt or alias in wt for alias in aliases):
                        candidates.append((float(word['x0'])+float(word['x1']))/2)
            if candidates: positions[key]=sum(candidates)/len(candidates)
        score=len(positions)
        if score>=3 and (best is None or score>best[0]): best=(score,idx,positions)
    return (best[1],best[2]) if best else None


def _boundaries(positions: dict[str,float], page_width: float | None) -> list[tuple[str,float,float]]:
    ordered=sorted(positions.items(),key=lambda item:item[1])
    result=[]
    for i,(key,x) in enumerate(ordered):
        left=0.0 if i==0 else (ordered[i-1][1]+x)/2
        right=(page_width or x+200) if i==len(ordered)-1 else (x+ordered[i+1][1])/2
        result.append((key,left,right))
    return result


def _cell_text(line: dict, left: float, right: float) -> str:
    items=[str(w['text']) for w in line['words'] if left <= (float(w['x0'])+float(w['x1']))/2 < right]
    return ' '.join(items).strip()


def _money(value: str) -> float | None:
    parsed=parse_money(value)
    return float(parsed) if parsed is not None else None


def schedule_from_layout(document: ReadDocument) -> dict | None:
    rows=[]
    used_pages=[]
    for page in document.pages:
        lines=_lines(page)
        header=_header_positions(lines,SCHEDULE_HEADERS)
        if not header: continue
        header_idx,positions=header
        columns=_boundaries(positions,page.page_width)
        page_rows=[]
        for line in lines[header_idx+1:]:
            cells={key:_cell_text(line,left,right) for key,left,right in columns}
            date_match=DATE_RE.search(cells.get('date','') or line['text'])
            if not date_match: continue
            raw_date=date_match.group(0).replace('/','.').replace('-','.')
            parsed_date=None
            for fmt in ('%d.%m.%Y','%d.%m.%y'):
                try: parsed_date=datetime.strptime(raw_date,fmt).strftime('%d.%m.%Y'); break
                except ValueError: pass
            if not parsed_date: continue
            row={'date':parsed_date,'page':page.page_number,'source_method':page.extraction_method,'raw':line['text'][:700],'layout_method':'coordinates'}
            numeric=0
            for key in ('principal','interest','payment','balance'):
                value=_money(cells.get(key,''))
                if value is not None:
                    row[key]=value; numeric+=1
            if numeric>=2: page_rows.append(row)
        if len(page_rows)>=2:
            rows.extend(page_rows); used_pages.append(page.page_number)
    if len(rows)<2: return None
    by_key={}
    for row in rows:
        key=(row['date'],row.get('principal'),row.get('interest'),row.get('payment'),row.get('balance'))
        by_key[key]=row
    rows=sorted(by_key.values(),key=lambda r:datetime.strptime(r['date'],'%d.%m.%Y'))
    complete=sum(sum(r.get(k) is not None for k in ('principal','interest','payment','balance'))>=3 for r in rows)
    confidence=0.94 if complete/max(len(rows),1)>=0.8 else 0.84
    return {
        'name':'payment_schedule_rows','label_ru':'Таблица графика платежей',
        'columns':[{'key':'date','label_ru':'Дата'},{'key':'principal','label_ru':'Основной долг'},{'key':'interest','label_ru':'Вознаграждение'},{'key':'payment','label_ru':'Платёж'},{'key':'balance','label_ru':'Остаток'},{'key':'page','label_ru':'Страница'}],
        'rows':rows,'row_count':len(rows),
        'summary':{
            'principal_sum_kzt':sum(r.get('principal',0) for r in rows),
            'interest_sum_kzt':sum(r.get('interest',0) for r in rows),
            'payment_sum_kzt':sum(r.get('payment',0) for r in rows),
            'first_payment_date':rows[0]['date'],'last_payment_date':rows[-1]['date'],
            'layout_pages':used_pages,
        },
        'confidence':confidence,'status':'extracted' if confidence>=0.9 else 'candidate',
        'notes':'Колонки восстановлены по координатам слов на странице. Требуется выборочная сверка сложных объединённых ячеек.',
    }


def _plain_number(value: str) -> int | None:
    compact = re.sub(r"\s+", "", value or "")
    if not re.fullmatch(r"\d{1,3}", compact):
        return None
    number = int(compact)
    return number if 1 <= number <= 500 else None


def _tech_value(text: str, labels: tuple[str, ...], pattern: str = r"([A-ZА-Я0-9][A-ZА-Я0-9 ._/\-]{1,50})") -> str | None:
    for label in labels:
        match = re.search(rf"(?:{label})\s*[:№-]?\s*{pattern}", text, re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ,.;:-")
    return None


def _is_total_line(text: str) -> bool:
    upper = text.upper().strip()
    return bool(re.match(r"^(?:ИТОГО|ВСЕГО|БАРЛЫҒЫ|ЖАЛПЫ)\b", upper))


def _merge_asset_rows(rows: list[dict]) -> list[dict]:
    # A VIN row is authoritative. Merge nearby specification fragments into it.
    by_vin = {row["vin"]: row for row in rows if row.get("vin")}
    result = list(by_vin.values())
    for row in rows:
        if row.get("vin"):
            continue
        candidates = [
            target for target in result
            if target.get("page") == row.get("page")
            and (
                not row.get("model") or not target.get("model")
                or row.get("model") in target.get("model", "")
                or target.get("model", "") in row.get("model")
            )
        ]
        if len(candidates) == 1:
            target = candidates[0]
            for key, value in row.items():
                if value not in (None, "", []) and target.get(key) in (None, "", []):
                    target[key] = value
        elif row.get("equipment_type") or row.get("model"):
            result.append(row)

    # If only one unique VIN exists, suppress duplicate non-VIN lines from the same item.
    vins = {row.get("vin") for row in result if row.get("vin")}
    if len(vins) == 1:
        vin_row = next(row for row in result if row.get("vin"))
        for row in result:
            if row is vin_row:
                continue
            for key in ("equipment_type", "model", "manufacturer", "brand", "manufacture_year",
                        "color", "country_of_origin", "chassis_number", "serial_number",
                        "engine_number", "unit_price_kzt", "total_amount_kzt"):
                if vin_row.get(key) in (None, "") and row.get(key) not in (None, ""):
                    vin_row[key] = row[key]
        result = [vin_row]
        vin_row["quantity"] = 1
    return result


def assets_from_layout(document: ReadDocument) -> dict | None:
    rows = []
    total_lines = []
    for page in document.pages:
        lines = _lines(page)
        header = _header_positions(lines, ASSET_HEADERS)
        if not header:
            continue
        header_idx, positions = header
        columns = _boundaries(positions, page.page_width)

        pending: list[dict] = []
        for line in lines[header_idx + 1:]:
            cells = {key: _cell_text(line, left, right) for key, left, right in columns}
            joined = " ".join(value for value in cells.values() if value).strip()
            if not joined:
                continue
            if _is_total_line(joined):
                total_lines.append({"page": page.page_number, "text": joined})
                continue

            vin_match = VIN_RE.search(cells.get("vin", "") or joined)
            quantity = _plain_number(cells.get("quantity", ""))
            unit = _money(cells.get("unit_price_kzt", ""))
            total = _money(cells.get("total_amount_kzt", ""))
            equipment = (cells.get("equipment") or "").strip() or None
            model = (cells.get("model") or "").strip() or None

            # Never interpret a piece of a money value such as "15 900 000" as quantity 15.
            if quantity and re.search(rf"\b{quantity}\s+\d{{3}}\s+\d{{3}}\b", joined):
                quantity = None

            row = {
                "equipment_name": " ".join(value for value in (equipment, model) if value) or None,
                "equipment_type": equipment,
                "model": model,
                "quantity": quantity,
                "vin": vin_match.group(0).upper() if vin_match else None,
                "unit_price_kzt": unit,
                "total_amount_kzt": total,
                "manufacturer": _tech_value(joined, ("ПРОИЗВОДИТЕЛЬ", "ИЗГОТОВИТЕЛЬ", "ӨНДІРУШІ")),
                "brand": _tech_value(joined, ("МАРКА", "БРЕНД")),
                "manufacture_year": _tech_value(joined, ("ГОД ВЫПУСКА", "ЖЫЛЫ"), r"(\d{4})"),
                "color": _tech_value(joined, ("ЦВЕТ", "ТҮСІ")),
                "country_of_origin": _tech_value(joined, ("СТРАНА ПРОИСХОЖДЕНИЯ", "СТРАНА ИЗГОТОВЛЕНИЯ", "ШЫҒАРЫЛҒАН ЕЛ")),
                "chassis_number": _tech_value(joined, ("НОМЕР ШАССИ", "ШАССИ")),
                "serial_number": _tech_value(joined, ("СЕРИЙНЫЙ НОМЕР", "ЗАВОДСКОЙ НОМЕР", "СЕРИЯЛЫҚ НӨМІР")),
                "engine_number": _tech_value(joined, ("НОМЕР ДВИГАТЕЛЯ", "ДВИГАТЕЛЬ")),
                "page": page.page_number,
                "source_method": page.extraction_method,
                "raw": joined[:1200],
                "evidence_level": "layout",
                "layout_method": "coordinates-v2",
            }

            meaningful = any(row.get(key) not in (None, "") for key in (
                "vin", "quantity", "unit_price_kzt", "total_amount_kzt",
                "equipment_type", "model", "manufacturer", "brand",
                "manufacture_year", "chassis_number", "serial_number",
            ))
            if meaningful:
                pending.append(row)

        rows.extend(pending)

    if not rows:
        return None

    rows = _merge_asset_rows(rows)
    unique = {}
    for row in rows:
        key = ("vin", row["vin"]) if row.get("vin") else (
            "asset", row.get("page"), row.get("equipment_type"), row.get("model"),
            row.get("quantity"), row.get("unit_price_kzt"), row.get("total_amount_kzt"),
        )
        unique[key] = row
    rows = list(unique.values())

    # Arithmetic repair: when quantity and only one price are present.
    for row in rows:
        qty = row.get("quantity")
        unit = row.get("unit_price_kzt")
        total = row.get("total_amount_kzt")
        if qty == 1:
            if unit is None and total is not None:
                row["unit_price_kzt"] = total
            elif total is None and unit is not None:
                row["total_amount_kzt"] = unit
        elif isinstance(qty, int) and qty > 1:
            if unit is not None and total is None:
                row["total_amount_kzt"] = round(unit * qty, 2)
            elif total is not None and unit is None and total % qty == 0:
                row["unit_price_kzt"] = round(total / qty, 2)

    total_quantity = sum(row["quantity"] for row in rows if isinstance(row.get("quantity"), int)) or None
    vins = {row["vin"] for row in rows if row.get("vin")}
    types = defaultdict(int)
    for row in rows:
        types[row.get("equipment_type") or "Не определено"] += row.get("quantity") if isinstance(row.get("quantity"), int) else 1

    columns = [
        {"key": "equipment_type", "label_ru": "Вид техники"},
        {"key": "manufacturer", "label_ru": "Производитель"},
        {"key": "brand", "label_ru": "Марка"},
        {"key": "model", "label_ru": "Модель / комплектация"},
        {"key": "manufacture_year", "label_ru": "Год выпуска"},
        {"key": "vin", "label_ru": "VIN / идентификатор"},
        {"key": "chassis_number", "label_ru": "Номер шасси"},
        {"key": "serial_number", "label_ru": "Серийный номер"},
        {"key": "engine_number", "label_ru": "Номер двигателя"},
        {"key": "color", "label_ru": "Цвет"},
        {"key": "country_of_origin", "label_ru": "Страна происхождения"},
        {"key": "quantity", "label_ru": "Количество"},
        {"key": "unit_price_kzt", "label_ru": "Цена за единицу, тенге"},
        {"key": "total_amount_kzt", "label_ru": "Общая стоимость позиции, тенге"},
        {"key": "page", "label_ru": "Страница"},
    ]
    confidence = 0.95 if vins and all(row.get("quantity") == 1 for row in rows if row.get("vin")) else 0.84
    return {
        "name": "asset_vin_rows",
        "label_ru": "Транспорт, техника и предметы финансирования",
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "summary": {
            "total_quantity": total_quantity,
            "unique_vin_count": len(vins),
            "equipment_by_type": dict(types),
            "total_identified_amount_kzt": sum(row.get("total_amount_kzt") or 0 for row in rows) or None,
            "ignored_total_lines": total_lines,
        },
        "confidence": confidence,
        "status": "extracted" if confidence >= 0.9 else "candidate",
        "notes": (
            "Строки ИТОГО не считаются отдельной техникой. VIN-строки объединяются "
            "со строками модели и характеристик. Денежные группы не используются как количество."
        ),
    }

