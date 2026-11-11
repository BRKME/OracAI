"""Ремонт lp_history после бага «переоценка = сбор» (аудит 11.11.2026).

Любое уменьшение несобранных комиссий считалось сбором, и вся сумма
доливалась в копилку заново. Пересобираем fees_cumulative по сохранённым
дневным снимкам правилом lp_system.fee_delta (то же, что теперь в бою).

Результат — НИЖНЯЯ граница: комиссии, набежавшие и собранные внутри одних
суток, по дневным снимкам не видны. Прежнее значение сохраняется в
fees_cumulative_inflated. Базисы недели и месяца переставляются на новые
значения, иначе «за неделю» ушло бы в ноль до конца периода.
"""
import json
import sys

sys.path.insert(0, ".")
from lp_system import fee_delta  # noqa: E402

PATH = "state/lp_history.json"
PERIOD = "state/lp_fees_period.json"

h = json.load(open(PATH))
snaps = h["snapshots"]
if any("fees_cumulative_inflated" in s for s in snaps):
    print("уже отремонтировано — пропуск")
    sys.exit(0)

prev, cum, debt = {}, 0.0, 0.0
for s in snaps:
    pf = s.get("positions_fees_tracking", {})
    d = sum(fee_delta(prev[k], v) for k, v in pf.items() if k in prev) - debt
    debt = -d if d < 0 else 0.0
    cum += max(0.0, d)
    s["fees_cumulative_inflated"] = s.get("fees_cumulative", 0.0)
    s["fees_cumulative"] = round(cum, 2)
    s["fees_reval_debt"] = round(debt, 4)
    prev = {**prev, **pf}

by_date = {s["date"]: s["fees_cumulative"] for s in snaps}
try:
    p = json.load(open(PERIOD))
    for k, e in p.items():
        if e.get("start") in by_date:
            e["baseline"] = by_date[e["start"]]
    json.dump(p, open(PERIOD, "w"), indent=2)
except FileNotFoundError:
    pass

json.dump(h, open(PATH, "w"), indent=2)
print(f"снимков: {len(snaps)} · было ${snaps[-1]['fees_cumulative_inflated']:,.0f} "
      f"· стало ${cum:,.0f} (нижняя граница)")
