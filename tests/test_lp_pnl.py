"""Прибыль LP с учётом вводов и выводов (журнал state/lp_flows.csv).

Оператор пишет вводы/выводы в чат, Claude заносит их в журнал. Прибыль =
TVL сейчас − TVL на старте − чистый ввод. Без журнала падение TVL от вывода
выглядело убытком, а доливка — прибылью.
"""
import pytest

import lp_pnl


def _snap(date, tvl, ts=None):
    return {"date": date, "timestamp": ts or f"{date}T23:00:00+00:00", "tvl": tvl}


HIST = [_snap("2026-11-10", 16000), _snap("2026-11-11", 15600),
        _snap("2026-11-12", 11700), _snap("2026-11-13", 11900)]


def _flows(tmp_path, rows):
    p = tmp_path / "f.csv"
    for r in rows:
        lp_pnl.add_flow(p, **r)
    return p


def test_withdrawal_is_not_a_loss(tmp_path):
    f = _flows(tmp_path, [dict(date="2026-11-12", amount_usd=-4000, wallet="MMA_1",
                               note="вывод")])
    r = lp_pnl.pnl(HIST, lp_pnl.load_flows(f), since="2026-11-11")
    assert r["net_flows"] == pytest.approx(-4000)
    assert r["pnl"] == pytest.approx(11900 - 15600 + 4000)        # +300
    assert r["start_tvl"] == 15600 and r["end_tvl"] == 11900


def test_deposit_is_not_a_profit(tmp_path):
    f = _flows(tmp_path, [dict(date="2026-11-13", amount_usd=1000, wallet="MMA_3",
                               note="доливка")])
    hist = HIST[:3] + [_snap("2026-11-13", 12900)]
    r = lp_pnl.pnl(hist, lp_pnl.load_flows(f), since="2026-11-12")
    assert r["pnl"] == pytest.approx(12900 - 11700 - 1000)        # 200


def test_flow_on_start_day_is_already_in_start_tvl(tmp_path):
    """Старт — TVL на конец дня `since`: поток того же дня в нём уже учтён."""
    f = _flows(tmp_path, [dict(date="2026-11-12", amount_usd=-4000, wallet="MMA_1",
                               note="вывод")])
    r = lp_pnl.pnl(HIST, lp_pnl.load_flows(f), since="2026-11-12")
    assert r["net_flows"] == 0 and r["pnl"] == pytest.approx(200)


def test_ledger_keeps_order_and_notes(tmp_path):
    f = _flows(tmp_path, [dict(date="2026-11-13", amount_usd=500, wallet="MMA_3", note="b"),
                          dict(date="2026-11-12", amount_usd=-4000, wallet="MMA_1", note="a")])
    rows = lp_pnl.load_flows(f)
    assert [r["date"] for r in rows] == ["2026-11-12", "2026-11-13"]
    assert rows[0]["note"] == "a"


def test_unexplained_drop_is_flagged(tmp_path):
    """Падение TVL больше 15% за день без записи — спросить оператора."""
    assert lp_pnl.unexplained_jump(HIST, []) == ("2026-11-12", pytest.approx(-3900))
    f = _flows(tmp_path, [dict(date="2026-11-12", amount_usd=-4000, wallet="MMA_1",
                               note="вывод")])
    assert lp_pnl.unexplained_jump(HIST, lp_pnl.load_flows(f)) is None


def test_report_line(tmp_path):
    f = _flows(tmp_path, [dict(date="2026-11-12", amount_usd=-4000, wallet="MMA_1",
                               note="вывод")])
    line = lp_pnl.report_line(HIST, lp_pnl.load_flows(f), since="2026-11-11")
    assert "с 11.11" in line and "+$300" in line and "вывод $4,000" in line


def test_report_line_asks_when_drop_unexplained():
    line = lp_pnl.report_line(HIST, [], since="2026-11-11")
    assert "12.11" in line and "Claude" in line


def test_lp_report_carries_pnl_line(tmp_path, monkeypatch):
    from lp_system import format_unified_report
    monkeypatch.setattr(lp_pnl, "FLOWS_FILE", tmp_path / "none.csv")
    md = {"tvl": 11900, "count": 1, "in_range": 1, "positions": [], "fees": 1.0,
          "by_wallet": {}}
    r = format_unified_report(md, None, HIST)
    assert "без записи о вводе/выводе" in r


def test_lp_report_survives_without_failed_wallets_line(tmp_path, monkeypatch):
    from lp_system import format_unified_report
    monkeypatch.setattr(lp_pnl, "FLOWS_FILE", tmp_path / "none.csv")
    md = {"tvl": 11900, "count": 1, "in_range": 1, "positions": [], "fees": 1.0,
          "by_wallet": {}, "failed_wallets": ["MMA_3"]}
    r = format_unified_report(md, None, HIST)
    assert "Итог LP" not in r and "без записи" not in r   # TVL неполный — не считаем
