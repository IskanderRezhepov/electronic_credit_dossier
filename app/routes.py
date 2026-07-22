from __future__ import annotations

import json
import re
import tempfile
import time
import uuid
from pathlib import Path

from flask import Blueprint, abort, current_app, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from .parsers.specific import parse_by_type
from .services.classifier import classify
from .services.candidate_resolver import resolve_candidates
from .services.client_registry import get_client, list_clients, register_result
from .services.document_reader import OCRUnavailableError, SUPPORTED_EXTENSIONS, read_document
from .services.dossier import build_dossier_summary
from .services.exporter import save_excel, save_json
from .services.field_catalog import DOCUMENT_TYPES, FIELD_CATEGORIES
from .services.quality import review_fields
from .services.review import apply_review, field_value_for_form
from .services.table_extractor import extract_tables
from .services.validators import validate_fields, validation_warnings

bp = Blueprint('main', __name__)

ANALYSIS_MODES = {
    "fast": {"label_ru": "Быстрый", "dpi": 150},
    "standard": {"label_ru": "Стандартный", "dpi": None},
    "accurate": {"label_ru": "Максимальная точность", "dpi": 230},
}


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
    register_result(result_dir, result)
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
    return render_template('index.html', supported_formats=', '.join(sorted(SUPPORTED_EXTENSIONS)), analysis_modes=ANALYSIS_MODES)


@bp.post('/analyze')
def analyze():
    uploaded_files = request.files.getlist('documents')
    if not uploaded_files or all(not file.filename for file in uploaded_files):
        return render_template('index.html', error='Выберите хотя бы один файл.', supported_formats=', '.join(sorted(SUPPORTED_EXTENSIONS)), analysis_modes=ANALYSIS_MODES)

    analysis_mode = request.form.get('analysis_mode', 'standard')
    if analysis_mode not in ANALYSIS_MODES:
        analysis_mode = 'standard'
    started_total = time.perf_counter()
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
                    return render_template('index.html', error=f'Формат {suffix} пока не поддерживается.', supported_formats=', '.join(sorted(SUPPORTED_EXTENSIONS)), analysis_modes=ANALYSIS_MODES)
                local_path = temp_path / filename
                uploaded.save(local_path)

                document_started = time.perf_counter()
                read_started = time.perf_counter()
                mode_dpi = ANALYSIS_MODES[analysis_mode]['dpi'] or current_app.config['OCR_DPI']
                read_result = read_document(
                    local_path,
                    ocr_languages=current_app.config['OCR_LANGUAGES'],
                    ocr_dpi=mode_dpi,
                    min_digital_chars=current_app.config['MIN_DIGITAL_TEXT_CHARS'],
                    ocr_cache_dir=Path(current_app.config['OCR_CACHE_FOLDER']),
                    analysis_mode=analysis_mode,
                )
                read_seconds = time.perf_counter() - read_started
                extract_started = time.perf_counter()
                classification = classify(read_result.full_text, uploaded.filename)
                fields = parse_by_type(read_result, classification.key)
                fields = resolve_candidates(read_result, fields, classification.key)
                fields = validate_fields(fields)
                warnings = review_fields(classification.key, fields) + validation_warnings(fields)
                tables = _normalise_tables(extract_tables(read_result, classification.key))
                extract_seconds = time.perf_counter() - extract_started

                documents.append({
                    'filename': uploaded.filename,
                    'source_type': read_result.source_type,
                    'page_count': read_result.page_count,
                    'used_ocr': read_result.used_ocr,
                    'page_methods': [
                        {'page': page.page_number, 'method': page.extraction_method,
                         'char_count': page.char_count, 'quality': page.quality,
                         'cache_hit': page.cache_hit,
                         'layout_word_count': len(page.layout_words)}
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
                    'timing': {
                        'reading_ocr_seconds': round(read_seconds, 2),
                        'extraction_seconds': round(extract_seconds, 2),
                        'total_seconds': round(time.perf_counter() - document_started, 2),
                        'cached_ocr_pages': sum(1 for page in read_result.pages if page.cache_hit),
                    },
                })
    except OCRUnavailableError as exc:
        return render_template('index.html', error=f'{exc} Установите Tesseract OCR и языки rus, kaz, eng.', supported_formats=', '.join(sorted(SUPPORTED_EXTENSIONS)), analysis_modes=ANALYSIS_MODES)
    except Exception as exc:
        return render_template('index.html', error=f'Ошибка обработки: {exc}', supported_formats=', '.join(sorted(SUPPORTED_EXTENSIONS)), analysis_modes=ANALYSIS_MODES)

    result = {
        'result_id': uuid.uuid4().hex,
        'analysis': {
            'mode': analysis_mode,
            'mode_label_ru': ANALYSIS_MODES[analysis_mode]['label_ru'],
            'total_seconds': round(time.perf_counter() - started_total, 2),
        },
        'documents': documents,
        'dossier': build_dossier_summary(documents),
    }
    _save_result_bundle(result)
    return render_template(
        'results.html',
        result=result,
        field_value_for_form=field_value_for_form,
        field_categories=FIELD_CATEGORIES,
        document_types=DOCUMENT_TYPES,
        saved=False,
    )


@bp.get('/results/<result_id>')
def show_result(result_id: str):
    result = _load_result(result_id)
    return render_template(
        'results.html',
        result=result,
        field_value_for_form=field_value_for_form,
        field_categories=FIELD_CATEGORIES,
        document_types=DOCUMENT_TYPES,
        saved=request.args.get("saved") == "1",
    )


@bp.post('/review/<result_id>')
def review_result(result_id: str):
    result = _load_result(result_id)
    updated, _changed_count = apply_review(result, request.form)
    for document in updated.get("documents", []):
        document["fields"] = validate_fields(document.get("fields", []))
        document["warnings"] = review_fields(
            document.get("document_type", "unknown"),
            document.get("fields", []),
        ) + validation_warnings(document.get("fields", []))
    updated["dossier"] = build_dossier_summary(updated.get("documents", []))
    _save_result_bundle(updated)
    return redirect(url_for("main.show_result", result_id=result_id, saved="1"))


@bp.get('/history')
def history():
    clients = list_clients(Path(current_app.config['RESULT_FOLDER']))
    return render_template('history.html', clients=clients)


@bp.get('/clients/<client_key>')
def client_card(client_key: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", client_key):
        abort(404)
    result_folder = Path(current_app.config['RESULT_FOLDER'])
    client = get_client(result_folder, client_key)
    if not client:
        abort(404)

    results = []
    for item in client.get("results", []):
        path = result_folder / f"{item.get('result_id')}.json"
        if not path.exists():
            continue
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        results.append(result)

    aggregate_documents = [
        document
        for result in results
        for document in result.get("documents", [])
    ]
    aggregate_dossier = build_dossier_summary(aggregate_documents)
    return render_template(
        'client.html',
        client=client,
        results=results,
        dossier=aggregate_dossier,
    )


@bp.get('/download/<result_id>/<kind>')
def download(result_id: str, kind: str):
    extension = {'json': 'json', 'excel': 'xlsx'}.get(kind)
    if extension is None or not result_id.isalnum():
        abort(404)
    path = _result_path(result_id, extension)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=f'результат_анализа.{extension}')
