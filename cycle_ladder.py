"""
cycle_ladder.py — turn cycle position into MONEY decisions.

The audit chain (Phase 3→4, cycle_context validation on 2013-2026) showed two
things at once: daily regime classification adds ~nothing over a one-line
SMA200 rule, while the slow valuation extremes (MVRV/Mayer) HAVE been reliably
distinguishable for 13 years. So the money is not in classifying today better;
it's in a handful of big pre-committed decisions per cycle:

  • buy harder when the market trades below its aggregate cost basis,
  • take chips off when it's visibly overheated,
  • re-risk the reserve on the objective trend trigger (10d above SMA200),
  • and DON'T improvise in the ambiguous middle.

This module encodes those decisions as a ladder: zone → weekly-DCA multiplier
+ fixation fraction + re-risk flag. It's pre-committed precisely so that in
the moment of fear (ACCUMULATION) or euphoria (EUPHORIA) the plan executes
instead of being re-debated.

Published daily as state/cycle_ladder.json — the inter-bot contract consumed
by hl_weekly_planner's Saturday DCA run (raw.githubusercontent fetch, per the
BRKME contract convention).

Policy defaults (v1) — change ONLY via a versioned policy update, never ad hoc:

  zone           MVRV band   DCA mult   fixation
  ACCUMULATION   < 1.0       2.0        0
  NEUTRAL        1.0–1.5     1.5        0      (capped to 1.0 under
                                                STRUCTURAL_BEAR_RISK — the
                                                2022 lesson: cheap-ish didn't
                                                stop a top-driven bear)
  EXPANSION      1.5–2.2     1.0        0
  DISTRIBUTION   2.2–3.0     0.5        0.25   (sell 25% of stack on entry)
  EUPHORIA       >= 3.0      0.0        0.50   (stop buys, sell half)

Re-risk: days_above_sma200 >= 10 → deploy reserve (mirrors the Phase 4
recovery override; same objective trigger, applied to the DCA reserve).
"""
from __future__ import annotations
from typing import Optional, Dict

POLICY_VERSION = "ladder-v1.0"

DCA_MULTIPLIERS = {
    "ACCUMULATION": 2.0,
    "NEUTRAL": 1.5,
    "EXPANSION": 1.0,
    "DISTRIBUTION": 0.5,
    "EUPHORIA": 0.0,
    "UNKNOWN": 1.0,      # no data -> behave like base, never like an extreme
}

FIXATION_DISTRIBUTION = 0.25   # take 25% off when entering DISTRIBUTION
FIXATION_EUPHORIA = 0.50       # take 50% off in EUPHORIA

RE_RISK_DAYS_ABOVE_SMA200 = 10  # same objective trigger as Phase 4 override


def compute_ladder(zone: str,
                   drawdown_call: str = "AMBIGUOUS",
                   days_above_sma200: int = 0,
                   mvrv: Optional[float] = None) -> Dict:
    """Pure policy: cycle reading -> concrete weekly actions.

    Returns the full contract dict the planner consumes. No I/O.
    """
    zone = (zone or "UNKNOWN").upper()
    mult = DCA_MULTIPLIERS.get(zone, 1.0)
    rationale_bits = []

    # The 2022 guard: NEUTRAL (1.0-1.5) looked "cheap-ish" in mid-2022 and the
    # market still halved, because that drawdown began from an overheated top.
    # When the cycle layer flags exactly that pattern, don't lean in at 1.5x —
    # hold base DCA until the market is genuinely below cost basis.
    if zone == "NEUTRAL" and drawdown_call == "STRUCTURAL_BEAR_RISK":
        mult = min(mult, 1.0)
        rationale_bits.append(
            "NEUTRAL при риске структурного медведя — множитель удержан на 1.0 "
            "(урок 2022: 'почти дёшево' не остановило топ-движимый медвежий рынок)")

    fixation = 0.0
    if zone == "DISTRIBUTION":
        fixation = FIXATION_DISTRIBUTION
        rationale_bits.append(
            f"Зона распределения — фиксация {FIXATION_DISTRIBUTION*100:.0f}% "
            "стэка при входе в зону")
    elif zone == "EUPHORIA":
        fixation = FIXATION_EUPHORIA
        rationale_bits.append(
            f"Эйфория — покупки остановлены, фиксация {FIXATION_EUPHORIA*100:.0f}%")

    re_risk = days_above_sma200 >= RE_RISK_DAYS_ABOVE_SMA200
    if re_risk:
        rationale_bits.append(
            f"{days_above_sma200}д выше SMA200 (порог {RE_RISK_DAYS_ABOVE_SMA200}) "
            "— объективный триггер: разворачивай резерв")

    if not rationale_bits:
        rationale_bits.append(
            f"Зона {zone}: недельный DCA ×{mult:g} по лестнице, без фиксаций")

    return {
        "policy_version": POLICY_VERSION,
        "zone": zone,
        "drawdown_call": drawdown_call,
        "mvrv": mvrv,
        "dca_multiplier": mult,
        "fixation_fraction": fixation,
        "re_risk": re_risk,
        "days_above_sma200": int(days_above_sma200 or 0),
        "rationale": "; ".join(rationale_bits),
    }


# ════════════════════════════════════════════════════════════════════
# v1.1 — event-based percent-of-capital signals
# ════════════════════════════════════════════════════════════════════
# v1's weekly multiplier required defining a base stake in dollars. v1.1
# replaces it with ONE-TIME signals on zone entry, expressed as a fraction of
# capital ("buy 30% of capital"). No concrete sums exist anywhere; the buy
# fractions are budgeted to sum to <=1.0 across a full descent, and selling in
# EUPHORIA resets the budget for the next cycle.

SIGNAL_POLICY_VERSION = "ladder-v1.1"

BUY_ON_ENTRY = {            # one-time, % of capital, on entering the zone
    "NEUTRAL": 0.30,
    "ACCUMULATION": 0.30,
}
STRUCTURAL_BEAR_HALF_STEP = 0.5   # NEUTRAL entry under top-driven bear: half
SELL_ON_ENTRY = {           # one-time, % of current stack
    "DISTRIBUTION": 0.25,
    "EUPHORIA": 0.50,
}


def compute_signal(zone: str,
                   prev_zone: Optional[str],
                   drawdown_call: str = "AMBIGUOUS",
                   days_above_sma200: int = 0,
                   state: Optional[Dict] = None) -> Dict:
    """One-time, percent-of-capital signal on zone transitions.

    `state` carries memory between daily runs:
      bought_zones : zones whose buy tranche is already spent this cycle
      sold_zones   : zones whose fixation already fired this cycle
      re_risk_fired: the SMA200 reserve deployment already happened

    Returns the signal plus `new_state` for the caller to persist.
    """
    zone = (zone or "UNKNOWN").upper()
    prev = (prev_zone or "").upper()
    st = dict(state or {})
    bought = list(st.get("bought_zones", []))
    sold = list(st.get("sold_zones", []))
    re_risk_fired = bool(st.get("re_risk_fired", False))
    spent = float(st.get("spent", sum(BUY_ON_ENTRY.get(z, 0.0) for z in bought)))

    def _out(action, frac_cap=0.0, frac_stack=0.0, trigger="", why=""):
        return {
            "policy_version": SIGNAL_POLICY_VERSION,
            "zone": zone,
            "drawdown_call": drawdown_call,
            "action": action,
            "fraction_of_capital": round(frac_cap, 4),
            "fraction_of_stack": round(frac_stack, 4),
            "trigger": trigger,
            "rationale": why,
            "new_state": {"bought_zones": bought, "sold_zones": sold,
                          "re_risk_fired": re_risk_fired,
                          "spent": round(spent, 4)},
        }

    entered = zone != prev

    # ── SELL on entering overheated zones (one-time per cycle) ──
    if entered and zone in SELL_ON_ENTRY and zone not in sold:
        frac = SELL_ON_ENTRY[zone]
        sold.append(zone)
        if zone == "EUPHORIA":
            # top of the cycle: reset the buy budget for the next descent
            bought = []
            re_risk_fired = False
            spent = 0.0
        return _out("SELL", frac_stack=frac, trigger=f"entered_{zone}",
                    why=f"Вход в зону {zone} — разовая фиксация "
                        f"{frac*100:.0f}% стэка")

    # ── Re-risk: objective SMA200 trigger deploys the REMAINDER, once ──
    if days_above_sma200 >= RE_RISK_DAYS_ABOVE_SMA200 and not re_risk_fired:
        remainder = max(0.0, min(1.0, 1.0 - spent))
        re_risk_fired = True
        if remainder > 0:
            spent += remainder
            return _out("BUY", frac_cap=remainder, trigger="re_risk_sma200",
                        why=f"{days_above_sma200}д выше SMA200 — объективный "
                            f"триггер, разворачиваем остаток резерва "
                            f"({remainder*100:.0f}% капитала)")

    # ── BUY on entering cheap zones (one-time per cycle) ──
    if entered and zone in BUY_ON_ENTRY and zone not in bought:
        frac = BUY_ON_ENTRY[zone]
        why_extra = ""
        if zone == "NEUTRAL" and drawdown_call == "STRUCTURAL_BEAR_RISK":
            frac *= STRUCTURAL_BEAR_HALF_STEP
            why_extra = (" (половинный шаг: просадка началась с перегретой "
                         "вершины — урок 2022; остальное ждёт ACCUMULATION)")
        bought.append(zone)
        spent += frac
        return _out("BUY", frac_cap=frac, trigger=f"entered_{zone}",
                    why=f"Вход в зону {zone} — покупка на "
                        f"{frac*100:.0f}% капитала{why_extra}")

    return _out("HOLD", trigger="no_event",
                why=f"Зона {zone} без события — держим план, не импровизируем")
