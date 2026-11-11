"""Журнал отправленных решений и счёт против простого удержания.

Аудит 11.11.2026: доля из сообщения нигде не сохранялась, и на вопрос
«зарабатывает ли стратегия» ответа не было — пришлось восстанавливать.
"""
import pytest

import signal_log as sl


def _fill(path, rows):
    for d, price, target in rows:
        sl.append_signal(path, date=d, btc_price=price, target_pos=target,
                         label="⚪ ДЕРЖАТЬ", regime="BULL", confidence=0.5)


def test_one_row_per_day_last_wins(tmp_path):
    p = tmp_path / "s.csv"
    _fill(p, [("2026-11-01", 100, 0.9), ("2026-11-01", 101, 0.6)])
    rows = sl.load(p)
    assert len(rows) == 1 and float(rows[0]["target_pos"]) == 0.6


def test_performance_against_hold(tmp_path):
    p = tmp_path / "s.csv"
    # день 1→2: +10% при доле 100%; день 2→3: −10% при доле 0%
    _fill(p, [("2026-11-01", 100, 1.0), ("2026-11-02", 110, 0.0), ("2026-11-03", 99, 0.0)])
    r = sl.performance(p, cost=0.0)
    assert r["hold_pct"] == pytest.approx(-1.0)
    assert r["strategy_pct"] == pytest.approx(10.0)
    assert r["days"] == 2 and r["since"] == "2026-11-01"


def test_cost_is_charged_on_turnover(tmp_path):
    p = tmp_path / "s.csv"
    _fill(p, [("2026-11-01", 100, 1.0), ("2026-11-02", 100, 0.0), ("2026-11-03", 100, 0.0)])
    assert sl.performance(p, cost=0.001)["strategy_pct"] == pytest.approx(-0.1)


def test_too_short_gives_none(tmp_path):
    p = tmp_path / "s.csv"
    assert sl.performance(p) is None
    _fill(p, [("2026-11-01", 100, 1.0)])
    assert sl.performance(p) is None


def test_score_line(tmp_path):
    p = tmp_path / "s.csv"
    _fill(p, [("2026-11-01", 100, 1.0), ("2026-11-02", 110, 0.0), ("2026-11-03", 99, 0.0)])
    line = sl.score_line(p, cost=0.0)
    assert "01.11" in line and "+10.0%" in line and "−1.0%" in line


def test_days_without_price_are_skipped(tmp_path):
    p = tmp_path / "s.csv"
    _fill(p, [("2026-11-01", 100, 1.0), ("2026-11-02", float("nan"), 1.0),
              ("2026-11-03", 110, 1.0)])
    assert sl.performance(p, cost=0.0)["strategy_pct"] == pytest.approx(10.0)
