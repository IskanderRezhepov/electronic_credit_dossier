from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Literal

import hashlib
import json

import fitz
import pytesseract
from docx import Document
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pytesseract import Output

from .text_utils import normalize_text

SUPPORTED_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp', '.docx', '.txt'}


@dataclass
class PageContent:
    page_number: int
    text: str
    extraction_method: Literal['digital', 'ocr', 'hybrid', 'none']
    char_count: int
    quality: float
    variants: dict[str, str] = dataclass_field(default_factory=dict)


@dataclass
class ReadDocument:
    filename: str
    page_count: int
    source_type: str
    pages: list[PageContent]

    @property
    def full_text(self) -> str:
        return '\n'.join(page.text for page in self.pages)

    @property
    def used_ocr(self) -> bool:
        return any(page.extraction_method in {'ocr', 'hybrid'} for page in self.pages)


class OCRUnavailableError(RuntimeError):
    pass


def read_document(path: Path, *, ocr_languages: str, ocr_dpi: int, min_digital_chars: int, ocr_cache_dir: Path | None = None) -> ReadDocument:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f'Неподдерживаемый формат: {suffix}')
    if suffix == '.pdf':
        return _read_pdf(path, ocr_languages, ocr_dpi, min_digital_chars, ocr_cache_dir)
    if suffix == '.docx':
        return _read_docx(path)
    if suffix == '.txt':
        text = normalize_text(path.read_text(encoding='utf-8', errors='ignore'))
        return ReadDocument(path.name, 1, 'text', [PageContent(1, text, 'digital', len(text), 0.99, {'digital': text})])
    return _read_image(path, ocr_languages)


def _read_docx(path: Path) -> ReadDocument:
    doc = Document(path)
    parts: list[str] = []
    for paragraph in doc.paragraphs:
        if paragraph.text.strip():
            parts.append(paragraph.text)
    for table in doc.tables:
        for row in table.rows:
            parts.append(' | '.join(cell.text.strip() for cell in row.cells))
    text = normalize_text('\n'.join(parts))
    return ReadDocument(path.name, 1, 'docx', [PageContent(1, text, 'digital', len(text), 0.99, {'digital': text})])


def _read_pdf(path: Path, languages: str, dpi: int, min_digital_chars: int, cache_dir: Path | None = None) -> ReadDocument:
    pages: list[PageContent] = []
    with fitz.open(path) as doc:
        for index, page in enumerate(doc, start=1):
            digital = normalize_text(page.get_text('text') or '')
            if _digital_text_is_usable(digital, min_digital_chars):
                pages.append(PageContent(index, digital, 'digital', len(digital), 0.99, {'digital': digital}))
                continue

            image = _render_page(page, dpi)
            ocr = _ocr_cached(image, languages, dpi, cache_dir)
            combined = normalize_text(f'{digital}\n{ocr["preferred"]}' if digital else ocr['preferred'])
            method = 'hybrid' if digital and combined else 'ocr' if combined else 'none'
            pages.append(PageContent(index, combined, method, len(combined), ocr['quality'], ocr['variants']))

        return ReadDocument(path.name, doc.page_count, 'pdf', pages)


def _read_image(path: Path, languages: str) -> ReadDocument:
    try:
        image = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
        ocr = _ocr_multimode(image, languages)
    except Exception as exc:
        raise RuntimeError(f'Не удалось прочитать изображение: {exc}') from exc
    text = normalize_text(ocr['preferred'])
    return ReadDocument(path.name, 1, 'image', [PageContent(1, text, 'ocr', len(text), ocr['quality'], ocr['variants'])])


def _digital_text_is_usable(text: str, minimum: int) -> bool:
    if len(text) < minimum:
        return False
    letters = sum(ch.isalpha() for ch in text)
    return letters / max(len(text), 1) > 0.25


def _render_page(page: fitz.Page, dpi: int) -> Image.Image:
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return Image.frombytes('RGB', [pix.width, pix.height], pix.samples)


def _preprocess_variants(image: Image.Image) -> dict[str, Image.Image]:
    gray = ImageOps.exif_transpose(image).convert('L')
    gray = ImageOps.autocontrast(gray, cutoff=1)
    contrast = ImageEnhance.Contrast(gray).enhance(1.45).filter(ImageFilter.SHARPEN)
    threshold = contrast.point(lambda p: 255 if p > 175 else 0)
    return {'gray': gray, 'contrast': contrast, 'threshold': threshold}



def _ocr_cached(image: Image.Image, languages: str, dpi: int, cache_dir: Path | None) -> dict:
    if cache_dir is None:
        return _ocr_multimode(image, languages)

    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    digest.update(image.tobytes())
    digest.update(f"|{languages}|{dpi}|adaptive-v1".encode("utf-8"))
    cache_path = cache_dir / f"{digest.hexdigest()}.json"

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and "preferred" in cached:
                return cached
        except (OSError, ValueError, TypeError):
            pass

    result = _ocr_multimode(image, languages)
    try:
        cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return result



def _ocr_multimode(image: Image.Image, languages: str) -> dict:
    """
    Fast adaptive OCR:
    1. one general pass for every page;
    2. a table pass only when the first pass is weak;
    3. sparse/column passes only for difficult pages.

    This is substantially faster than running 3-5 Tesseract passes on every
    page, especially for long scanned contracts.
    """
    prepared = _preprocess_variants(image)
    width, height = image.size
    variants: dict[str, str] = {}
    qualities: list[float] = []

    full_text, full_quality = _ocr_with_quality(prepared["contrast"], languages, psm=3)
    variants["full"] = normalize_text(full_text)
    qualities.append(full_quality)

    enough_text = len(variants["full"]) >= 350
    strong_page = full_quality >= 0.69 and enough_text

    if not strong_page:
        table_text, table_quality = _ocr_with_quality(prepared["threshold"], languages, psm=6)
        variants["table"] = normalize_text(table_text)
        qualities.append(table_quality)

    best_quality = max(qualities or [0.0])
    best_chars = max((len(value) for value in variants.values()), default=0)

    if best_quality < 0.60 or best_chars < 220:
        sparse_text, sparse_quality = _ocr_with_quality(prepared["gray"], languages, psm=11)
        variants["sparse"] = normalize_text(sparse_text)
        qualities.append(sparse_quality)

    # Column OCR is expensive and is reserved for visibly wide/two-column pages
    # that still have weak recognition after the normal passes.
    if width / max(height, 1) > 0.78 and max(qualities or [0.0]) < 0.66:
        for side, box in {
            "right_column": (int(width * 0.47), 0, width, height),
            "left_column": (0, 0, int(width * 0.53), height),
        }.items():
            crop = prepared["contrast"].crop(box)
            side_text, side_quality = _ocr_with_quality(crop, languages, psm=6)
            variants[side] = normalize_text(side_text)
            qualities.append(side_quality)

    preferred_parts = [
        variants.get("full", ""),
        variants.get("table", ""),
        variants.get("right_column", ""),
        variants.get("left_column", ""),
        variants.get("sparse", ""),
    ]
    preferred = normalize_text("\n".join(part for part in preferred_parts if part))
    return {
        "preferred": preferred,
        "quality": round(max(qualities or [0.0]), 2),
        "variants": variants,
    }

def _ocr_with_quality(image: Image.Image, languages: str, psm: int) -> tuple[str, float]:
    try:
        config = f'--oem 3 --psm {psm} -c preserve_interword_spaces=1'
        data = pytesseract.image_to_data(image, lang=languages, config=config, output_type=Output.DICT)
        words: list[str] = []
        confidences: list[float] = []
        for text, conf in zip(data.get('text', []), data.get('conf', [])):
            text = str(text).strip()
            try:
                conf_value = float(conf)
            except (TypeError, ValueError):
                conf_value = -1
            if text:
                words.append(text)
                if conf_value >= 0:
                    confidences.append(conf_value)
        quality = (sum(confidences) / len(confidences) / 100) if confidences else 0.0
        return ' '.join(words), quality
    except pytesseract.TesseractNotFoundError as exc:
        raise OCRUnavailableError('Tesseract OCR не установлен или не добавлен в PATH.') from exc
