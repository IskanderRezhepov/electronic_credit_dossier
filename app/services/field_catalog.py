from __future__ import annotations

FIELD_CATEGORIES = [
    {
        "key": "party",
        "label_ru": "Стороны и идентификаторы",
        "fields": [
            ("lessee_iin_bin", "ИИН/БИН — Лизингополучатель"),
            ("lessor_iin_bin", "ИИН/БИН — Лизингодатель"),
            ("borrower_iin_bin", "ИИН/БИН — Заёмщик"),
            ("beneficiary_iin_bin", "ИИН/БИН — Бенефициар"),
            ("guarantor_iin_bin", "ИИН/БИН — Гарант"),
            ("pledgor_iin_bin", "ИИН/БИН — Залогодатель"),
            ("seller_iin_bin", "ИИН/БИН — Продавец"),
            ("buyer_iin_bin", "ИИН/БИН — Покупатель"),
            ("sender_iin_bin", "ИИН/БИН — Отправитель"),
            ("recipient_iin_bin", "ИИН/БИН — Получатель"),
            ("principal_iin_bin", "ИИН/БИН — Принципал"),
            ("principal_name", "Принципал"),
            ("fund_iin_bin", "ИИН/БИН — Фонд Даму"),
            ("bank_bin", "БИН банка"),
            ("client_iin_bin", "ИИН/БИН — Клиент"),
            ("lessee_name", "Лизингополучатель"),
            ("lessor_name", "Лизингодатель"),
            ("borrower_name", "Заёмщик"),
            ("beneficiary_name", "Бенефициар"),
            ("guarantor_name", "Гарант"),
            ("pledgor_name", "Залогодатель"),
            ("seller_name", "Продавец"),
            ("buyer_name", "Покупатель"),
            ("sender_name", "Отправитель"),
            ("recipient_name", "Получатель"),
        ],
    },
    {
        "key": "bank",
        "label_ru": "Банковские реквизиты",
        "fields": [
            ("lessee_iban", "IBAN — Лизингополучатель"),
            ("lessor_iban", "IBAN — Лизингодатель"),
            ("borrower_iban", "IBAN — Заёмщик"),
            ("beneficiary_iban", "IBAN — Бенефициар"),
            ("sender_iban", "IBAN — Отправитель"),
            ("recipient_iban", "IBAN — Получатель"),
            ("deposit_iban", "Счёт денежного залога"),
            ("bank_bic", "БИК банка"),
            ("bank_iban", "IBAN банка"),
        ],
    },
    {
        "key": "contract",
        "label_ru": "Договоры и даты",
        "fields": [
            ("lease_contract_number", "Номер договора лизинга"),
            ("purchase_contract_number", "Номер договора купли-продажи"),
            ("guarantee_contract_number", "Номер договора гарантии"),
            ("guarantee_contract_date", "Дата договора гарантии"),
            ("pledge_contract_number", "Номер договора залога"),
            ("subsidy_contract_number", "Номер договора субсидирования"),
            ("direct_debit_agreement_number", "Номер соглашения о прямом дебетовании"),
            ("linked_lease_contract_number", "Связанный договор лизинга"),
            ("linked_guarantee_contract_number", "Связанный договор гарантии"),
            ("linked_subsidy_contract_number", "Связанный договор субсидирования"),
            ("contract_date", "Дата договора"),
            ("lease_contract_date", "Дата договора лизинга"),
            ("purchase_contract_date", "Дата договора купли-продажи"),
            ("addendum_number", "Номер дополнительного соглашения"),
            ("addendum_date", "Дата дополнительного соглашения"),
            ("base_contract_date", "Дата основного заявления / договора"),
            ("doc_ids", "DOC ID"),
        ],
    },
    {
        "key": "money",
        "label_ru": "Суммы и ставки",
        "fields": [
            ("financing_amount_kzt", "Сумма финансирования, тенге"),
            ("loan_amount_kzt", "Сумма займа / транша, тенге"),
            ("lease_asset_value_kzt", "Стоимость предмета лизинга, тенге"),
            ("purchase_total_kzt", "Сумма договора купли-продажи, тенге"),
            ("pledge_amount_kzt", "Сумма залога, тенге"),
            ("advance_payment_kzt", "Авансовый платёж, тенге"),
            ("unit_price_kzt", "Стоимость одной единицы техники, тенге"),
            ("nominal_rate_percent", "Ставка вознаграждения, %"),
            ("subsidized_rate_percent", "Субсидируемая ставка, %"),
            ("recipient_rate_percent", "Ставка, оплачиваемая получателем, %"),
        ],
    },
    {
        "key": "equipment",
        "label_ru": "Техника и имущество",
        "fields": [
            ("equipment_type", "Вид техники"),
            ("equipment_model", "Марка / модель техники"),
            ("equipment_manufacturer", "Производитель техники"),
            ("equipment_brand", "Марка техники"),
            ("equipment_color", "Цвет техники"),
            ("equipment_country", "Страна происхождения"),
            ("chassis_number", "Номер шасси"),
            ("engine_number", "Номер двигателя"),
            ("equipment_quantity", "Количество техники"),
            ("vin", "VIN"),
            ("serial_number", "Серийный номер"),
            ("manufacture_year", "Год выпуска"),
            ("equipment_total_kzt", "Общая стоимость техники, тенге"),
        ],
    },
    {
        "key": "insurance",
        "label_ru": "Страхование",
        "fields": [
            ("insurance_type", "Вид страхования"),
            ("insurance_company", "Страховая компания"),
            ("insurance_policy_number", "Номер полиса / договора страхования"),
            ("insurance_contract_date", "Дата договора / полиса страхования"),
            ("insurance_start_date", "Дата начала страхования"),
            ("insurance_end_date", "Дата окончания страхования"),
            ("insurance_renewal_date", "Дата пролонгации страхования"),
            ("insurance_sum_kzt", "Страховая сумма, тенге"),
            ("insurance_premium_kzt", "Страховая премия, тенге"),
            ("insurance_beneficiary", "Выгодоприобретатель"),
            ("insurance_holder", "Страхователь"),
            ("insurance_company_iin_bin", "ИИН/БИН страховой компании"),
            ("insurance_company_iban", "IBAN страховой компании"),
            ("insurance_holder_iban", "IBAN страхователя"),
            ("insurance_tariff_percent", "Страховой тариф, %"),
            ("insurance_linked_contract", "Связанный договор лизинга / займа"),
            ("insurance_linked_contracts", "Связанные договоры лизинга / займа"),
            ("insurance_status", "Статус страхования"),
            ("insurance_days_remaining", "Дней до окончания страхования"),
        ],
    },
    {
        "key": "gps",
        "label_ru": "GPS и мониторинг",
        "fields": [
            ("gps_provider", "Поставщик GPS / мониторинга"),
            ("gps_contract_number", "Номер договора GPS"),
            ("gps_start_date", "Дата начала GPS-мониторинга"),
            ("gps_end_date", "Дата окончания GPS-мониторинга"),
            ("gps_service_fee_kzt", "Стоимость GPS-услуг, тенге"),
            ("gps_contract_date", "Дата договора GPS"),
            ("gps_customer", "Заказчик GPS"),
            ("gps_device_quantity", "Количество GPS-трекеров"),
            ("gps_device_unit_price_kzt", "Цена одного GPS-трекера, тенге"),
            ("gps_monthly_fee_kzt", "Абонентская плата GPS в месяц, тенге"),
            ("gps_provider_iin_bin", "ИИН/БИН поставщика GPS"),
            ("gps_customer_iin_bin", "ИИН/БИН заказчика GPS"),
            ("gps_equipment_total_kzt", "Стоимость GPS-оборудования, тенге"),
            ("gps_annual_fee_kzt", "Абонентская плата GPS за год, тенге"),
        ],
    },
    {
        "key": "other",
        "label_ru": "Другое",
        "fields": [
            ("custom", "Другое поле"),
        ],
    },
]

FIELD_BY_NAME = {
    name: {"name": name, "label_ru": label, "category": category["key"]}
    for category in FIELD_CATEGORIES
    for name, label in category["fields"]
}

DOCUMENT_TYPES = [
    ("unknown", "Неизвестный тип документа"),
    ("lease_contract", "Договор финансового лизинга"),
    ("purchase_contract", "Договор купли-продажи"),
    ("acceptance_act", "Акт приёма-передачи"),
    ("payment_schedule", "График платежей"),
    ("addendum", "Дополнительное соглашение"),
    ("guarantee_contract", "Договор гарантии / поручительства"),
    ("bank_guarantee_application", "Заявление о предоставлении банковской гарантии"),
    ("cash_pledge_agreement", "Договор денежного залога"),
    ("subsidy_agreement", "Договор субсидирования"),
    ("direct_debit_agreement", "Соглашение о прямом дебетовании"),
    ("credit_line_agreement", "Соглашение о кредитной линии"),
    ("invoice", "Счёт / счёт-фактура"),
    ("signature_receipt", "Квитанция о подписании"),
    ("insurance_contract", "Договор / полис страхования"),
    ("gps_service_contract", "Договор GPS / спутникового мониторинга"),
]


def field_definition(name: str, custom_label: str | None = None) -> dict:
    if name == "custom":
        label = (custom_label or "").strip()
        if not label:
            raise ValueError("Для категории «Другое поле» укажите название.")
        safe_name = "manual_" + "_".join(
            part for part in __import__("re").sub(r"[^A-Za-zА-Яа-я0-9]+", " ", label).lower().split()
        )[:60]
        return {"name": safe_name or "manual_field", "label_ru": label, "category": "other"}
    item = FIELD_BY_NAME.get(name)
    if not item:
        raise ValueError("Неизвестная категория поля.")
    return dict(item)


def document_type_label(key: str) -> str | None:
    return dict(DOCUMENT_TYPES).get(key)
