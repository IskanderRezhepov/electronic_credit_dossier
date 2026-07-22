
from app.parsers.base import valid_contract_number


def test_rejects_ocr_words():
    assert not valid_contract_number("1-КОСЫМША")
    assert not valid_contract_number("ҚОСЫМША")
    assert not valid_contract_number("ДОГОВОР")


def test_accepts_real_contract_numbers():
    assert valid_contract_number("AQ5/2024/U/S/106529")
    assert valid_contract_number("640/BL/15-07")
    assert valid_contract_number("KAZ14112024KTL")
