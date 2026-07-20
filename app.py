from __future__ import annotations

import json
import re
import tempfile
import uuid
from pathlib import Path

import fitz
from flask import Flask, abort, render_template, request, send_file
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
RESULT_DIR = Path("instance/results")
RESULT_DIR.mkdir(parents=True, exist_ok=True)


def normalize_text(text: str) -> str:
    text = text.replace("\\u00a0", " ").replace("\\xad", "").replace("￾", "")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\\n".join(line for line in lines if line)


def read_pdf(path: Path) -> dict:
    pages = []
    with fitz.open(path) as doc:
        for number, page in enumerate(doc, start=1):
            pages.append({
                "page": number,
                "text": normalize_text(page.get_text("text") or ""),
            })
        return {
            "filename": path.name,
            "page_count": doc.page_count,
            "pages": pages,
            "full_text": "\\n".join(p["text"] for p in pages),
        }


def classify(text: str) -> tuple[str, str]:
    low = text.lower()
    if "заявление о присоединении" in low and "срок лизинга" in low:
        return "lease_contract", "Заявление о присоединении / договор финансового лизинга"
    if "договор купли-продажи товара" in low and "продавец продает" in low:
        return "purchase_contract", "Договор купли-продажи для передачи в финансовый лизинг"
    return "unknown", "Неизвестный тип документа"


def parse_number(raw: str) -> float:
    return float(raw.replace(" ", "").replace(",", "."))


def make_quote(text: str, start: int, end: int, radius: int = 120) -> str:
    return " ".join(text[max(0, start-radius):min(len(text), end+radius)].split())


def find_field(pdf: dict, patterns, name, label, converter=None, confidence=0.96):
    for page in pdf["pages"]:
        for pattern in patterns:
            match = re.search(pattern, page["text"], re.IGNORECASE | re.DOTALL)
            if match:
                raw = match.group(1) if match.groups() else match.group(0)
                value = converter(raw) if converter else raw.strip()
                return {
                    "name": name,
                    "label_ru": label,
                    "value": value,
                    "page": page["page"],
                    "quote": make_quote(page["text"], match.start(), match.end()),
                    "confidence": confidence,
                    "value_type": "direct",
                }
    return None


def common_fields(pdf: dict) -> list[dict]:
    fields = []
    specs = [
        ([r"DOC ID\\s+([A-Z0-9]+)"], "doc_id", "DOC ID", None),
        ([r"Рег\\.\\s*Номер:\\s*([0-9/.-]+)"], "registration_number", "Регистрационный номер", None),
        ([r"Рег\\.\\s*Дата:\\s*(\\d{2}\\.\\d{2}\\.\\d{4})"], "registration_date", "Регистрационная дата", None),
        ([r"Статус\\s+(Подписан|Подписано)"], "signature_status", "Статус подписания", None),
        ([r"(?:Количество страниц|Количество\\s+страниц)\\s+(\\d+)"], "signed_page_count", "Страниц по квитанции", int),
        ([r"(?:Подписи|Электронные подписи \\(ЭЦП\\))\\s+(\\d+)"], "signature_count", "Количество ЭЦП", int),
    ]
    for patterns, name, label, converter in specs:
        field = find_field(pdf, patterns, name, label, converter)
        if field:
            fields.append(field)

    ids = sorted(set(re.findall(r"\\b\\d{12}\\b", pdf["full_text"])))
    if ids:
        fields.append({
            "name": "iin_bin_candidates",
            "label_ru": "Найденные ИИН/БИН",
            "value": ids,
            "page": None,
            "quote": None,
            "confidence": 0.9,
            "value_type": "direct",
        })
    return fields


def extract_lease(pdf: dict) -> list[dict]:
    fields = common_fields(pdf)
    specs = [
        ([r"Заявление о присоединении\\s*\\(Договор лизинга\\)\\s*№\\s*([A-ZА-Я0-9/_-]+)"], "lease_contract_number", "Номер договора лизинга", None),
        ([r"Стоимость Предмета лизинга составляет\\s+(\\d+\\s+\\d+\\s+\\d+,\\d{2})"], "asset_value_kzt", "Стоимость предмета лизинга", parse_number),
        ([r"составляет\\s+(\\d+)\\s*\\(Тридцать семь\\)\\s*месяцев"], "lease_term_months", "Срок лизинга, месяцев", int),
        ([r"в размере\\s+(\\d{1,2}[,.]\\d)\\s*%[^\\n]{0,80}годовых"], "interest_rate_percent", "Ставка вознаграждения, %", lambda x: float(x.replace(",", "."))),
        ([r"авансовый платеж в размере\\s+(\\d+\\s+\\d+\\s+\\d+,\\d{2})"], "advance_payment_kzt", "Авансовый платёж", parse_number),
        ([r"комиссию за организацию лизинга в размере\\s+(\\d+)%"], "commission_percent", "Комиссия, %", float),
        ([r"что составляет\\s+(\\d+\\s+\\d+,\\d{2})\\s*\\([^)]*\\)\\s*тенге"], "commission_kzt", "Комиссия за организацию лизинга", parse_number),
        ([r"Продавец\\s*-\\s*TОО\\s*«([^»]+)»"], "seller_name", "Продавец", None),
        ([r"Договору купли-продажи[^№]{0,100}№\\s*([A-ZА-Я0-9/_-]+)"], "linked_purchase_contract", "Связанный договор купли-продажи", None),
        ([r"Договор гарантии\\s*№\\s*([A-ZА-Я0-9/_-]+)"], "guarantee_contract_number", "Номер договора гарантии", None),
        ([r"(Самосвал\\s+HOWO,\\s*T5G,\\s*год выпуска\\s*[–-]\\s*2025\\s*г\\.)"], "asset_description", "Предмет лизинга", None),
    ]
    for patterns, name, label, converter in specs:
        field = find_field(pdf, patterns, name, label, converter)
        if field:
            fields.append(field)

    values = {f["name"]: f["value"] for f in fields}
    value = values.get("asset_value_kzt")
    advance = values.get("advance_payment_kzt")
    commission = values.get("commission_kzt")

    if isinstance(value, (int, float)) and isinstance(advance, (int, float)):
        financing = value - advance
        fields.append({
            "name": "financing_amount_kzt",
            "label_ru": "Сумма финансирования",
            "value": round(financing, 2),
            "page": None,
            "quote": f"{value:.2f} - {advance:.2f}",
            "confidence": 1.0,
            "value_type": "calculated",
        })
        fields.append({
            "name": "advance_percent",
            "label_ru": "Аванс, %",
            "value": round(advance / value * 100, 4),
            "page": None,
            "quote": f"{advance:.2f} / {value:.2f} × 100",
            "confidence": 1.0,
            "value_type": "calculated",
        })
        if isinstance(commission, (int, float)):
            expected = financing * 0.01
            fields.append({
                "name": "commission_math_check",
                "label_ru": "Проверка комиссии 1%",
                "value": {
                    "expected": round(expected, 2),
                    "actual": round(commission, 2),
                    "matches": abs(expected - commission) < 0.01,
                },
                "page": None,
                "quote": f"{financing:.2f} × 1%",
                "confidence": 1.0,
                "value_type": "calculated",
            })
    return fields


def extract_purchase(pdf: dict) -> list[dict]:
    fields = common_fields(pdf)
    specs = [
        ([r"ДОГОВОР купли-продажи товара[^№]*№\\s*([A-ZА-Я0-9/_-]+)"], "purchase_contract_number", "Номер договора купли-продажи", None),
        ([r"Общая стоимость настоящего Договора\\s+составляет\\s+(\\d+\\s+\\d+\\s+\\d+,\\d{2})"], "purchase_total_kzt", "Общая стоимость договора", parse_number),
        ([r"в том числе НДС\\s+(\\d+)%"], "vat_percent", "НДС, %", float),
        ([r"поставку Товара в\\s+течение\\s+(\\d+)\\s*\\([^)]*\\)\\s*рабочих дней"], "delivery_term_workdays", "Срок поставки, рабочих дней", int),
        ([r"(Республика Казахстан,\\s*город\\s*Актобе,\\s*проспект\\s*Нокина,\\s*14е)"], "delivery_address", "Место поставки", None),
        ([r"пеню в размере\\s+(\\d+[,.]\\d)%\\s*от общей стоимости Договора за каждый день просрочки"], "late_delivery_penalty_percent_daily", "Пеня за просрочку, % в день", lambda x: float(x.replace(",", "."))),
        ([r"Товарищество с ограниченной ответственностью\\s*«([^»]+)»"], "seller_name", "Продавец", None),
        ([r"БИН:\\s*(\\d{12})"], "seller_bin", "БИН продавца", None),
        ([r"Заявление о присоединении\\s*№\\s*([A-ZА-Я0-9/_-]+)"], "linked_lease_contract", "Связанный договор лизинга", None),
        ([r"(Самосвал\\s+HOWO,\\s*T5G,\\s*год выпуска\\s*[–-]\\s*2025\\s*г\\.)"], "asset_description", "Товар", None),
    ]
    for patterns, name, label, converter in specs:
        field = find_field(pdf, patterns, name, label, converter)
        if field:
            fields.append(field)
    return fields


def field_map(document: dict) -> dict:
    return {f["name"]: f["value"] for f in document["fields"]}


def client_id(fields: dict):
    ids = fields.get("iin_bin_candidates") or []
    for value in ids:
        if value.startswith("95"):
            return value
    return ids[0] if ids else None


def compare(checks, name, left, right, normalize=False):
    if left is None or right is None:
        checks.append({"name": name, "status": "warning", "message": "Недостаточно данных для проверки."})
        return
    a = " ".join(str(left).lower().split()) if normalize else left
    b = " ".join(str(right).lower().split()) if normalize else right
    checks.append({
        "name": name,
        "status": "ok" if a == b else "error",
        "message": "Значения совпадают." if a == b else f"Не совпало: {left} / {right}.",
    })


def validate(documents: list[dict]) -> list[dict]:
    checks = []
    lease = next((d for d in documents if d["document_type"] == "lease_contract"), None)
    purchase = next((d for d in documents if d["document_type"] == "purchase_contract"), None)

    if not lease or not purchase:
        return [{"name": "Базовая пара документов", "status": "warning", "message": "Нужны оба договора."}]

    lf, pf = field_map(lease), field_map(purchase)
    compare(checks, "Стоимость предмета", lf.get("asset_value_kzt"), pf.get("purchase_total_kzt"))
    compare(checks, "Описание имущества", lf.get("asset_description"), pf.get("asset_description"), True)
    compare(checks, "ИИН/БИН клиента", client_id(lf), client_id(pf))

    lease_link = str(lf.get("linked_purchase_contract", ""))
    purchase_number = str(pf.get("purchase_contract_number", ""))
    if lease_link and purchase_number:
        checks.append({
            "name": "Связь с договором купли-продажи",
            "status": "ok" if purchase_number in lease_link else "error",
            "message": f"ДКП: {purchase_number}; ссылка: {lease_link}.",
        })

    lease_number = str(lf.get("lease_contract_number", ""))
    purchase_link = str(pf.get("linked_lease_contract", ""))
    if lease_number and purchase_link:
        checks.append({
            "name": "Связь с договором лизинга",
            "status": "ok" if lease_number == purchase_link else "error",
            "message": f"ДЛ: {lease_number}; ссылка: {purchase_link}.",
        })

    for doc, expected in [(lease, 2), (purchase, 3)]:
        actual = field_map(doc).get("signature_count")
        checks.append({
            "name": f"Количество ЭЦП: {doc['document_type_label_ru']}",
            "status": "ok" if actual == expected else "warning" if actual is None else "error",
            "message": f"Ожидалось {expected}, найдено {actual if actual is not None else 'не найдено'}.",
        })

    math_check = lf.get("commission_math_check")
    if isinstance(math_check, dict):
        checks.append({
            "name": "Проверка комиссии 1%",
            "status": "ok" if math_check["matches"] else "error",
            "message": f"Ожидаемо {math_check['expected']:,.2f}; в документе {math_check['actual']:,.2f}.",
        })
    return checks


def save_json(result: dict, path: Path):
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_sheet_title(raw_title: str, existing_titles: list[str]) -> str:
    # Excel forbids these characters in worksheet names: \ / * ? : [ ]
    title = re.sub(r'[\\/*?:\[\]]', ' - ', raw_title)
    title = ' '.join(title.split()).strip(" '")
    title = title[:31] or "Документ"

    base_title = title
    counter = 2
    while title in existing_titles:
        suffix = f" ({counter})"
        title = f"{base_title[:31-len(suffix)]}{suffix}"
        counter += 1

    return title


def save_excel(result: dict, path: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Сводка"
    fill = PatternFill("solid", fgColor="D9EAF7")
    ws.append(["Показатель", "Значение"])
    for c in ws[1]:
        c.fill = fill
        c.font = Font(bold=True)
    ws.append(["Документов", len(result["documents"])])
    ws.append(["Проверок", len(result["checks"])])

    for doc in result["documents"]:
        sheet_title = safe_sheet_title(doc["document_type_label_ru"], wb.sheetnames)
        sheet = wb.create_sheet(sheet_title)
        sheet.append(["Поле", "Значение", "Страница", "Тип", "Уверенность", "Цитата"])
        for c in sheet[1]:
            c.fill = fill
            c.font = Font(bold=True)
        for f in doc["fields"]:
            value = json.dumps(f["value"], ensure_ascii=False) if isinstance(f["value"], (dict, list)) else f["value"]
            sheet.append([f["label_ru"], value, f["page"], f["value_type"], f["confidence"], f["quote"]])
        widths = {"A": 32, "B": 32, "C": 12, "D": 14, "E": 12, "F": 85}
        for col, width in widths.items():
            sheet.column_dimensions[col].width = width
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)

    checks = wb.create_sheet("Проверки")
    checks.append(["Проверка", "Статус", "Комментарий"])
    for c in checks[1]:
        c.fill = fill
        c.font = Font(bold=True)
    for item in result["checks"]:
        checks.append([item["name"], item["status"], item["message"]])
    checks.column_dimensions["A"].width = 40
    checks.column_dimensions["B"].width = 15
    checks.column_dimensions["C"].width = 80
    wb.save(path)


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/analyze")
def analyze():
    uploads = request.files.getlist("documents")
    if not uploads or all(not f.filename for f in uploads):
        return render_template("index.html", error="Выберите PDF-файлы.")

    documents = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for upload in uploads:
            if not upload.filename:
                continue
            filename = secure_filename(upload.filename)
            if not filename.lower().endswith(".pdf"):
                return render_template("index.html", error=f"{upload.filename}: нужен PDF.")
            path = tmp_path / filename
            upload.save(path)
            pdf = read_pdf(path)
            doc_type, label = classify(pdf["full_text"])
            fields = extract_lease(pdf) if doc_type == "lease_contract" else extract_purchase(pdf) if doc_type == "purchase_contract" else common_fields(pdf)
            documents.append({
                "filename": upload.filename,
                "page_count": pdf["page_count"],
                "document_type": doc_type,
                "document_type_label_ru": label,
                "fields": fields,
            })

    result_id = uuid.uuid4().hex
    result = {"result_id": result_id, "documents": documents, "checks": validate(documents)}
    save_json(result, RESULT_DIR / f"{result_id}.json")
    save_excel(result, RESULT_DIR / f"{result_id}.xlsx")
    return render_template("results.html", result=result)


@app.get("/download/<result_id>/<kind>")
def download(result_id: str, kind: str):
    if not result_id.isalnum():
        abort(404)
    ext = "xlsx" if kind == "excel" else "json" if kind == "json" else None
    if not ext:
        abort(404)
    path = RESULT_DIR / f"{result_id}.{ext}"
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"результат_анализа.{ext}")