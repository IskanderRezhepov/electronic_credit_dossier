from __future__ import annotations

import io
import re
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageOps


def _normalise_token(value: object) -> str:
    return re.sub(r"[^0-9A-ZА-ЯӘҒҚҢӨҰҮІҺ]", "", str(value or "").upper())


def _query_tokens(query: object) -> list[str]:
    return [
        token for token in (_normalise_token(part) for part in re.findall(r"[\w-]+", str(query or ""), re.UNICODE))
        if token
    ]


def find_highlight_boxes(layout: dict | None, query: object) -> list[tuple[float, float, float, float]]:
    """Find query tokens in saved page-layout words and return merged boxes."""
    if not layout or not query:
        return []
    words = [item for item in layout.get("words", []) if isinstance(item, dict)]
    word_tokens = [_normalise_token(item.get("text")) for item in words]
    query_tokens = _query_tokens(query)
    if not query_tokens:
        return []

    # Long quotes are noisy. Search the first distinctive 8 tokens and then
    # fall back to compact-value matching for identifiers such as BIN/IBAN/VIN.
    query_tokens = [token for token in query_tokens if len(token) >= 2][:8]
    boxes = []
    for start in range(len(word_tokens)):
        matched_indices = []
        cursor = start
        for target in query_tokens:
            while cursor < len(word_tokens) and not word_tokens[cursor]:
                cursor += 1
            if cursor >= len(word_tokens):
                break
            current = word_tokens[cursor]
            if current == target or target in current or current in target:
                matched_indices.append(cursor)
                cursor += 1
            else:
                break
        if matched_indices and len(matched_indices) >= min(2, len(query_tokens)):
            selected = [words[index] for index in matched_indices]
            boxes.append((
                min(float(item.get("x0", 0)) for item in selected),
                min(float(item.get("y0", 0)) for item in selected),
                max(float(item.get("x1", 0)) for item in selected),
                max(float(item.get("y1", 0)) for item in selected),
            ))
            if len(boxes) >= 8:
                break

    if boxes:
        return boxes

    compact_query = _normalise_token(query)
    if len(compact_query) < 5:
        return []
    for index, token in enumerate(word_tokens):
        if compact_query == token or compact_query in token or token in compact_query:
            item = words[index]
            boxes.append((float(item.get("x0", 0)), float(item.get("y0", 0)),
                          float(item.get("x1", 0)), float(item.get("y1", 0))))
    return boxes[:8]


def _draw_highlights(image: Image.Image, boxes, layout_width: float, layout_height: float) -> Image.Image:
    if not boxes or not layout_width or not layout_height:
        return image
    draw = ImageDraw.Draw(image, "RGBA")
    sx = image.width / float(layout_width)
    sy = image.height / float(layout_height)
    for x0, y0, x1, y1 in boxes:
        rectangle = (x0 * sx - 4, y0 * sy - 3, x1 * sx + 4, y1 * sy + 3)
        draw.rectangle(rectangle, fill=(255, 221, 64, 92), outline=(230, 145, 0, 230), width=3)
    return image


def render_source_page(source_path: Path, page_number: int, *, layout: dict | None = None,
                       query: object = None, dpi: int = 150) -> bytes:
    """Render one PDF/image page to PNG and optionally highlight saved OCR coordinates."""
    if page_number < 1:
        raise ValueError("Номер страницы должен быть положительным.")
    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        with fitz.open(source_path) as document:
            if page_number > document.page_count:
                raise ValueError("Страница не существует.")
            page = document[page_number - 1]
            scale = dpi / 72.0
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            # Digital PDFs can provide a more exact direct search even when saved layout is absent.
            direct_boxes = []
            if query:
                for rect in page.search_for(str(query))[:8]:
                    direct_boxes.append((rect.x0, rect.y0, rect.x1, rect.y1))
            if direct_boxes:
                image = _draw_highlights(image, direct_boxes, page.rect.width, page.rect.height)
            else:
                boxes = find_highlight_boxes(layout, query)
                image = _draw_highlights(
                    image, boxes,
                    (layout or {}).get("width") or page.rect.width,
                    (layout or {}).get("height") or page.rect.height,
                )
    elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        if page_number != 1:
            raise ValueError("У изображения только одна страница.")
        image = ImageOps.exif_transpose(Image.open(source_path)).convert("RGB")
        max_width = 1800
        if image.width > max_width:
            height = round(image.height * max_width / image.width)
            image = image.resize((max_width, height))
        boxes = find_highlight_boxes(layout, query)
        image = _draw_highlights(
            image, boxes,
            (layout or {}).get("width") or image.width,
            (layout or {}).get("height") or image.height,
        )
    else:
        raise ValueError("Предпросмотр доступен для PDF и изображений.")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
