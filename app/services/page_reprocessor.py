from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image, ImageEnhance, ImageOps

from .document_reader import PageContent, ReadDocument, _ocr_multimode
from .text_utils import normalize_text

PROFILE_MAP = {
    "fast": {"mode": "fast", "dpi": 150, "label_ru": "Быстрый"},
    "accurate": {"mode": "accurate", "dpi": 260, "label_ru": "Максимальная точность"},
    "table": {"mode": "accurate", "dpi": 260, "label_ru": "Таблица"},
    "columns": {"mode": "accurate", "dpi": 260, "label_ru": "Две колонки"},
    "contrast": {"mode": "accurate", "dpi": 260, "label_ru": "Повышенный контраст"},
}


def _load_page_image(source_path: Path, page_number: int, dpi: int) -> Image.Image:
    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        with fitz.open(source_path) as document:
            if page_number < 1 or page_number > document.page_count:
                raise ValueError("Страница не существует.")
            page = document[page_number - 1]
            scale = dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        if page_number != 1:
            raise ValueError("У изображения только одна страница.")
        return ImageOps.exif_transpose(Image.open(source_path)).convert("RGB")
    raise ValueError("Повторный OCR доступен для PDF и изображений.")


def reprocess_source_page(source_path: Path, page_number: int, *, profile: str,
                          languages: str, rotation: int = 0) -> dict:
    settings = PROFILE_MAP.get(profile)
    if not settings:
        raise ValueError("Неизвестный OCR-профиль.")
    if rotation not in {0, 90, 180, 270}:
        raise ValueError("Недопустимый угол поворота.")

    image = _load_page_image(source_path, page_number, settings["dpi"])
    if rotation:
        image = image.rotate(-rotation, expand=True)
    if profile == "contrast":
        image = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.9).convert("RGB")

    result = _ocr_multimode(image, languages, settings["mode"])
    preferred = normalize_text(result.get("preferred", ""))
    return {
        "page": page_number,
        "text": preferred,
        "quality": float(result.get("quality") or 0),
        "variants": result.get("variants") or {},
        "layout_words": result.get("layout_words") or [],
        "width": float(image.width),
        "height": float(image.height),
        "profile": profile,
        "profile_label_ru": settings["label_ru"],
        "languages": languages,
        "rotation": rotation,
        "char_count": len(preferred),
    }


def rebuild_read_document(document: dict) -> ReadDocument:
    text_by_page = {
        int(item.get("page")): str(item.get("text") or "")
        for item in document.get("page_texts", [])
        if item.get("page")
    }
    layouts = {
        int(item.get("page")): item
        for item in document.get("page_layouts", [])
        if item.get("page")
    }
    methods = {
        int(item.get("page")): item
        for item in document.get("page_methods", [])
        if item.get("page")
    }
    pages=[]
    for page_number in range(1, int(document.get("page_count") or 0)+1):
        method=methods.get(page_number,{})
        layout=layouts.get(page_number,{})
        value=text_by_page.get(page_number,"")
        pages.append(PageContent(
            page_number=page_number,
            text=value,
            extraction_method=method.get("method") or "ocr",
            char_count=len(value),
            quality=float(method.get("quality") or 0),
            variants={},
            cache_hit=False,
            layout_words=layout.get("words") or [],
            page_width=layout.get("width"),
            page_height=layout.get("height"),
        ))
    return ReadDocument(
        filename=document.get("filename") or "document",
        page_count=len(pages),
        source_type=document.get("source_type") or "pdf",
        pages=pages,
    )


def merge_manual_fields(new_fields: list[dict], old_fields: list[dict]) -> list[dict]:
    result=list(new_fields)
    seen={(str(item.get("name")), str(item.get("value"))) for item in result}
    for item in old_fields or []:
        keep = item.get("extraction_method") == "manual" or item.get("status") in {"confirmed", "corrected"}
        key=(str(item.get("name")), str(item.get("value")))
        if keep and key not in seen:
            result.append(item)
            seen.add(key)
    return result
