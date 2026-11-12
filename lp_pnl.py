"""Прибыль LP с учётом вводов и выводов.

Журнал state/lp_flows.csv ведёт Claude со слов оператора: оператор пишет в
чат «вывел $4,000 из MMA_1», Claude дописывает строку и пушит. Знак суммы:
плюс — деньги пришли в LP, минус — ушли из LP.

Прибыль за период = TVL на конец − TVL на старте − чистый ввод за период.
В неё входит всё: комиссии, IL и движение цены токенов. Старт — последний
снимок истории за день `since` (конец дня), поэтому поток того же дня уже
сидит в стартовом TVL и в период не входит.
"""
import csv
from pathlib import Path

FLOWS_FILE = Path("state/lp_flows.csv")
FIELDS = ["date", "amount_usd", "wallet", "note"]
PNL_SINCE = "2026-11-11"     # с этого дня журнал ведётся (до него потоков не знаем)
JUMP_SHARE = 0.15            # дневное изменение TVL больше — спросить про поток


def load_flows(path=FLOWS_FILE) -> list:
    try:
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        return []
    for r in rows:
        r["amount_usd"] = float(r["amount_usd"])
    return sorted(rows, key=lambda r: r["date"])


def add_flow(path=FLOWS_FILE, *, date: str, amount_usd: float, wallet: str = "",
             note: str = "") -> None:
    rows = load_flows(path)
    rows.append({"date": date, "amount_usd": float(amount_usd),
                 "wallet": wallet, "note": note})
    rows.sort(key=lambda r: r["date"])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "amount_usd": f"{r['amount_usd']:.2f}"})


def _day_end(history: list) -> dict:
    """date -> TVL последнего снимка за день."""
    out = {}
    for s in sorted(history, key=lambda s: s.get("timestamp", s.get("date", ""))):
        if s.get("date") and s.get("tvl") is not None:
            out[s["date"]] = float(s["tvl"])
    return out


def pnl(history: list, flows: list, since: str = PNL_SINCE):
    days = _day_end(history)
    start_days = [d for d in days if d <= since]
    if not start_days or not days:
        return None
    d0, d1 = max(start_days), max(days)
    if d1 <= d0:
        return None
    period = [f for f in flows if d0 < f["date"] <= d1]
    net = sum(f["amount_usd"] for f in period)
    start, end = days[d0], days[d1]
    return {"since": d0, "until": d1, "start_tvl": start, "end_tvl": end,
            "net_flows": net, "deposits": sum(f["amount_usd"] for f in period if f["amount_usd"] > 0),
            "withdrawals": -sum(f["amount_usd"] for f in period if f["amount_usd"] < 0),
            "pnl": end - start - net}


def unexplained_jump(history: list, flows: list, since: str = PNL_SINCE):
    """Последний день, где TVL изменился больше чем на JUMP_SHARE без записи
    в журнале. (дата, изменение $) или None."""
    days = _day_end(history)
    keys = sorted(d for d in days if d >= since)
    flow_days = {f["date"] for f in flows}
    found = None
    prev = max((d for d in days if d < since), default=None)
    for d in keys:
        if prev is not None and days[prev] > 0:
            ch = days[d] - days[prev]
            if abs(ch) / days[prev] > JUMP_SHARE and d not in flow_days:
                found = (d, ch)
        prev = d
    return found


def _usd(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}${abs(x):,.0f}"


def report_line(history: list, flows: list, since: str = PNL_SINCE) -> str:
    """Строка для отчёта LP. Пусто — считать пока не из чего."""
    jump = unexplained_jump(history, flows, since)
    if jump:
        d, ch = jump
        return (f"⚠️ TVL {_usd(ch)} за {d[8:10]}.{d[5:7]} без записи о вводе/выводе — "
                f"если двигал деньги, напиши Claude сумму, иначе прибыль врёт")
    r = pnl(history, flows, since)
    if not r:
        return ""
    s = r["since"]
    parts = [f"💼 Итог LP с {s[8:10]}.{s[5:7]}: {_usd(r['pnl'])}"]
    if r["start_tvl"]:
        parts[0] += f" ({r['pnl'] / r['start_tvl'] * 100:+.1f}%)"
    if r["withdrawals"]:
        parts.append(f"вывод ${r['withdrawals']:,.0f}")
    if r["deposits"]:
        parts.append(f"ввод ${r['deposits']:,.0f}")
    return " · ".join(parts)
