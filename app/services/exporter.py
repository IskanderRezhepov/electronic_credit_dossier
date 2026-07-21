from __future__ import annotations

import json
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill


def save_json(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _safe_sheet_title(title: str, used: set[str]) -> str:
    title = re.sub(r'[\[\]\*\?/\\:]', ' - ', title)
    title = re.sub(r'\s+', ' ', title).strip()[:31] or 'Документ'
    original = title
    counter = 2
    while title in used:
        suffix = f' {counter}'
        title = original[:31-len(suffix)] + suffix
        counter += 1
    used.add(title)
    return title


def save_excel(data: dict, path: Path) -> None:
    wb = Workbook()
    summary = wb.active
    summary.title = 'Сводка'
    header_fill = PatternFill('solid', fgColor='D9EAF7')
    warning_fill = PatternFill('solid', fgColor='FFF2CC')
    header_font = Font(bold=True)

    summary.append(['Показатель', 'Значение'])
    for cell in summary[1]:
        cell.fill = header_fill
        cell.font = header_font
    summary.append(['Документов', len(data['documents'])])
    summary.append(['Документов с OCR', sum(bool(doc['used_ocr']) for doc in data['documents'])])
    summary.append(['Неизвестных типов', sum(doc['document_type'] == 'unknown' for doc in data['documents'])])
    summary.append(['Предупреждений', sum(len(doc.get('warnings', [])) for doc in data['documents'])])
    dossier = data.get('dossier') or {}
    if dossier:
        summary.append(['Междокументных совпадений', dossier.get('counts', {}).get('match', 0)])
        summary.append(['Междокументных расхождений', dossier.get('counts', {}).get('mismatch', 0)])
        summary.append(['Не удалось проверить', dossier.get('counts', {}).get('not_enough_data', 0)])

    used = {'Сводка'}
    if dossier:
        dossier_sheet = wb.create_sheet('Сверка досье')
        dossier_sheet.append(['Категория', 'Проверка', 'Статус', 'Результат', 'Доказательства'])
        for cell in dossier_sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
        status_ru = {'match': 'Совпадает', 'mismatch': 'Расхождение', 'not_enough_data': 'Недостаточно данных'}
        for check in dossier.get('checks', []):
            evidence = '; '.join(
                f"{item.get('filename')} / {item.get('field')} = {item.get('value')}"
                for item in check.get('evidence', [])
            )
            dossier_sheet.append([
                check.get('category'), check.get('check'), status_ru.get(check.get('status'), check.get('status')),
                check.get('message'), evidence,
            ])
        for column, width in {'A':22,'B':42,'C':20,'D':75,'E':100}.items():
            dossier_sheet.column_dimensions[column].width = width
        for row in dossier_sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)
        dossier_sheet.freeze_panes = 'A2'
        used.add('Сверка досье')
    for document in data['documents']:
        sheet = wb.create_sheet(_safe_sheet_title(document['document_type_label_ru'], used))
        sheet.append(['Поле', 'Значение', 'Страница', 'Метод', 'Уверенность', 'Статус', 'Примечание', 'Цитата'])
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
        for item in document['fields']:
            value = item['value']
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            sheet.append([
                item['label_ru'], value, item['page'], item['extraction_method'],
                item['confidence'], item.get('status'), item.get('notes'), item['quote'],
            ])
        for column, width in {'A':35,'B':42,'C':11,'D':14,'E':13,'F':14,'G':38,'H':90}.items():
            sheet.column_dimensions[column].width = width
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)
        sheet.freeze_panes = 'A2'

        if document.get('warnings'):
            warn_sheet = wb.create_sheet(_safe_sheet_title('Контроль - ' + document['document_type_label_ru'], used))
            warn_sheet.append(['Важность', 'Поле', 'Сообщение'])
            for cell in warn_sheet[1]:
                cell.fill = warning_fill
                cell.font = header_font
            for warning in document['warnings']:
                warn_sheet.append([warning['severity'], warning['field'], warning['message']])
            warn_sheet.column_dimensions['A'].width = 16
            warn_sheet.column_dimensions['B'].width = 35
            warn_sheet.column_dimensions['C'].width = 90

    wb.save(path)
