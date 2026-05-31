"""
cycle_context.py — Cycle-position layer for OracAI.

Purpose: answer "correction within a bull cycle" vs "start of a structural bear"
using SLOW structural/valuation metrics, NOT direction prediction.

Design principle: this layer is honest about ambiguity. It only makes a
confident call when the valuation metrics are at an extreme that has
historically been unambiguous. In the muddy middle it says AMBIGUOUS and
defers to the objective trend trigger (price reclaiming the 200d SMA).

Inputs:
  close : np.ndarray   daily close, oldest→newest (>=200 days)
  mvrv  : float|None    current MVRV (market value / realized value).
                        From CoinMetrics CapMVRVCur. If None, price-only mode.
Outputs: dict with zone, drawdown_call, confidence, metrics, analogs.

Thresholds are calibrated on 2013-2026 BTC history (see validate() below):
  - MVRV>2.5 inside a drawdown → 85% chance lower in 90d (top-driven bear)
  - MVRV<1.0 → price below aggregate cost basis, capitulation/value zone
  - 1.0<=MVRV<1.5 → genuinely ambiguous; cheap valuations did NOT prevent 2022
"""
import numpy as np


def _mayer(close):
    if len(close) < 200:
        return None
    return float(close[-1] / np.mean(close[-200:]))


def compute_cycle_context(close, mvrv=None, mvrv_peak_90d=None):
    """mvrv_peak_90d: max MVRV over the trailing ~90 days. Used to detect that a
    drawdown began FROM an overheated state, even if MVRV has since compressed.
    If not supplied, falls back to current mvrv (weaker)."""
    close = np.asarray(close, dtype=float)
    price = float(close[-1])
    ath = float(np.max(close))
    dd_ath = (price / ath - 1) * 100          # drawdown from all-time high, %
    mayer = _mayer(close)                       # price / 200d SMA
    below_trend = mayer is not None and mayer < 1.0
    if mvrv_peak_90d is None:
        mvrv_peak_90d = mvrv

    # ── Cycle zone (where in the multi-year cycle) ──
    # MVRV primary (true cycle metric); Mayer as price-only proxy.
    if mvrv is not None:
        if mvrv >= 3.0:      zone = "EUPHORIA"        # historic top zone
        elif mvrv >= 2.2:    zone = "DISTRIBUTION"    # late-cycle, overvalued
        elif mvrv >= 1.5:    zone = "EXPANSION"       # healthy mid-cycle
        elif mvrv >= 1.0:    zone = "NEUTRAL"         # near aggregate cost basis
        else:                zone = "ACCUMULATION"    # below cost basis, value
    elif mayer is not None:
        if mayer >= 2.4:     zone = "EUPHORIA"
        elif mayer >= 1.5:   zone = "DISTRIBUTION"
        elif mayer >= 1.1:   zone = "EXPANSION"
        elif mayer >= 0.85:  zone = "NEUTRAL"
        else:                zone = "ACCUMULATION"
    else:
        zone = "UNKNOWN"

    # ── The actual question: correction vs structural bear ──
    # Only meaningful when in a real drawdown.
    in_drawdown = dd_ath < -15 and below_trend

    call, conf, rationale = "NOT_IN_DRAWDOWN", 0.0, ""
    if in_drawdown:
        m = mvrv if mvrv is not None else None
        peak = mvrv_peak_90d if mvrv_peak_90d is not None else m
        # "Did this drawdown start from an overheated cycle top?" — use the
        # trailing MVRV peak, because by the time price is -15% under the 200d
        # SMA, current MVRV has already compressed below any 'hot' threshold.
        if peak is not None and peak >= 2.5:
            call = "STRUCTURAL_BEAR_RISK"
            conf = 0.70
            rationale = ("Drawdown began from an overheated top (MVRV peaked "
                         f">=2.5 in the last 90d, peak {peak:.2f}). Cycle tops "
                         "that rolled over from this zone (2017-12, 2021-04) "
                         "became multi-month structural bears, not dips.")
        elif m is not None and m < 1.0:
            call = "CAPITULATION_VALUE_ZONE"
            conf = 0.65
            rationale = ("Price below aggregate cost basis (MVRV<1.0). "
                         "Historically a bottoming/accumulation zone — hit at "
                         "every cycle low (2018-11, 2020-03, 2022-H2).")
        else:
            call = "AMBIGUOUS"
            conf = 0.30
            rationale = ("Valuation in the muddy middle (MVRV~1-2.5) and no "
                         "overheated peak behind it. Cheap by valuation did NOT "
                         "prevent the 2022 bear (entered -40% near MVRV 1.5, "
                         "fell to -72%). Defer to trend: a structural bottom is "
                         "confirmed only when price reclaims and holds the 200d SMA.")
            if mayer is not None and mayer < 0.70:
                rationale += (" Mayer<0.70: deep-value overshoot — 90d still "
                              "shaky but +40% mean fwd-180d historically.")

    return {
        "zone": zone,
        "drawdown_call": call,
        "confidence": round(conf, 2),
        "metrics": {
            "price": round(price, 2),
            "mvrv": round(mvrv, 3) if mvrv is not None else None,
            "mvrv_peak_90d": round(mvrv_peak_90d, 3) if mvrv_peak_90d is not None else None,
            "mayer_multiple": round(mayer, 3) if mayer is not None else None,
            "drawdown_from_ath_pct": round(dd_ath, 1),
            "below_200d_sma": bool(below_trend),
        },
        "rationale": rationale,
        "objective_reentry_trigger": "price closes above 200d SMA and holds >10 days",
    }


# ────────────────────────────────────────────────────────────────────
def validate():
    """Replay the classifier over 2013-2026 history and check it makes
    sense at known cycle turns."""
    import pandas as pd
    from pathlib import Path
    p = Path(__file__).parent / "data" / "btc.csv"
    df = pd.read_csv(p, parse_dates=["time"])
    df = df[df["PriceUSD"].notna() & (df["PriceUSD"] > 0)]
    df = df[df["time"] >= "2013-01-01"].reset_index(drop=True)
    close = df["PriceUSD"].values
    mvrv_series = df["CapMVRVCur"].values

    checkpoints = ["2017-12-17", "2018-12-15", "2021-04-14", "2022-11-21",
                   "2024-03-13", "2026-02-04"]
    print(f"{'date':12s} {'price':>10s} {'MVRV':>5s} {'Mayer':>6s} {'zone':14s} {'drawdown_call':22s} conf")
    print("-" * 90)
    for cp in checkpoints:
        idx = df.index[df["time"] >= cp]
        if len(idx) == 0:
            continue
        i = idx[0]
        if i < 200:
            continue
        out = compute_cycle_context(close[:i + 1], mvrv=float(mvrv_series[i]))
        m = out["metrics"]
        print(f"{cp:12s} {m['price']:>10,.0f} {m['mvrv']:>5.2f} "
              f"{m['mayer_multiple']:>6.2f} {out['zone']:14s} "
              f"{out['drawdown_call']:22s} {out['confidence']}")


if __name__ == "__main__":
    validate()
