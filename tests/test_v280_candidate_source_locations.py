from app.services.source_locator import (
    enrich_field_locations,
    locate_value,
    unresolved_pages,
)


def layout():
    return [{
        "page": 3,
        "width": 600,
        "height": 800,
        "words": [
            {"text": "Заемщик", "x0": 20, "y0": 100, "x1": 90, "y1": 115},
            {"text": "БИН", "x0": 100, "y0": 100, "x1": 125, "y1": 115},
            {"text": "790", "x0": 130, "y0": 100, "x1": 155, "y1": 115},
            {"text": "105", "x0": 160, "y0": 100, "x1": 185, "y1": 115},
            {"text": "403", "x0": 190, "y0": 100, "x1": 215, "y1": 115},
            {"text": "331", "x0": 220, "y0": 100, "x1": 245, "y1": 115},
        ],
    }]


def test_locates_candidate_split_across_words():
    found = locate_value(layout(), "790105403331")
    assert found
    assert found[0]["page"] == 3
    assert "Заемщик" in found[0]["quote"]


def test_enriches_candidate_list_with_locations():
    fields = [{
        "name": "iin_bin_candidates",
        "label_ru": "Неопределённые ИИН/БИН",
        "value": ["790105403331", "111111111111"],
        "status": "candidate",
    }]
    enriched = enrich_field_locations(fields, layout())
    assert "790105403331" in enriched[0]["source_locations"]
    assert "111111111111" not in enriched[0]["source_locations"]


def test_marks_low_quality_page_as_unresolved():
    items = unresolved_pages([{
        "page": 2,
        "quality": 0.31,
        "char_count": 25,
        "method": "ocr",
        "layout_word_count": 0,
    }], "unknown")
    assert items
    assert items[0]["page"] == 2
