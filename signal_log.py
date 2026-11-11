"""Журнал отправленных решений (доля BTC из ежедневного сообщения) и счёт
стратегии против простого удержания.

Одна строка на день: то, что реально ушло оператору. Доходность считается
от цены в момент одного сообщения до цены в момент следующего — ровно то,
что получил бы человек, исполняющий сообщения.
"""
import csv
from pathlib import Path

LOG_FILE = Path("state/signal_log.csv")
FIELDS = ["date", "btc_price", "target_pos", "label", "regime", "confidence", "source"]
COST = 0.001   # издержки на единицу оборота


def load(path=LOG_FILE) -> list:
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def append_signal(path=LOG_FILE, *, date, btc_price, target_pos, label,
                  regime, confidence, source="sent") -> None:
    """Записать решение дня. Повтор за тот же день заменяет строку."""
    rows = [r for r in load(path) if r["date"] != date]
    rows.append({"date": date, "btc_price": btc_price, "target_pos": target_pos,
                 "label": label, "regime": regime, "confidence": confidence,
                 "source": source})
    rows.sort(key=lambda r: r["date"])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def performance(path=LOG_FILE, cost: float = COST):
    """Итог стратегии и удержания в процентах. None — меньше двух записей."""
    rows = [r for r in load(path) if _num(r["btc_price"]) and _num(r["target_pos"])]
    if len(rows) < 2:
        return None
    price = [float(r["btc_price"]) for r in rows]
    pos = [float(r["target_pos"]) for r in rows]
    eq, prev = 1.0, pos[0]
    for i in range(len(rows) - 1):
        eq *= 1 + pos[i] * (price[i + 1] / price[i] - 1) - abs(pos[i] - prev) * cost
        prev = pos[i]
    return {"since": rows[0]["date"], "days": len(rows) - 1,
            "strategy_pct": (eq - 1) * 100,
            "hold_pct": (price[-1] / price[0] - 1) * 100,
            "avg_pos": sum(pos[:-1]) / (len(pos) - 1)}


def _num(v) -> bool:
    """Число, пригодное для счёта (дни сбоя данных пишутся без цены)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x and x > 0 or x == 0.0


def _pct(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}{abs(x):.1f}%"


def score_line(path=LOG_FILE, cost: float = COST) -> str:
    """Строка для сообщения: счёт с начала журнала. Пусто — считать не из чего."""
    r = performance(path, cost)
    if not r:
        return ""
    d = r["since"]
    return (f"Счёт с {d[8:10]}.{d[5:7]}: стратегия {_pct(r['strategy_pct'])} · "
            f"держать {_pct(r['hold_pct'])}")
