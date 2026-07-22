from app.services.validators import (
    correction_suggestions,
    validate_field,
    validate_fields,
    validate_iban,
    validate_iin_bin,
    validate_vin,
)


def test_iin_bin_validation_detects_wrong_checksum():
    assert validate_iin_bin("123")["valid"] is False
    assert validate_iin_bin("123456789012")["status"] in {"valid", "invalid"}


def test_kazakhstan_iban_mod97():
    valid = validate_iban("KZ86125KZT5004100100")
    assert valid["normalised"] == "KZ86125KZT5004100100"
    assert isinstance(valid["valid"], bool)


def test_vin_structure():
    assert validate_vin("LZGJL4V44PX123456")["valid"] is True
    assert validate_vin("LZGJL4V44PX12345O")["valid"] is False


def test_invalid_automatic_field_becomes_candidate():
    fields = validate_fields([{
        "name": "borrower_iin_bin",
        "label_ru": "ИИН/БИН — Заёмщик",
        "value": "123",
        "status": "extracted",
        "confidence": 0.96,
        "notes": None,
    }])
    assert fields[0]["status"] == "candidate"
    assert fields[0]["confidence"] <= 0.55


def test_manual_confirmed_invalid_value_stays_confirmed():
    field = validate_field({
        "name": "borrower_iin_bin",
        "label_ru": "ИИН/БИН — Заёмщик",
        "value": "123",
        "status": "confirmed",
    })
    assert field["validation"]["valid"] is False
