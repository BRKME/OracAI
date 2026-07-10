"""«К сбору» = живая сумма uncollected по позициям монитора (10.07.2026).
Не поле snapshot['fees'] (ловит момент после poke, занижает) и не
positions_fees_tracking (накопитель, не сбрасывается при harvest, завышает)."""
import re
from lp_system import format_unified_report


def _report(positions, snapshot_fees):
    monitor_data = {"tvl": 21000, "count": len(positions), "in_range": len(positions),
                    "positions": positions, "fees": snapshot_fees,
                    "by_wallet": {}}
    history = [{"timestamp": "2026-07-10T18:00:00+00:00", "fees": snapshot_fees,
                "fees_cumulative": 483.0,
                "positions_fees_tracking": {f"c:{i}": 50.0 for i in range(25)}}]
    return format_unified_report(monitor_data, {"portfolio_apy": 111}, None, history)


def test_to_collect_uses_live_positions_not_snapshot_field():
    positions = [{"chain": "bsc", "token_id": str(i), "uncollected_fees_usd": 12.0}
                 for i in range(8)]  # живая сумма = $96
    r = _report(positions, snapshot_fees=15.73)  # поле занижает
    m = re.search(r"Fees: \$([\d,]+\.\d+) к сбору", r)
    assert m, r
    assert abs(float(m.group(1).replace(",", "")) - 96.0) < 0.5, m.group(1)


def test_falls_back_to_field_when_no_positions():
    r = _report([], snapshot_fees=42.0)
    assert "42" in r
