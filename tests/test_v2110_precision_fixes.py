from app.services.source_locator import locate_value


def test_source_locations_are_deduplicated():
    layouts = [{
        "page": 1,
        "words": [
            {"text": "ИИК", "x0": 10, "y0": 10, "x1": 25, "y1": 20},
            {"text": "KZ298562203134304780", "x0": 30, "y0": 10, "x1": 180, "y1": 20},
            {"text": "KZ298562203134304780", "x0": 30.5, "y0": 10.5, "x1": 180.5, "y1": 20.5},
        ],
    }]
    found = locate_value(layouts, "KZ298562203134304780")
    assert len(found) == 1
    assert found[0]["page"] == 1


def test_partial_token_is_not_a_location():
    layouts = [{
        "page": 1,
        "words": [{"text": "KZ29", "x0": 10, "y0": 10, "x1": 40, "y1": 20}],
    }]
    assert locate_value(layouts, "KZ298562203134304780") == []
