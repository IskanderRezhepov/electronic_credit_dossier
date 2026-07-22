
from app.parsers.specific import parse_addendum, parse_acceptance_act
from app.services.document_reader import PageContent, ReadDocument


def doc(text: str, filename: str, used_ocr: bool = True) -> ReadDocument:
    return ReadDocument(
        filename=filename,
        page_count=1,
        source_type="pdf",
        pages=[
            PageContent(
                page_number=1,
                text=text,
                extraction_method="ocr" if used_ocr else "digital",
                quality=0.82 if used_ocr else 0.99,
                char_count=len(text),
            )
        ],
    )


def test_addendum_reads_main_lease_number():
    document = doc(
        "ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ №1 К ДОГОВОРУ "
        "ФИНАНСОВОГО ЛИЗИНГА № AG4/2022/U/L/113039 "
        "от 28 октября 2022 г.",
        "допик.pdf",
    )
    fields = parse_addendum(document)
    values = {item["name"]: item["value"] for item in fields}
    assert values["lease_contract_number"] == "AG4/2022/U/L/113039"


def test_plural_act_package_does_not_invent_single_number():
    document = doc(
        "АКТ ПРИЕМА-ПЕРЕДАЧИ\nАКТ ПРИЕМА-ПЕРЕДАЧИ",
        "F482414776_Акты приема - передачи.pdf",
    )
    fields = parse_acceptance_act(document)
    names = {item["name"] for item in fields}
    assert "act_package_detected" in names
    assert "act_number" not in names
