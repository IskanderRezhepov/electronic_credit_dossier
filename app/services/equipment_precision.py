from __future__ import annotations

import re
from copy import deepcopy


BRANDS = (
    "HYUNDAI", "JAC", "XCMG", "SHACMAN", "HOWO", "SITRAK", "SANY",
    "ZOOMLION", "LIUGONG", "SDLG", "VOLVO", "KOMATSU", "CATERPILLAR",
    "CAT", "DOOSAN", "MAN", "DAF", "SCANIA", "KAMAZ", "КАМАЗ",
)

TYPE_PATTERNS = (
    ("Легковой автомобиль", ("ЛЕГКОВОЙ АВТОМОБИЛЬ", "HYUNDAI SANTA FE")),
    ("Микроавтобус / фургон", ("JAC SUNRAY", "МИКРОАВТОБУС", "ФУРГОН")),
    ("Самосвал", ("САМОСВАЛ", "DUMP TRUCK")),
    ("Седельный тягач", ("СЕДЕЛЬНЫЙ ТЯГАЧ", "ТЯГАЧ")),
    ("Автокран", ("АВТОКРАН", "АВТОМОБИЛЬНЫЙ КРАН")),
    ("Экскаватор-погрузчик", ("ЭКСКАВАТОР-ПОГРУЗЧИК", "BACKHOE")),
    ("Экскаватор", ("ЭКСКАВАТОР",)),
    ("Погрузчик", ("ПОГРУЗЧИК", "LOADER")),
    ("Автобус", ("АВТОБУС",)),
    ("Грузовой автомобиль", ("ГРУЗОВОЙ АВТОМОБИЛЬ", "ГРУЗОВИК")),
    ("Автомобиль", ("АВТОМОБИЛЬ",)),
    ("Оборудование", ("ОБОРУДОВАНИЕ",)),
)


def _context(document, vin: str, radius: int = 1800) -> tuple[str, int]:
    for page in document.pages:
        match = re.search(re.escape(vin), page.text, re.I)
        if match:
            return page.text[max(0, match.start()-radius):match.end()+radius], page.page_number
    return document.full_text[:4000], 1


def _model(context: str) -> tuple[str | None, str | None]:
    upper = context.upper()
    best = None
    brand = None
    for candidate in BRANDS:
        pos = upper.find(candidate)
        if pos < 0:
            continue
        chunk = context[pos:pos+110]
        chunk = re.split(
            r"\b(?:VIN|КОЛИЧЕСТВО|САНЫ|ЦЕНА|СТОИМОСТЬ|СУММА|ИТОГО|ГОД ВЫПУСКА|ЦВЕТ|БИН|ИИК)\b",
            chunk, maxsplit=1, flags=re.I,
        )[0]
        chunk = re.sub(r"\s+", " ", chunk).strip(" ,.;:-|")
        # Keep useful trim/seat descriptors, but stop at long prose.
        words = chunk.split()
        if len(words) > 9:
            chunk = " ".join(words[:9])
        if len(chunk) >= len(candidate) + 2:
            best = chunk
            brand = candidate.title() if candidate != "JAC" else "JAC"
            break
    return brand, best



def _clean_model(value: str | None) -> str | None:
    if not value:
        return value
    text = re.sub(r"\s+", " ", value).strip(" ,.;:-|")
    # Remove an unfinished parenthetical engine fragment rather than exporting
    # syntactically broken text.
    if text.count("(") > text.count(")"):
        text = text.rsplit("(", 1)[0].rstrip()
    text = re.sub(r"\s+(?:VIN|БИН|ИИК)$", "", text, flags=re.I).strip()
    return text or None


def _equipment_type(context: str, model: str | None) -> str | None:
    upper = (context + " " + (model or "")).upper()
    for label, aliases in TYPE_PATTERNS:
        if any(alias in upper for alias in aliases):
            return label
    return None


def _explicit_year(context: str) -> str | None:
    match = re.search(r"(?:ГОД\s+ВЫПУСКА|ШЫҒАРЫЛҒАН\s+ЖЫЛЫ|ЖЫЛЫ)\s*[:\-]?\s*(20\d{2})", context, re.I)
    return match.group(1) if match else None


def _explicit_value(context: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        match = re.search(rf"{label}\s*[:\-]?\s*([^\n|;,]{{2,60}})", context, re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ,.;:-")
    return None


def improve_equipment_tables(document, fields: list[dict], tables: list[dict]) -> list[dict]:
    result = deepcopy(tables)
    contract_value = next((
        float(item.get("value"))
        for item in fields
        if item.get("name") in {
            "lease_asset_value_kzt", "purchase_total_kzt", "total_amount_kzt",
            "act_total_amount_kzt",
        } and item.get("value") not in (None, "")
    ), None)

    for table in result:
        if table.get("name") != "asset_vin_rows":
            continue
        rows = table.get("rows", [])
        vins = {row.get("vin") for row in rows if row.get("vin")}

        # Collapse accidental duplicates when there is one financed vehicle.
        if len(vins) == 1 and len(rows) > 1:
            vin = next(iter(vins))
            primary = next(row for row in rows if row.get("vin") == vin)
            for row in rows:
                if row is primary:
                    continue
                for key, value in row.items():
                    if primary.get(key) in (None, "") and value not in (None, ""):
                        primary[key] = value
            rows = [primary]
            table["rows"] = rows

        for row in rows:
            vin = row.get("vin")
            if not vin:
                continue
            context, page_number = _context(document, vin)
            brand, model = _model(context)
            model = _clean_model(model)
            equipment_type = _equipment_type(context, model)

            if brand:
                row["brand"] = brand
                row["manufacturer"] = row.get("manufacturer") or brand
            if model:
                row["model"] = model
                row["equipment_name"] = model
            if equipment_type:
                row["equipment_type"] = equipment_type

            # Remove fields fabricated from dates or the VIN label itself.
            if row.get("equipment_type") and str(row["equipment_type"]).upper().startswith("VIN"):
                row["equipment_type"] = equipment_type
            row["manufacture_year"] = _explicit_year(context)
            row["color"] = _explicit_value(context, ("ЦВЕТ", "ТҮСІ"))
            row["country_of_origin"] = _explicit_value(
                context, ("СТРАНА ПРОИСХОЖДЕНИЯ", "СТРАНА ИЗГОТОВЛЕНИЯ", "ШЫҒАРЫЛҒАН ЕЛ")
            )
            row["chassis_number"] = row.get("chassis_number") or _explicit_value(
                context, ("НОМЕР ШАССИ", "ШАССИ")
            )
            row["serial_number"] = row.get("serial_number") or _explicit_value(
                context, ("СЕРИЙНЫЙ НОМЕР", "ЗАВОДСКОЙ НОМЕР")
            )
            row["engine_number"] = row.get("engine_number") or _explicit_value(
                context, ("НОМЕР ДВИГАТЕЛЯ",)
            )
            row["page"] = page_number
            row["quantity"] = 1

        # A single-VIN lease normally finances exactly one item. If the table
        # amount is implausibly different, use the explicit contract asset value.
        if len(rows) == 1 and rows[0].get("vin") and contract_value:
            row = rows[0]
            current = float(row.get("total_amount_kzt") or 0)
            if current <= 0 or abs(current - contract_value) / max(contract_value, 1) > 0.20:
                row["unit_price_kzt"] = contract_value
                row["total_amount_kzt"] = contract_value
                row["amount_source"] = "contract_asset_value"
                row["amount_notes"] = (
                    "Стоимость восстановлена по явно указанной стоимости предмета "
                    "лизинга, поскольку в строке спецификации денежная группа была разделена."
                )
            elif row.get("unit_price_kzt") is None:
                row["unit_price_kzt"] = current

        table["row_count"] = len(rows)
        table["summary"] = {
            "total_quantity": sum(int(row.get("quantity") or 0) for row in rows) or None,
            "unique_vin_count": len({row.get("vin") for row in rows if row.get("vin")}),
            "equipment_by_type": {
                label: sum(int(row.get("quantity") or 1) for row in rows if (row.get("equipment_type") or "Не определено") == label)
                for label in sorted({row.get("equipment_type") or "Не определено" for row in rows})
            },
            "total_identified_amount_kzt": sum(float(row.get("total_amount_kzt") or 0) for row in rows) or None,
        }
        table["notes"] = (
            "VIN, модель, технические характеристики и стоимость объединены в одну позицию. "
            "Год и цвет сохраняются только при явной подписи в документе."
        )
    return result
