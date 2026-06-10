"""
Tests for cycle_ladder.py — the policy that converts cycle position into a
concrete weekly-DCA multiplier and reserve/fixation actions.

This is where "understanding the phase" becomes money: a handful of big
pre-committed decisions per cycle (buy harder when below cost basis, take
chips off in distribution, re-risk on the objective SMA200 trigger) instead
of daily regime guessing.

Run: python -m pytest tests/test_cycle_ladder.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cycle_ladder as cl


def _ladder(zone="NEUTRAL", call="AMBIGUOUS", days_above=0, mvrv=1.15):
    return cl.compute_ladder(zone=zone, drawdown_call=call,
                             days_above_sma200=days_above, mvrv=mvrv)


class TestDcaMultipliers:
    def test_accumulation_doubles_dca(self):
        out = _ladder(zone="ACCUMULATION", mvrv=0.9,
                      call="CAPITULATION_VALUE_ZONE")
        assert out["dca_multiplier"] == 2.0

    def test_neutral_is_one_and_half(self):
        out = _ladder(zone="NEUTRAL", mvrv=1.15)
        assert out["dca_multiplier"] == 1.5

    def test_expansion_is_base(self):
        out = _ladder(zone="EXPANSION", mvrv=1.8, call="NOT_IN_DRAWDOWN")
        assert out["dca_multiplier"] == 1.0

    def test_distribution_halves(self):
        out = _ladder(zone="DISTRIBUTION", mvrv=2.4, call="NOT_IN_DRAWDOWN")
        assert out["dca_multiplier"] == 0.5

    def test_euphoria_stops_buys(self):
        out = _ladder(zone="EUPHORIA", mvrv=3.2, call="NOT_IN_DRAWDOWN")
        assert out["dca_multiplier"] == 0.0


class TestStructuralBearGuard:
    def test_neutral_under_structural_bear_capped_to_base(self):
        # 2022 lesson: MVRV 1.0-1.5 didn't prevent the bear when the drawdown
        # started from an overheated top. Don't lean in at 1.5x there.
        out = _ladder(zone="NEUTRAL", call="STRUCTURAL_BEAR_RISK", mvrv=1.3)
        assert out["dca_multiplier"] == 1.0

    def test_accumulation_stays_aggressive_even_in_structural_bear(self):
        # below aggregate cost basis is where you WANT to buy regardless of
        # how the drawdown started — that's the whole point of the ladder.
        out = _ladder(zone="ACCUMULATION", call="STRUCTURAL_BEAR_RISK", mvrv=0.85)
        assert out["dca_multiplier"] == 2.0


class TestReRisk:
    def test_re_risk_fires_at_10_days_above_sma200(self):
        out = _ladder(zone="NEUTRAL", days_above=10)
        assert out["re_risk"] is True

    def test_no_re_risk_below_threshold(self):
        out = _ladder(zone="NEUTRAL", days_above=9)
        assert out["re_risk"] is False

    def test_current_live_state_no_re_risk(self):
        # today: -50% from ATH, below SMA200 (days_above = 0)
        out = _ladder(zone="NEUTRAL", days_above=0, mvrv=1.15)
        assert out["re_risk"] is False


class TestFixation:
    def test_distribution_sets_fixation_tier(self):
        out = _ladder(zone="DISTRIBUTION", mvrv=2.4, call="NOT_IN_DRAWDOWN")
        assert out["fixation_fraction"] == cl.FIXATION_DISTRIBUTION

    def test_euphoria_sets_higher_fixation(self):
        out = _ladder(zone="EUPHORIA", mvrv=3.2, call="NOT_IN_DRAWDOWN")
        assert out["fixation_fraction"] == cl.FIXATION_EUPHORIA
        assert out["fixation_fraction"] > cl.FIXATION_DISTRIBUTION

    def test_no_fixation_in_cheap_zones(self):
        for z in ("ACCUMULATION", "NEUTRAL", "EXPANSION"):
            assert _ladder(zone=z)["fixation_fraction"] == 0.0


class TestContractShape:
    def test_output_carries_everything_planner_needs(self):
        out = _ladder()
        for key in ("dca_multiplier", "re_risk", "fixation_fraction",
                    "zone", "drawdown_call", "rationale", "policy_version"):
            assert key in out

    def test_unknown_zone_is_safe_base(self):
        out = _ladder(zone="UNKNOWN")
        assert out["dca_multiplier"] == 1.0
        assert out["fixation_fraction"] == 0.0
