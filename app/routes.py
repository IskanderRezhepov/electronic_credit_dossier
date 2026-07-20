
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    render_template,
    request,
    send_file,
)
from werkzeug.utils import secure_filename

from .parsers.specific import parse_by_type
from .services.classifier import classify
from .services.document_reader import (
    OCRUnavailableError,
    SUPPORTED_EXTENSIONS,
    read_document,
)
from .services.exporter import save_excel, save_json


bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    return render_template(
        "index.html",
        supported_formats=", ".join(sorted(SUPPORTED_EXTENSIONS)),
    )


@bp.post("/analyze")
def analyze():
    uploaded_files = request.files.getlist("documents")

    if not uploaded_files or all(not file.filename for file in uploaded_files):
        return render_template(
            "index.html",
            error="Выберите хотя бы один файл.",
            supported_formats=", ".join(sorted(SUPPORTED_EXTENSIONS)),
        )

    documents = []

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            for uploaded in uploaded_files:
                if not uploaded.filename:
                    continue

                filename = secure_filename(uploaded.filename)
                suffix = Path(filename).suffix.lower()

                if suffix not in SUPPORTED_EXTENSIONS:
                    return render_template(
                        "index.html",
                        error=f"Формат {suffix} пока не поддерживается.",
                        supported_formats=", ".join(sorted(SUPPORTED_EXTENSIONS)),
                    )

                local_path = temp_path / filename
                uploaded.save(local_path)

                read_result = read_document(
                    local_path,
                    ocr_languages=current_app.config["OCR_LANGUAGES"],
                    ocr_dpi=current_app.config["OCR_DPI"],
                    min_digital_chars=current_app.config["MIN_DIGITAL_TEXT_CHARS"],
                )
                classification = classify(
                    read_result.full_text,
                    uploaded.filename,
                )
                fields = parse_by_type(
                    read_result,
                    classification.key,
                )

                documents.append(
                    {
                        "filename": uploaded.filename,
                        "source_type": read_result.source_type,
                        "page_count": read_result.page_count,
                        "used_ocr": read_result.used_ocr,
                        "page_methods": [
                            {
                                "page": page.page_number,
                                "method": page.extraction_method,
                                "char_count": page.char_count,
                            }
                            for page in read_result.pages
                        ],
                        "document_type": classification.key,
                        "document_type_label_ru": classification.label_ru,
                        "classification_confidence": classification.confidence,
                        "matched_keywords": classification.matched_keywords,
                        "fields": fields,
                    }
                )

    except OCRUnavailableError as exc:
        return render_template(
            "index.html",
            error=(
                f"{exc} Установите Tesseract OCR и языки rus, kaz, eng, "
                "затем перезапустите программу."
            ),
            supported_formats=", ".join(sorted(SUPPORTED_EXTENSIONS)),
        )
    except Exception as exc:
        return render_template(
            "index.html",
            error=f"Ошибка обработки: {exc}",
            supported_formats=", ".join(sorted(SUPPORTED_EXTENSIONS)),
        )

    result = {
        "result_id": uuid.uuid4().hex,
        "documents": documents,
    }

    result_dir = Path(current_app.config["RESULT_FOLDER"])
    save_json(result, result_dir / f"{result['result_id']}.json")
    save_excel(result, result_dir / f"{result['result_id']}.xlsx")

    return render_template("results.html", result=result)


@bp.get("/download/<result_id>/<kind>")
def download(result_id: str, kind: str):
    extension = {"json": "json", "excel": "xlsx"}.get(kind)
    if extension is None or not result_id.isalnum():
        abort(404)

    path = Path(current_app.config["RESULT_FOLDER"]) / f"{result_id}.{extension}"
    if not path.exists():
        abort(404)

    return send_file(
        path,
        as_attachment=True,
        download_name=f"результат_анализа.{extension}",
    )
