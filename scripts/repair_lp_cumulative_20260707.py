"""Ремонт lp_history после фантома 07.07 (arbitrum:5571457, $1,783.90).
Пересобирает fees_cumulative с момента per-position reset теми же правилами,
что боевой add_snapshot, плюс фантом-фильтр: пофиционная дельта за шаг > $50
— глитч, в копилку идёт 0. Отравленный ключ удаляется из трекинга последнего
снапшота (следующий прогон с исправленной математикой заведёт baseline)."""
import json

PHANTOM_STEP_USD = 50.0
PATH = "state/lp_history.json"

h = json.load(open(PATH))
snaps = h["snapshots"] if isinstance(h, dict) else h
start = next(i for i, s in enumerate(snaps) if s.get("positions_fees_tracking"))
prev_pf, cum, touched = {}, 0.0, 0
for s in snaps[start:]:
    pf = s.get("positions_fees_tracking", {})
    delta_total = 0.0
    for key, cur in pf.items():
        if key in prev_pf:
            d = cur - prev_pf[key]
            if d < 0:
                d = cur
            if d > PHANTOM_STEP_USD:
                print(f"  глитч отброшен: {s.get('timestamp','')[:16]} {key} +${d:,.2f}")
                d = 0.0
            delta_total += d
    cum += max(0.0, delta_total)
    if abs(s.get("fees_cumulative", 0) - cum) > 0.005:
        touched += 1
    s["fees_cumulative"] = round(cum, 2)
    prev_pf = dict(prev_pf); prev_pf.update(pf)

removed = snaps[-1].get("positions_fees_tracking", {}).pop("arbitrum:5571457", None)
json.dump(h, open(PATH, "w"), ensure_ascii=False, indent=1)
print(f"снапшотов исправлено: {touched} · cumulative: ${cum:,.2f} · "
      f"ключ удалён (был ${removed or 0:,.2f})")
