
from app.services.candidate_resolver import _explicit_role_score


def test_role_regex_is_a_string_and_does_not_crash():
    context = "ПРОДАВЕЦ: ТОО ANTO MOTORS БИН 241240023483"
    score = _explicit_role_score(context, "seller", "241240023483")
    assert score > 0.6


def test_unrelated_role_scores_lower():
    context = "ПРОДАВЕЦ: ТОО ANTO MOTORS БИН 241240023483"
    seller_score = _explicit_role_score(context, "seller", "241240023483")
    buyer_score = _explicit_role_score(context, "buyer", "241240023483")
    assert seller_score > buyer_score
