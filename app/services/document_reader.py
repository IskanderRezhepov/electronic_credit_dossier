
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import fitz
import pytesseract
from PIL import Image

from .text_utils import normalize_text


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
}


@dataclass
class PageContent:
    page_number: int
    text: str
    extraction_method: Literal["digital", "ocr", "hybrid", "none"]
    char_count: int


@dataclass
class ReadDocument:
    filename: str
    page_count: int
    source_type: str
    pages: list[PageContent]

    @property
    def full_text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    @property
    def used_ocr(self) -> bool:
        return any(page.extraction_method in {"ocr", "hybrid"} for page in self.pages)


class OCRUnavailableError(RuntimeError):
    pass


def read_document(
    path: Path,
    *,
    ocr_languages: str,
    ocr_dpi: int,
    min_digital_chars: int,
) -> ReadDocument:
    suffix = path.suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Неподдерживаемый формат: {suffix}")

    if suffix == ".pdf":
        return _read_pdf(
            path,
            ocr_languages=ocr_languages,
            ocr_dpi=ocr_dpi,
            min_digital_chars=min_digital_chars,
        )

    return _read_image(path, ocr_languages=ocr_languages)


def _read_pdf(
    path: Path,
    *,
    ocr_languages: str,
    ocr_dpi: int,
    min_digital_chars: int,
) -> ReadDocument:
    pages: list[PageContent] = []

    with fitz.open(path) as doc:
        for index, page in enumerate(doc, start=1):
            digital = normalize_text(page.get_text("text") or "")

            if len(digital) >= min_digital_chars:
                pages.append(
                    PageContent(
                        page_number=index,
                        text=digital,
                        extraction_method="digital",
                        char_count=len(digital),
                    )
                )
                continue

            ocr_text = _ocr_pdf_page(page, ocr_languages, ocr_dpi)
            combined = normalize_text(
                f"{digital}\n{ocr_text}" if digital else ocr_text
            )

            method = "hybrid" if digital and ocr_text else "ocr" if ocr_text else "none"

            pages.append(
                PageContent(
                    page_number=index,
                    text=combined,
                    extraction_method=method,
                    char_count=len(combined),
                )
            )

        return ReadDocument(
            filename=path.name,
            page_count=doc.page_count,
            source_type="pdf",
            pages=pages,
        )


def _read_image(path: Path, *, ocr_languages: str) -> ReadDocument:
    try:
        image = Image.open(path)
        text = _ocr_image(image, ocr_languages)
    except Exception as exc:
        raise RuntimeError(f"Не удалось прочитать изображение: {exc}") from exc

    return ReadDocument(
        filename=path.name,
        page_count=1,
        source_type="image",
        pages=[
            PageContent(
                page_number=1,
                text=normalize_text(text),
                extraction_method="ocr",
                char_count=len(normalize_text(text)),
            )
        ],
    )


def _ocr_pdf_page(page: fitz.Page, languages: str, dpi: int) -> str:
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    return _ocr_image(image, languages)


def _ocr_image(image: Image.Image, languages: str) -> str:
    try:
        return pytesseract.image_to_string(
            image,
            lang=languages,
            config="--oem 3 --psm 6",
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise OCRUnavailableError(
            "Tesseract OCR не установлен или не добавлен в PATH."
        ) from exc
