"""
Tests for ladder v1.1 — event-based percent-of-capital signals.

v1 emitted a weekly DCA multiplier, which required defining a base stake in
dollars. v1.1 replaces it with one-time signals on ZONE ENTRY expressed as a
fraction of capital ("buy 30% of capital"), so no concrete sums exist anywhere
and the deploy fractions must sum to <= 1.0 across a full descent.

Run: python -m pytest tests/test_ladder_signals.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cycle_ladder as cl


def _sig(zone, prev_zone, call="AMBIGUOUS", days_above=0, state=None):
    return cl.compute_signal(zone=zone, prev_zone=prev_zone,
                             drawdown_call=call,
                             days_above_sma200=days_above,
                             state=state or {})


class TestBuySignalsFireOnEntry:
    def test_entering_neutral_buys_30pct(self):
        s = _sig("NEUTRAL", prev_zone="EXPANSION")
        assert s["action"] == "BUY"
        assert s["fraction_of_capital"] == 0.30

    def test_entering_accumulation_buys_30pct(self):
        s = _sig("ACCUMULATION", prev_zone="NEUTRAL",
                 call="CAPITULATION_VALUE_ZONE")
        assert s["action"] == "BUY"
        assert s["fraction_of_capital"] == 0.30

    def test_staying_in_zone_holds(self):
        s = _sig("NEUTRAL", prev_zone="NEUTRAL")
        assert s["action"] == "HOLD"
        assert s["fraction_of_capital"] == 0.0

    def test_signal_not_repeated_if_zone_already_bought(self):
        # left NEUTRAL and came back — the 30% for NEUTRAL was already spent
        state = {"bought_zones": ["NEUTRAL"]}
        s = _sig("NEUTRAL", prev_zone="EXPANSION", state=state)
        assert s["action"] == "HOLD"


class TestStructuralBearGuard:
    def test_neutral_entry_under_structural_bear_is_half_step(self):
        s = _sig("NEUTRAL", prev_zone="EXPANSION", call="STRUCTURAL_BEAR_RISK")
        assert s["action"] == "BUY"
        assert s["fraction_of_capital"] == 0.15   # half of 30%

    def test_accumulation_full_even_in_structural_bear(self):
        s = _sig("ACCUMULATION", prev_zone="NEUTRAL",
                 call="STRUCTURAL_BEAR_RISK")
        assert s["fraction_of_capital"] == 0.30


class TestReRisk:
    def test_re_risk_deploys_remainder(self):
        state = {"bought_zones": ["NEUTRAL", "ACCUMULATION"]}
        s = _sig("EXPANSION", prev_zone="EXPANSION", days_above=10, state=state)
        assert s["action"] == "BUY"
        # 1.0 - 0.30 - 0.30 = 0.40 remainder
        assert abs(s["fraction_of_capital"] - 0.40) < 1e-9
        assert s["trigger"] == "re_risk_sma200"

    def test_re_risk_fires_once(self):
        state = {"bought_zones": ["NEUTRAL"], "re_risk_fired": True}
        s = _sig("EXPANSION", prev_zone="EXPANSION", days_above=30, state=state)
        assert s["action"] == "HOLD"

    def test_budget_never_exceeds_one(self):
        # nothing bought before; re-risk deploys the FULL remainder = 1.0 max
        s = _sig("EXPANSION", prev_zone="EXPANSION", days_above=10, state={})
        assert s["fraction_of_capital"] <= 1.0


class TestSellSignals:
    def test_entering_distribution_sells_quarter(self):
        s = _sig("DISTRIBUTION", prev_zone="EXPANSION",
                 call="NOT_IN_DRAWDOWN")
        assert s["action"] == "SELL"
        assert s["fraction_of_stack"] == 0.25

    def test_entering_euphoria_sells_half(self):
        s = _sig("EUPHORIA", prev_zone="DISTRIBUTION", call="NOT_IN_DRAWDOWN")
        assert s["action"] == "SELL"
        assert s["fraction_of_stack"] == 0.50

    def test_sell_not_repeated(self):
        state = {"sold_zones": ["DISTRIBUTION"]}
        s = _sig("DISTRIBUTION", prev_zone="EXPANSION",
                 call="NOT_IN_DRAWDOWN", state=state)
        assert s["action"] == "HOLD"

    def test_sell_resets_buy_budget_for_next_cycle(self):
        # selling in euphoria means next descent starts a fresh buy budget
        state = {"bought_zones": ["NEUTRAL", "ACCUMULATION"], "re_risk_fired": True}
        s = _sig("EUPHORIA", prev_zone="DISTRIBUTION",
                 call="NOT_IN_DRAWDOWN", state=state)
        assert s["new_state"]["bought_zones"] == []
        assert s["new_state"]["re_risk_fired"] is False


class TestStatePropagation:
    def test_buy_records_zone_in_new_state(self):
        s = _sig("NEUTRAL", prev_zone="EXPANSION")
        assert "NEUTRAL" in s["new_state"]["bought_zones"]

    def test_hold_keeps_state(self):
        state = {"bought_zones": ["NEUTRAL"]}
        s = _sig("NEUTRAL", prev_zone="NEUTRAL", state=state)
        assert s["new_state"]["bought_zones"] == ["NEUTRAL"]

    def test_contract_shape(self):
        s = _sig("NEUTRAL", prev_zone="EXPANSION")
        for k in ("action", "fraction_of_capital", "fraction_of_stack",
                  "trigger", "rationale", "policy_version", "new_state"):
            assert k in s


class TestSpentTracking:
    def test_half_step_remainder_accounts_actual_spend(self):
        # NEUTRAL entered under structural bear -> only 15% spent
        s1 = _sig("NEUTRAL", prev_zone="EXPANSION", call="STRUCTURAL_BEAR_RISK")
        assert s1["fraction_of_capital"] == 0.15
        st = s1["new_state"]
        # later re-risk must deploy 1.0 - 0.15 = 0.85, not 1.0 - 0.30 = 0.70
        s2 = _sig("EXPANSION", prev_zone="EXPANSION", days_above=10, state=st)
        assert abs(s2["fraction_of_capital"] - 0.85) < 1e-9
