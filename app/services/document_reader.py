from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
    variants: dict[str, str]


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


def read_document(path: Path, *, ocr_languages: str, ocr_dpi: int, min_digital_chars: int) -> ReadDocument:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f'Неподдерживаемый формат: {suffix}')
    if suffix == '.pdf':
        return _read_pdf(path, ocr_languages, ocr_dpi, min_digital_chars)
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


def _read_pdf(path: Path, languages: str, dpi: int, min_digital_chars: int) -> ReadDocument:
    pages: list[PageContent] = []
    with fitz.open(path) as doc:
        for index, page in enumerate(doc, start=1):
            digital = normalize_text(page.get_text('text') or '')
            if _digital_text_is_usable(digital, min_digital_chars):
                pages.append(PageContent(index, digital, 'digital', len(digital), 0.99, {'digital': digital}))
                continue

            image = _render_page(page, dpi)
            ocr = _ocr_multimode(image, languages)
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


def _ocr_multimode(image: Image.Image, languages: str) -> dict:
    prepared = _preprocess_variants(image)
    width, height = image.size
    variants: dict[str, str] = {}
    qualities: list[float] = []

    # Несколько режимов OCR: обычный текст, разреженный текст и таблицы.
    for label, prepared_image, psm in [
        ('full', prepared['contrast'], 3),
        ('sparse', prepared['gray'], 11),
        ('table', prepared['threshold'], 6),
    ]:
        text, quality = _ocr_with_quality(prepared_image, languages, psm=psm)
        variants[label] = normalize_text(text)
        qualities.append(quality)

    # Для двуязычных страниц отдельно читаем колонки.
    if width > height * 0.72:
        for side, box in {
            'right_column': (int(width * 0.46), 0, width, height),
            'left_column': (0, 0, int(width * 0.54), height),
        }.items():
            crop = prepared['contrast'].crop(box)
            text, quality = _ocr_with_quality(crop, languages, psm=6)
            variants[side] = normalize_text(text)
            qualities.append(quality)

    preferred_parts = [
        variants.get('right_column', ''),
        variants.get('full', ''),
        variants.get('table', ''),
        variants.get('left_column', ''),
        variants.get('sparse', ''),
    ]
    preferred = normalize_text('\n'.join(part for part in preferred_parts if part))
    return {'preferred': preferred, 'quality': round(max(qualities or [0.0]), 2), 'variants': variants}


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
