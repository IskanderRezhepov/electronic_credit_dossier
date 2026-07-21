
from app.services.classifier import classify


def test_purchase_contract_not_misclassified_as_act():
    text = """
    ДОГОВОР КУПЛИ-ПРОДАЖИ ТОВАРА
    Продавец продает, Покупатель покупает товар для последующей передачи
    в финансовый лизинг. После поставки подписывается Акт приема-передачи.
    """
    result = classify(text, "F-1638892498_ДКП_ИП КОРГАНОВ.pdf")
    assert result.key == "purchase_contract"


def test_acceptance_act_classification():
    text = """
    АКТ ПРИЕМА-ПЕРЕДАЧИ №1
    Продавец передает, а Покупатель принимает автомобили.
    Наименование товара, VIN, количество, Итого.
    """
    result = classify(text, "Акт приема передачи №1.pdf")
    assert result.key == "acceptance_act"


def test_payment_schedule_classification():
    text = """
    ГРАФИК ПОГАШЕНИЯ
    Остаток основного долга, дата погашения,
    сумма погашения процентов, сумма займа.
    """
    result = classify(text, "AG2-2022-U-L-113039.pdf")
    assert result.key == "payment_schedule"
