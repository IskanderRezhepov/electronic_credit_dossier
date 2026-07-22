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


def assets_from_layout(document: ReadDocument) -> dict | None:
    rows=[]
    for page in document.pages:
        lines=_lines(page)
        header=_header_positions(lines,ASSET_HEADERS)
        if not header: continue
        header_idx,positions=header
        columns=_boundaries(positions,page.page_width)
        for line in lines[header_idx+1:]:
            cells={key:_cell_text(line,left,right) for key,left,right in columns}
            joined=' '.join(cells.values())
            vin_match=VIN_RE.search(cells.get('vin','') or joined)
            quantity=None
            qmatch=re.search(r'\b(\d{1,3})\b',cells.get('quantity',''))
            if qmatch: quantity=int(qmatch.group(1))
            unit=_money(cells.get('unit_price_kzt',''))
            total=_money(cells.get('total_amount_kzt',''))
            equipment=(cells.get('equipment') or '').strip() or None
            model=(cells.get('model') or '').strip() or None
            if not any((vin_match,quantity,unit,total,equipment,model)): continue
            if not vin_match and not quantity and unit is None and total is None: continue
            rows.append({
                'equipment_name':' '.join(v for v in (equipment,model) if v) or None,
                'equipment_type':equipment,'model':model,'quantity':quantity,
                'vin':vin_match.group(0).upper() if vin_match else None,
                'unit_price_kzt':unit,'total_amount_kzt':total,
                'page':page.page_number,'source_method':page.extraction_method,
                'raw':line['text'][:900],'evidence_level':'layout','layout_method':'coordinates',
            })
    if not rows: return None
    unique={}
    for row in rows:
        key=('vin',row['vin']) if row.get('vin') else ('layout',row.get('page'),row.get('equipment_name'),row.get('quantity'),row.get('unit_price_kzt'),row.get('total_amount_kzt'))
        unique[key]=row
    rows=list(unique.values())
    total_quantity=sum(r['quantity'] for r in rows if isinstance(r.get('quantity'),int)) or None
    vins={r['vin'] for r in rows if r.get('vin')}
    types=defaultdict(int)
    for r in rows: types[r.get('equipment_type') or 'Не определено'] += r.get('quantity') if isinstance(r.get('quantity'),int) else 1
    confidence=0.94 if any(r.get('vin') for r in rows) else 0.84
    return {
        'name':'asset_vin_rows','label_ru':'Техника / предметы финансирования',
        'columns':[{'key':'equipment_type','label_ru':'Вид техники'},{'key':'model','label_ru':'Марка / модель'},{'key':'quantity','label_ru':'Количество'},{'key':'vin','label_ru':'VIN / идентификатор'},{'key':'unit_price_kzt','label_ru':'Цена за единицу, тенге'},{'key':'total_amount_kzt','label_ru':'Общая стоимость позиции, тенге'},{'key':'page','label_ru':'Страница'}],
        'rows':rows,'row_count':len(rows),
        'summary':{'total_quantity':total_quantity,'unique_vin_count':len(vins),'equipment_by_type':dict(types),'total_identified_amount_kzt':sum(r.get('total_amount_kzt') or 0 for r in rows) or None},
        'confidence':confidence,'status':'extracted' if confidence>=0.9 else 'candidate',
        'notes':'Строки и колонки восстановлены по координатам слов. Пустые ячейки не заполняются предположениями.',
    }
