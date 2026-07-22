from app.services.source_preview import find_highlight_boxes


def test_find_highlight_box_for_identifier():
    layout = {
        "width": 1000,
        "height": 1400,
        "words": [
            {"text": "БИН", "x0": 10, "y0": 20, "x1": 50, "y1": 40},
            {"text": "790105403331", "x0": 60, "y0": 20, "x1": 170, "y1": 40},
        ],
    }
    boxes = find_highlight_boxes(layout, "790105403331")
    assert boxes == [(60.0, 20.0, 170.0, 40.0)]


def test_find_highlight_box_for_phrase():
    layout = {
        "words": [
            {"text": "Договор", "x0": 10, "y0": 20, "x1": 70, "y1": 40},
            {"text": "лизинга", "x0": 75, "y0": 20, "x1": 140, "y1": 40},
            {"text": "№123", "x0": 145, "y0": 20, "x1": 190, "y1": 40},
        ]
    }
    boxes = find_highlight_boxes(layout, "Договор лизинга")
    assert boxes and boxes[0][0] == 10.0 and boxes[0][2] == 140.0
