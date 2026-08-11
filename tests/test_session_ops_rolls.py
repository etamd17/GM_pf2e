from services import session_ops_rolls as rolls


def test_check_result_includes_pf2e_degree_and_natural_adjustment():
    success = rolls.roll_check(8, 20, d20=12)
    assert success["total"] == 20
    assert success["degree"] == "success"

    natural_twenty = rolls.roll_check(0, 30, d20=20)
    assert natural_twenty["degree"] == "failure"

    natural_one = rolls.roll_check(20, 20, d20=1)
    assert natural_one["degree"] == "failure"


def test_damage_expression_is_bounded_and_structured(monkeypatch):
    monkeypatch.setattr(rolls.random, "randint", lambda _low, high: high)
    result = rolls.roll_damage("2d6+3 fire")
    assert result["formula"] == "2d6+3"
    assert result["total"] == 15
    assert result["dice"] == [{"sides": 6, "value": 6}, {"sides": 6, "value": 6}]


def test_unsafe_or_invalid_damage_expression_is_rejected():
    for expression in ("not dice", "0d6", "101d6", "1d1"):
        try:
            rolls.roll_damage(expression)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {expression}")
