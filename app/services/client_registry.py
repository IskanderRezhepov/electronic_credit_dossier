from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


CLIENT_ROLE_PRIORITY = (
    ("lessee_iin_bin", "Лизингополучатель"),
    ("borrower_iin_bin", "Заёмщик"),
    ("buyer_iin_bin", "Покупатель"),
    ("subsidy_recipient_bin", "Получатель субсидии"),
    ("recipient_iin_bin", "Получатель"),
    ("principal_iin_bin", "Принципал"),
)

CLIENT_NAME_FIELDS = (
    "lessee_name",
    "borrower_name",
    "buyer_name",
    "recipient_name",
    "subsidy_recipient_name",
    "principal_name",
)


def _normalise_identifier(value: object) -> str | None:
    text = re.sub(r"\D", "", str(value or ""))
    return text if len(text) == 12 else None


def _usable_fields(documents: Iterable[dict]):
    for document in documents:
        for field in document.get("fields", []):
            if field.get("status") in {"candidate", "rejected"}:
                continue
            yield document, field


def identify_client(documents: list[dict]) -> dict:
    """Choose a stable primary client from confirmed/extracted party fields."""
    fields = list(_usable_fields(documents))

    identifier = None
    role = None
    role_label = None
    evidence = None
    for field_name, label in CLIENT_ROLE_PRIORITY:
        for document, field in fields:
            if field.get("name") != field_name:
                continue
            candidate = _normalise_identifier(field.get("value"))
            if candidate:
                identifier = candidate
                role = field_name
                role_label = label
                evidence = {
                    "filename": document.get("filename"),
                    "page": field.get("page"),
                    "field": field.get("label_ru"),
                }
                break
        if identifier:
            break

    name = None
    for name_field in CLIENT_NAME_FIELDS:
        for _document, field in fields:
            if field.get("name") == name_field and str(field.get("value") or "").strip():
                name = str(field.get("value")).strip()
                break
        if name:
            break

    if not name and identifier:
        # Find nearby role name fields in the same document when available.
        role_prefix = (role or "").replace("_iin_bin", "")
        for _document, field in fields:
            if field.get("name") in {f"{role_prefix}_name", f"{role_prefix}_company_name"}:
                name = str(field.get("value") or "").strip() or None
                if name:
                    break

    return {
        "client_key": identifier or "unidentified",
        "iin_bin": identifier,
        "name": name,
        "role": role,
        "role_label_ru": role_label,
        "evidence": evidence,
        "identified": bool(identifier),
    }


def _registry_path(result_folder: Path) -> Path:
    return result_folder / "clients_index.json"


def load_registry(result_folder: Path) -> dict:
    path = _registry_path(result_folder)
    if not path.exists():
        return {"version": 1, "clients": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "clients": {}}
    if not isinstance(data, dict) or not isinstance(data.get("clients"), dict):
        return {"version": 1, "clients": {}}
    return data


def save_registry(result_folder: Path, registry: dict) -> None:
    result_folder.mkdir(parents=True, exist_ok=True)
    path = _registry_path(result_folder)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def register_result(result_folder: Path, result: dict) -> dict:
    client = identify_client(result.get("documents", []))
    result["client"] = client

    now = datetime.now(timezone.utc).isoformat()
    created_at = result.get("created_at") or now
    result["created_at"] = created_at
    result["updated_at"] = now

    registry = load_registry(result_folder)
    # Results without a reliable client identifier must stay separate.
    # Otherwise unrelated unknown clients would be merged into one card.
    key = client["client_key"]
    if key == "unidentified":
        key = f"unidentified-{result['result_id']}"
        client["client_key"] = key
    client_entry = registry["clients"].setdefault(key, {
        "client_key": key,
        "iin_bin": client.get("iin_bin"),
        "name": client.get("name"),
        "role_label_ru": client.get("role_label_ru"),
        "created_at": created_at,
        "updated_at": now,
        "results": [],
    })

    # Improve metadata when a later dossier identifies a name.
    for metadata_key in ("iin_bin", "name", "role_label_ru"):
        if client.get(metadata_key):
            client_entry[metadata_key] = client[metadata_key]
    client_entry["updated_at"] = now

    result_id = result["result_id"]
    summary = {
        "result_id": result_id,
        "created_at": created_at,
        "updated_at": now,
        "document_count": len(result.get("documents", [])),
        "document_types": sorted({
            doc.get("document_type_label_ru") or doc.get("document_type") or "Неизвестно"
            for doc in result.get("documents", [])
        }),
        "filenames": [doc.get("filename") for doc in result.get("documents", [])],
        "dossier_status": result.get("dossier", {}).get("status", "insufficient"),
        "review_status": result.get("review", {}).get("status"),
        "equipment_quantity": sum(
            table.get("summary", {}).get("total_quantity") or 0
            for doc in result.get("documents", [])
            for table in doc.get("tables", [])
            if table.get("name") == "asset_vin_rows"
        ),
    }

    existing = next(
        (item for item in client_entry["results"] if item.get("result_id") == result_id),
        None,
    )
    if existing is None:
        client_entry["results"].append(summary)
    else:
        existing.update(summary)

    client_entry["results"].sort(
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )
    save_registry(result_folder, registry)
    return client


def list_clients(result_folder: Path) -> list[dict]:
    registry = load_registry(result_folder)
    clients = list(registry["clients"].values())
    for client in clients:
        client["analysis_count"] = len(client.get("results", []))
        client["latest_result"] = client.get("results", [None])[0] if client.get("results") else None
    clients.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return clients


def get_client(result_folder: Path, client_key: str) -> dict | None:
    return load_registry(result_folder).get("clients", {}).get(client_key)
