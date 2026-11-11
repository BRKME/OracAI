"""
Tests for analyze_prod_log.py — the pre-registered validation methodology for
the prod-log window. Pure functions tested offline; the script itself just
wires them to state/prod_log.csv + price history.

Run: python -m pytest tests/test_analyze_prod_log.py -v
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyze_prod_log as ap


def _prices(vals, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(vals), freq="D")
    return pd.Series(vals, index=idx)


class TestForwardReturns:
    def test_forward_return_simple(self):
        p = _prices([100, 110, 121, 121, 121, 121, 121, 133.1])
        # from day0, 7d forward: 133.1/100 - 1 = 33.1%
        r = ap.forward_return(p, p.index[0], days=7)
        assert abs(r - 0.331) < 1e-6

    def test_forward_return_none_past_end(self):
        p = _prices([100, 101, 102])
        assert ap.forward_return(p, p.index[1], days=7) is None


class TestNonOverlapping:
    def test_thin_to_non_overlapping(self):
        # daily rows, 7d windows -> keep every 7th row
        idx = pd.date_range("2026-01-01", periods=21, freq="D")
        df = pd.DataFrame({"timestamp_utc": idx, "regime": ["BULL"] * 21})
        out = ap.non_overlapping(df, window_days=7)
        assert len(out) == 3
        # consecutive kept rows are >= 7 days apart
        gaps = out["timestamp_utc"].diff().dropna().dt.days
        assert (gaps >= 7).all()


class TestSma200Baseline:
    def test_baseline_exposure_above_below(self):
        # 250 days: first 200 flat 100 (SMA=100), then 10 above, then 40 below
        vals = [100.0] * 200 + [110.0] * 10 + [80.0] * 40
        p = _prices(vals)
        expo = ap.sma200_baseline_exposure(p)
        # above SMA -> 1.0; below -> the defensive cap
        assert expo.iloc[205] == 1.0
        assert expo.iloc[-1] == ap.BASELINE_DEFENSIVE_EXPOSURE

    def test_equity_curve_protects_in_drop(self):
        vals = [100.0] * 200 + [110.0] * 5 + list(np.linspace(110, 60, 45))
        p = _prices(vals)
        expo = ap.sma200_baseline_exposure(p)
        eq = ap.equity_from_exposure(p, expo)
        hodl = p / p.iloc[0]
        # baseline must lose less than HODL in the drop
        assert eq.iloc[-1] > hodl.iloc[-1]


class TestReEntryCriterion:
    def test_pass_when_target_reached_in_time(self):
        # override fired on day 0; target>=0.95 reached on day 3 -> PASS (N=5)
        rows = pd.DataFrame({
            "timestamp_utc": pd.date_range("2026-01-01", periods=6, freq="D"),
            "recovery_override_would_fire": [True, True, True, True, True, True],
            "exposure_cap": [0.5, 0.7, 0.9, 0.95, 0.95, 0.95],
        })
        res = ap.re_entry_test(rows, target=0.95, within_days=5)
        assert res["status"] == "PASS"
        assert res["days_to_target"] == 3

    def test_fail_when_too_slow(self):
        rows = pd.DataFrame({
            "timestamp_utc": pd.date_range("2026-01-01", periods=8, freq="D"),
            "recovery_override_would_fire": [True] * 8,
            "exposure_cap": [0.5] * 7 + [0.95],
        })
        res = ap.re_entry_test(rows, target=0.95, within_days=5)
        assert res["status"] == "FAIL"

    def test_not_yet_when_override_never_fired(self):
        rows = pd.DataFrame({
            "timestamp_utc": pd.date_range("2026-01-01", periods=5, freq="D"),
            "recovery_override_would_fire": [False] * 5,
            "exposure_cap": [0.35] * 5,
        })
        res = ap.re_entry_test(rows)
        assert res["status"] == "NOT_YET"
