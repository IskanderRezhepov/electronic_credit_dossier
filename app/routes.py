from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

from flask import Blueprint, abort, current_app, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from .parsers.specific import parse_by_type
from .services.classifier import classify
from .services.candidate_resolver import resolve_candidates
from .services.document_reader import OCRUnavailableError, SUPPORTED_EXTENSIONS, read_document
from .services.dossier import build_dossier_summary
from .services.exporter import save_excel, save_json
from .services.quality import review_fields
from .services.review import apply_review, field_value_for_form
from .services.table_extractor import extract_tables

bp = Blueprint('main', __name__)


def _result_path(result_id: str, extension: str = "json") -> Path:
    if not result_id.isalnum():
        abort(404)
    return Path(current_app.config['RESULT_FOLDER']) / f"{result_id}.{extension}"


def _load_result(result_id: str) -> dict:
    path = _result_path(result_id, "json")
    if not path.exists():
        abort(404)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        abort(500)


def _save_result_bundle(result: dict) -> None:
    result_dir = Path(current_app.config['RESULT_FOLDER'])
    result_dir.mkdir(parents=True, exist_ok=True)
    result_id = result["result_id"]
    save_json(result, result_dir / f"{result_id}.json")
    save_excel(result, result_dir / f"{result_id}.xlsx")


def _normalise_tables(tables):
    """Return only template-safe table dictionaries.

    This protects the HTML renderer from malformed/legacy table objects and
    from attribute names that collide with dict methods.
    """
    if not isinstance(tables, (list, tuple)):
        return []
    safe_tables = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        columns = table.get('columns')
        rows = table.get('rows')
        if not isinstance(columns, (list, tuple)) or not isinstance(rows, (list, tuple)):
            continue
        safe_columns = []
        for column in columns:
            if not isinstance(column, dict):
                continue
            key = column.get('key')
            if not isinstance(key, str) or not key:
                continue
            safe_columns.append({
                'key': key,
                'label_ru': str(column.get('label_ru') or key),
            })
        if not safe_columns:
            continue
        safe_rows = []
        for row in rows:
            if isinstance(row, dict):
                safe_rows.append(dict(row))
        safe = dict(table)
        safe['columns'] = safe_columns
        safe['rows'] = safe_rows
        safe['row_count'] = len(safe_rows)
        safe['label_ru'] = str(safe.get('label_ru') or 'Структурированная таблица')
        safe['notes'] = str(safe.get('notes') or '')
        safe['confidence'] = safe.get('confidence', 0)
        safe['status'] = str(safe.get('status') or 'candidate')
        safe_tables.append(safe)
    return safe_tables


@bp.get('/')
def index():
    return render_template('index.html', supported_formats=', '.join(sorted(SUPPORTED_EXTENSIONS)))


@bp.post('/analyze')
def analyze():
    uploaded_files = request.files.getlist('documents')
    if not uploaded_files or all(not file.filename for file in uploaded_files):
        return render_template('index.html', error='Выберите хотя бы один файл.', supported_formats=', '.join(sorted(SUPPORTED_EXTENSIONS)))

    documents = []
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for uploaded in uploaded_files:
                if not uploaded.filename:
                    continue
                original_name = Path(uploaded.filename).name
                suffix = Path(original_name).suffix.lower()
                safe_stem = secure_filename(Path(original_name).stem) or 'document'
                filename = f'{uuid.uuid4().hex}_{safe_stem}{suffix}'
                if suffix not in SUPPORTED_EXTENSIONS:
                    return render_template('index.html', error=f'Формат {suffix} пока не поддерживается.', supported_formats=', '.join(sorted(SUPPORTED_EXTENSIONS)))
                local_path = temp_path / filename
                uploaded.save(local_path)

                read_result = read_document(
                    local_path,
                    ocr_languages=current_app.config['OCR_LANGUAGES'],
                    ocr_dpi=current_app.config['OCR_DPI'],
                    min_digital_chars=current_app.config['MIN_DIGITAL_TEXT_CHARS'],
                    ocr_cache_dir=Path(current_app.config['OCR_CACHE_FOLDER']),
                )
                classification = classify(read_result.full_text, uploaded.filename)
                fields = parse_by_type(read_result, classification.key)
                fields = resolve_candidates(read_result, fields, classification.key)
                warnings = review_fields(classification.key, fields)
                tables = _normalise_tables(extract_tables(read_result, classification.key))

                documents.append({
                    'filename': uploaded.filename,
                    'source_type': read_result.source_type,
                    'page_count': read_result.page_count,
                    'used_ocr': read_result.used_ocr,
                    'page_methods': [
                        {'page': page.page_number, 'method': page.extraction_method,
                         'char_count': page.char_count, 'quality': page.quality}
                        for page in read_result.pages
                    ],
                    'document_type': classification.key,
                    'document_type_label_ru': classification.label_ru,
                    'classification_confidence': classification.confidence,
                    'matched_keywords': classification.matched_keywords,
                    'classification_alternatives': classification.alternatives,
                    'fields': fields,
                    'tables': tables,
                    'warnings': warnings,
                })
    except OCRUnavailableError as exc:
        return render_template('index.html', error=f'{exc} Установите Tesseract OCR и языки rus, kaz, eng.', supported_formats=', '.join(sorted(SUPPORTED_EXTENSIONS)))
    except Exception as exc:
        return render_template('index.html', error=f'Ошибка обработки: {exc}', supported_formats=', '.join(sorted(SUPPORTED_EXTENSIONS)))

    result = {
        'result_id': uuid.uuid4().hex,
        'documents': documents,
        'dossier': build_dossier_summary(documents),
    }
    _save_result_bundle(result)
    return render_template(
        'results.html',
        result=result,
        field_value_for_form=field_value_for_form,
        saved=False,
    )


@bp.get('/results/<result_id>')
def show_result(result_id: str):
    result = _load_result(result_id)
    return render_template(
        'results.html',
        result=result,
        field_value_for_form=field_value_for_form,
        saved=request.args.get("saved") == "1",
    )


@bp.post('/review/<result_id>')
def review_result(result_id: str):
    result = _load_result(result_id)
    updated, _changed_count = apply_review(result, request.form)
    updated["dossier"] = build_dossier_summary(updated.get("documents", []))
    _save_result_bundle(updated)
    return redirect(url_for("main.show_result", result_id=result_id, saved="1"))


@bp.get('/download/<result_id>/<kind>')
def download(result_id: str, kind: str):
    extension = {'json': 'json', 'excel': 'xlsx'}.get(kind)
    if extension is None or not result_id.isalnum():
        abort(404)
    path = _result_path(result_id, extension)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=f'результат_анализа.{extension}')
