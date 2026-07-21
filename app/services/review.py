from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone


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

    updated["review"] = {
        "updated_at": now,
        "changed_fields": changed_count,
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
