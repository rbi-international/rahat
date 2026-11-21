import pytest

from rahat.ledger import BudgetExhausted, Ledger
from rahat.router import Router


def test_ledger_refuses_once_the_allocation_is_spent():
    led = Ledger(allocation=1000)
    held = led.reserve(600)
    led.commit(held, 700, "intake")
    assert led.remaining == 300
    with pytest.raises(BudgetExhausted):
        led.reserve(400)


def test_ledger_reports_low_budget():
    led = Ledger(allocation=1000, low_ratio=0.1)
    led.commit(0, 950, "triage")
    assert led.low


def test_router_learns_the_cheap_model_is_good_enough():
    r = Router()
    for _ in range(60):
        r.reward("simple", "fast", True)
        r.reward("simple", "strong", True)
    picks = [r.choose("simple") for _ in range(200)]
    assert picks.count("fast") > 150  # equal accuracy, cheaper: fast should win


def test_router_moves_to_the_strong_model_when_officers_reject_fast():
    r = Router()
    for _ in range(40):
        r.reward("complex", "fast", False)
        r.reward("complex", "strong", True)
    picks = [r.choose("complex") for _ in range(200)]
    assert picks.count("strong") > 150
