"""Расчётный дневной fee — скользящие 30 дней по дельте fees_cumulative.
Гарды: окно не пересекает reset-границу (до 02.07 cumulative был legacy-
надутым); при истории короче 30д — честная подпись фактического окна."""
import pytest
from datetime import datetime, timedelta, timezone
from lp_system import daily_fee_rate


def _snap(days_ago, cum, tracked=True):
    ts = (datetime(2026, 7, 7, 12, tzinfo=timezone.utc) - timedelta(days=days_ago))
    s = {"timestamp": ts.isoformat(), "fees_cumulative": cum}
    if tracked:
        s["positions_fees_tracking"] = {"x:1": 1.0}
    return s


NOW = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)


def test_simple_rate():
    snaps = [_snap(10, 0.0), _snap(5, 50.0), _snap(0, 100.0)]
    rate, days = daily_fee_rate(snaps, now=NOW)
    assert rate == pytest.approx(10.0)   # 100$ за 10 дней
    assert days == pytest.approx(10.0)


def test_window_capped_at_30d():
    snaps = [_snap(45, 0.0), _snap(30, 100.0), _snap(0, 400.0)]
    rate, days = daily_fee_rate(snaps, now=NOW)
    assert days <= 30.0 + 1e-9
    assert rate == pytest.approx(10.0)   # (400-100)/30


def test_pre_reset_snapshots_ignored():
    """До reset cumulative legacy-надутый — в окно не входит."""
    snaps = [_snap(20, 5999.0, tracked=False),   # legacy
             _snap(5, 0.0), _snap(0, 130.0)]
    rate, days = daily_fee_rate(snaps, now=NOW)
    assert rate == pytest.approx(26.0) and days == pytest.approx(5.0)


def test_too_short_span_returns_none():
    snaps = [_snap(0.01, 1.0), _snap(0, 2.0)]
    assert daily_fee_rate(snaps, now=NOW) is None


def test_empty_history():
    assert daily_fee_rate([], now=NOW) is None
