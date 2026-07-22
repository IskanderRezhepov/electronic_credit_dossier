from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone

from .field_catalog import document_type_label, field_definition


ALLOWED_REVIEW_STATUSES = {
    "extracted",
    "candidate",
    "confirmed",
    "corrected",
    "rejected",
}


def _parse_value(raw_value: str, original_value):
    """Parse an edited value while preserving the original data type where possible."""
    text = (raw_value or "").strip()

    if isinstance(original_value, (list, dict)):
        if not text:
            return [] if isinstance(original_value, list) else {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # For candidate lists, a newline-separated edit is friendlier than
            # forcing the operator to write valid JSON.
            if isinstance(original_value, list):
                return [line.strip() for line in text.splitlines() if line.strip()]
            return text
        return parsed

    if original_value is None:
        return text

    if isinstance(original_value, bool):
        return text.lower() in {"1", "true", "yes", "да"}

    if isinstance(original_value, int) and not isinstance(original_value, bool):
        try:
            return int(text)
        except ValueError:
            return text

    if isinstance(original_value, float):
        try:
            return float(text.replace(" ", "").replace(",", "."))
        except ValueError:
            return text

    return text



def _parse_page(raw_page: str | None) -> int | None:
    text = (raw_page or "").strip()
    if not text:
        return None
    try:
        page = int(text)
    except ValueError:
        return None
    return page if page > 0 else None


def _normalise_manual_value(raw_value: str, field_name: str):
    text = (raw_value or "").strip()
    if field_name.endswith("_iin_bin"):
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits or text
    if field_name.endswith("_iban"):
        return "".join(text.upper().split())
    if field_name.endswith("_percent"):
        try:
            return float(text.replace(" ", "").replace(",", "."))
        except ValueError:
            return text
    if field_name.endswith("_kzt") or field_name in {"equipment_quantity", "manufacture_year"}:
        number = text.replace(" ", "").replace(",", ".")
        try:
            value = float(number)
            return int(value) if value.is_integer() else value
        except ValueError:
            return text
    return text


def _append_manual_fields(updated: dict, form_data, now: str) -> int:
    added = 0
    for doc_index, document in enumerate(updated.get("documents", [])):
        field_names = form_data.getlist(f"add_{doc_index}_field_name")
        values = form_data.getlist(f"add_{doc_index}_value")
        pages = form_data.getlist(f"add_{doc_index}_page")
        custom_labels = form_data.getlist(f"add_{doc_index}_custom_label")
        notes = form_data.getlist(f"add_{doc_index}_notes")
        candidate_sources = form_data.getlist(f"add_{doc_index}_candidate_source")

        row_count = max(
            len(field_names), len(values), len(pages), len(custom_labels),
            len(notes), len(candidate_sources), 0
        )
        for index in range(row_count):
            field_name = field_names[index] if index < len(field_names) else ""
            raw_value = values[index] if index < len(values) else ""
            if not field_name or not str(raw_value).strip():
                continue

            custom_label = custom_labels[index] if index < len(custom_labels) else ""
            definition = field_definition(field_name, custom_label)
            value = _normalise_manual_value(raw_value, definition["name"])
            page = _parse_page(pages[index] if index < len(pages) else "")
            note = (notes[index] if index < len(notes) else "").strip()
            candidate_source = (
                candidate_sources[index] if index < len(candidate_sources) else ""
            ).strip()

            document.setdefault("fields", []).append({
                "name": definition["name"],
                "label_ru": definition["label_ru"],
                "category": definition.get("category"),
                "value": value,
                "page": page,
                "quote": candidate_source or None,
                "confidence": 1.0,
                "extraction_method": "manual",
                "value_type": "direct",
                "status": "confirmed",
                "notes": _merge_note(note or None, "Поле добавлено вручную оператором."),
                "reviewed_at": now,
                "review_source": "manual_add",
                "manual": True,
            })
            added += 1
    return added


def _apply_document_type_overrides(updated: dict, form_data, now: str) -> int:
    changed = 0
    for doc_index, document in enumerate(updated.get("documents", [])):
        key = f"document_{doc_index}_type"
        if key not in form_data:
            continue
        requested = (form_data.get(key) or "").strip()
        label = document_type_label(requested)
        if not label or requested == document.get("document_type"):
            continue
        document.setdefault("original_document_type", document.get("document_type"))
        document.setdefault(
            "original_document_type_label_ru",
            document.get("document_type_label_ru"),
        )
        document["document_type"] = requested
        document["document_type_label_ru"] = label
        document["classification_confidence"] = 1.0
        document["classification_method"] = "manual"
        document["classification_reviewed_at"] = now
        changed += 1
    return changed


def apply_review(result: dict, form_data) -> tuple[dict, int]:
    """
    Apply operator edits submitted from the result page.

    Form field names:
      document_<doc_index>_field_<field_index>_value
      document_<doc_index>_field_<field_index>_status
    """
    updated = deepcopy(result)
    changed_count = 0
    now = datetime.now(timezone.utc).isoformat()

    for doc_index, document in enumerate(updated.get("documents", [])):
        for field_index, field in enumerate(document.get("fields", [])):
            prefix = f"document_{doc_index}_field_{field_index}"
            value_key = f"{prefix}_value"
            status_key = f"{prefix}_status"

            if value_key not in form_data and status_key not in form_data:
                continue

            original_value = field.get("value")
            original_status = field.get("status", "extracted")
            raw_value = form_data.get(value_key, "")
            requested_status = form_data.get(status_key, original_status)
            if requested_status not in ALLOWED_REVIEW_STATUSES:
                requested_status = original_status

            parsed_value = _parse_value(raw_value, original_value)
            value_changed = parsed_value != original_value
            status_changed = requested_status != original_status

            if not value_changed and not status_changed:
                continue

            if "original_value" not in field:
                field["original_value"] = original_value
            if "original_status" not in field:
                field["original_status"] = original_status

            field["value"] = parsed_value
            field["status"] = requested_status
            field["reviewed_at"] = now
            field["review_source"] = "manual"
            field["notes"] = _merge_note(
                field.get("notes"),
                "Поле проверено вручную оператором.",
            )
            changed_count += 1

    changed_count += _apply_document_type_overrides(updated, form_data, now)
    added_count = _append_manual_fields(updated, form_data, now)
    changed_count += added_count

    updated["review"] = {
        "updated_at": now,
        "changed_fields": changed_count,
        "added_fields": added_count,
        "status": "reviewed" if changed_count else "unchanged",
    }
    return updated, changed_count


def _merge_note(existing: str | None, addition: str) -> str:
    existing = (existing or "").strip()
    if not existing:
        return addition
    if addition in existing:
        return existing
    return f"{existing} {addition}"


def field_value_for_form(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return "" if value is None else str(value)
