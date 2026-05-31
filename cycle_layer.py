"""
cycle_layer.py — integration glue for OracAI's cycle-position answer.

Combines THREE independent readings into one honest card, WITHOUT merging them
into a single score (merging is what destroyed the edge in the audit):

  1. DIRECTION  — from the main engine (regime: BULL/BEAR/...). "Which way now."
  2. VALUATION  — from cycle_context.compute_cycle_context (MVRV/Mayer).
                  "Correction vs structural bear." This is the new answer.
  3. (optional) PHASE — from cycle_position_engine if present.

MVRV comes from mvrv_fetcher (live, CoinMetrics) in production; falls back to
the stale value in data/btc.csv, then to price-only (Mayer) if neither works.
"""
import logging
from typing import Optional, Dict
import numpy as np

from cycle_context import compute_cycle_context

logger = logging.getLogger(__name__)

_ZONE_RU = {
    "EUPHORIA": "Эйфория (исторические вершины)",
    "DISTRIBUTION": "Распределение (поздний цикл, перегрев)",
    "EXPANSION": "Экспансия (здоровая середина цикла)",
    "NEUTRAL": "Нейтрально (около себестоимости рынка)",
    "ACCUMULATION": "Накопление (ниже себестоимости, зона ценности)",
    "UNKNOWN": "Недостаточно данных",
}
_CALL_RU = {
    "STRUCTURAL_BEAR_RISK": "РИСК СТРУКТУРНОГО МЕДВЕДЯ",
    "CAPITULATION_VALUE_ZONE": "ЗОНА КАПИТУЛЯЦИИ / ЦЕННОСТИ",
    "AMBIGUOUS": "НЕОДНОЗНАЧНО — решает 200-дневная",
    "NOT_IN_DRAWDOWN": "Не в просадке",
}


def _get_mvrv(close: np.ndarray) -> Dict:
    """Resolve MVRV: live → stale CSV → price-only. Returns dict with source."""
    # 1. live
    try:
        from mvrv_fetcher import fetch_mvrv_snapshot
        snap = fetch_mvrv_snapshot("btc")
        if snap and snap.get("mvrv"):
            return {"mvrv": snap["mvrv"], "mvrv_peak_90d": snap["mvrv_peak_90d"],
                    "source": f"coinmetrics_live({snap['date']})"}
    except Exception as e:  # noqa: BLE001
        logger.info("Live MVRV unavailable (%s), trying CSV", e)
    # 2. stale CSV
    try:
        import pandas as pd
        from pathlib import Path
        p = Path(__file__).parent / "data" / "btc.csv"
        df = pd.read_csv(p, parse_dates=["time"])
        df = df[df["CapMVRVCur"].notna()]
        if len(df):
            tail = df["CapMVRVCur"].tail(90)
            return {"mvrv": float(tail.iloc[-1]), "mvrv_peak_90d": float(tail.max()),
                    "source": f"btc.csv_stale({df['time'].iloc[-1].date()})"}
    except Exception as e:  # noqa: BLE001
        logger.info("CSV MVRV unavailable (%s), price-only", e)
    # 3. price-only
    return {"mvrv": None, "mvrv_peak_90d": None, "source": "price_only(mayer)"}


def build_cycle_card(close, engine_output: Optional[Dict] = None) -> Dict:
    """close: daily closes oldest→newest. engine_output: main engine's dict
    (for regime/direction). Returns a combined, honest cycle card."""
    close = np.asarray(close, dtype=float)
    mv = _get_mvrv(close)
    ctx = compute_cycle_context(close, mvrv=mv["mvrv"], mvrv_peak_90d=mv["mvrv_peak_90d"])
    ctx["mvrv_source"] = mv["source"]

    direction = None
    if engine_output:
        direction = {
            "regime": engine_output.get("regime"),
            "risk_state": (engine_output.get("risk") or {}).get("risk_state"),
        }

    return {"direction": direction, "valuation": ctx}


def format_telegram(card: Dict) -> str:
    v = card["valuation"]
    m = v["metrics"]
    d = card.get("direction") or {}
    lines = ["📊 *Стадия рынка BTC*", ""]
    if d.get("regime"):
        rs = f" · {d['risk_state']}" if d.get("risk_state") else ""
        lines.append(f"*Направление:* {d['regime']}{rs}")
    lines.append(f"*Зона цикла:* {_ZONE_RU.get(v['zone'], v['zone'])}")
    lines.append(f"*Где мы:* {_CALL_RU.get(v['drawdown_call'], v['drawdown_call'])}")
    if v["confidence"] > 0:
        lines.append(f"   уверенность {int(v['confidence']*100)}%")
    lines.append("")
    mvrv_s = f"{m['mvrv']:.2f}" if m["mvrv"] is not None else "н/д"
    peak_s = f" (пик90д {m['mvrv_peak_90d']:.2f})" if m["mvrv_peak_90d"] is not None else ""
    mayer_s = f"{m['mayer_multiple']:.2f}" if m["mayer_multiple"] is not None else "н/д"
    lines.append(f"MVRV {mvrv_s}{peak_s} · Mayer {mayer_s} · от ATH {m['drawdown_from_ath_pct']:.0f}%")
    lines.append(f"_{v['rationale']}_")
    lines.append("")
    lines.append(f"🎯 Триггер ре-входа: {v['objective_reentry_trigger']}")
    lines.append(f"`источник MVRV: {v['mvrv_source']}`")
    return "\n".join(lines)


if __name__ == "__main__":
    import pandas as pd
    from pathlib import Path
    df = pd.read_csv(Path(__file__).parent / "data" / "btc.csv", parse_dates=["time"])
    df = df[df["PriceUSD"].notna() & (df["PriceUSD"] > 0)]
    close = df["PriceUSD"].values
    fake_engine = {"regime": "BEAR", "risk": {"risk_state": "RISK_OFF"}}
    card = build_cycle_card(close, fake_engine)
    print(format_telegram(card))
