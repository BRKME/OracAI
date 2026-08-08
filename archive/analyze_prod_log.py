"""
analyze_prod_log.py — PRE-REGISTERED validation of the prod-log window.

Written BEFORE the verdict date on purpose: the methodology is fixed now so
that when the data arrives we judge against criteria committed in advance,
not against whatever framing flatters the result. (Same discipline as the
Polymarket calibration journal.)

Four questions, in order of how much each one matters:

  1. EPISODE: during the live -50% leg, did the engine's exposure path lose
     less than HODL — and, critically, less than the ONE-LINE SMA200 baseline?
     The Phase 3-4 audit showed the engine's alpha came from an SMA200 rule;
     the engine must beat that cheap baseline live or it isn't earning its
     complexity.
  2. RE-ENTRY (pre-registered): when recovery_override fires (>=10d above
     SMA200), exposure_cap must reach >=0.95 within 5 logged days, with no
     false reset for 14 days. PASS/FAIL/NOT_YET. This is the engine's known
     weakest spot (-141pp in the 2023-24 walk-forward window) and its fix has
     never run live.
  3. FORWARD RETURNS by regime — on NON-OVERLAPPING windows only (overlapping
     daily rows share the same future and naive stats overstate significance).
  4. POWER CHECK: print sample sizes per regime so nobody reads a verdict into
     n=12.

Run:  python analyze_prod_log.py
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PROD_LOG = Path(__file__).parent / "state" / "prod_log.csv"
BASELINE_DEFENSIVE_EXPOSURE = 0.35   # mirror engine's current bear cap
RE_ENTRY_TARGET = 0.95               # pre-registered (Phase 4 override target)
RE_ENTRY_WITHIN_DAYS = 5             # pre-registered
RE_ENTRY_NO_RESET_DAYS = 14          # pre-registered


# ── pure functions (tested) ─────────────────────────────────────────────────

def forward_return(prices: pd.Series, ts, days: int) -> Optional[float]:
    """Return over `days` calendar days from the price at/just before ts."""
    try:
        start = prices.asof(pd.Timestamp(ts))
        end_ts = pd.Timestamp(ts) + pd.Timedelta(days=days)
        if end_ts > prices.index[-1]:
            return None
        end = prices.asof(end_ts)
        if pd.isna(start) or pd.isna(end) or start <= 0:
            return None
        return float(end / start - 1.0)
    except Exception:
        return None


def non_overlapping(df: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Thin rows so consecutive kept timestamps are >= window_days apart.

    Daily rows with 7d forward windows share most of their future; treating
    them as independent inflates n. Keep one row per window instead.
    """
    df = df.sort_values("timestamp_utc")
    kept = []
    last = None
    for _, r in df.iterrows():
        ts = pd.Timestamp(r["timestamp_utc"])
        if last is None or (ts - last).days >= window_days:
            kept.append(r)
            last = ts
    return pd.DataFrame(kept)


def sma200_baseline_exposure(prices: pd.Series) -> pd.Series:
    """The one-line null model: above SMA200 -> 1.0, below -> defensive cap."""
    sma = prices.rolling(200).mean()
    expo = pd.Series(np.where(prices > sma, 1.0, BASELINE_DEFENSIVE_EXPOSURE),
                     index=prices.index)
    expo[sma.isna()] = 1.0   # warm-up: behave like HODL
    return expo


def equity_from_exposure(prices: pd.Series, exposure: pd.Series) -> pd.Series:
    """Equity curve of holding `exposure` (decided on the prior day's signal)."""
    rets = prices.pct_change().fillna(0.0)
    strat = exposure.shift(1).fillna(exposure.iloc[0]) * rets
    return (1.0 + strat).cumprod()


def re_entry_test(rows: pd.DataFrame,
                  target: float = RE_ENTRY_TARGET,
                  within_days: int = RE_ENTRY_WITHIN_DAYS) -> dict:
    """Pre-registered test of the Phase 4 recovery override, live.

    From the FIRST row where recovery_override_would_fire is true:
      exposure_cap must reach >= target within `within_days` logged days.
    """
    df = rows.sort_values("timestamp_utc")
    fired = df[df["recovery_override_would_fire"].astype(str)
               .str.lower().isin(("true", "1"))]
    if fired.empty:
        return {"status": "NOT_YET",
                "note": "override ещё не срабатывал вживую (мы под SMA200)"}
    t0 = pd.Timestamp(fired.iloc[0]["timestamp_utc"])
    after = df[pd.to_datetime(df["timestamp_utc"]) >= t0]
    hit = after[after["exposure_cap"].astype(float) >= target]
    if hit.empty:
        elapsed = (pd.Timestamp(after.iloc[-1]["timestamp_utc"]) - t0).days
        status = "FAIL" if elapsed > within_days else "PENDING"
        return {"status": status, "fired_at": str(t0.date()),
                "days_elapsed": elapsed,
                "note": f"target {target} не достигнут за {elapsed}д"}
    days = (pd.Timestamp(hit.iloc[0]["timestamp_utc"]) - t0).days
    return {"status": "PASS" if days <= within_days else "FAIL",
            "fired_at": str(t0.date()), "days_to_target": days,
            "note": f"target {target} достигнут за {days}д "
                    f"(критерий: ≤{within_days}д)"}


# ── report ───────────────────────────────────────────────────────────────────

def _load_prices() -> pd.Series:
    ext = Path(__file__).parent / "data" / "external" / "btc_ohlcv.csv"
    if ext.exists():
        df = pd.read_csv(ext, parse_dates=["date"]).sort_values("date")
        return pd.Series(df["close"].values, index=df["date"])
    df = pd.read_csv(Path(__file__).parent / "data" / "btc.csv",
                     parse_dates=["time"])
    df = df[df["PriceUSD"].notna() & (df["PriceUSD"] > 0)].sort_values("time")
    return pd.Series(df["PriceUSD"].values, index=df["time"])


def main() -> None:
    if not PROD_LOG.exists():
        print("Нет state/prod_log.csv — нечего анализировать.")
        return
    df = pd.read_csv(PROD_LOG, parse_dates=["timestamp_utc"])
    prices = _load_prices()
    print(f"prod_log: {len(df)} строк, "
          f"{df['timestamp_utc'].min().date()} → {df['timestamp_utc'].max().date()}")

    # 1. EPISODE — exposure paths over the log window
    print("\n=== 1. ЭПИЗОД: движок vs HODL vs SMA200-правило (окно лога) ===")
    win = prices[prices.index >= df["timestamp_utc"].min().tz_localize(None)]
    full = prices  # need history for SMA200 warm-up
    base_expo_full = sma200_baseline_exposure(full)
    base_expo = base_expo_full[base_expo_full.index >= win.index[0]]
    # engine exposure path: as-of join of logged exposure_cap onto price dates
    eng = df.set_index(df["timestamp_utc"].dt.tz_localize(None))[
        "exposure_cap"].astype(float).sort_index()
    eng_expo = eng.reindex(win.index, method="ffill").fillna(1.0)
    hodl_eq = (win / win.iloc[0])
    eng_eq = equity_from_exposure(win, eng_expo)
    base_eq = equity_from_exposure(win, base_expo)
    print(f"  HODL      : {(hodl_eq.iloc[-1]-1)*100:+.1f}%")
    print(f"  SMA200    : {(base_eq.iloc[-1]-1)*100:+.1f}%  (бейзлайн в 1 строку)")
    print(f"  Движок    : {(eng_eq.iloc[-1]-1)*100:+.1f}%")
    edge_vs_base = (eng_eq.iloc[-1] - base_eq.iloc[-1]) * 100
    print(f"  Движок − SMA200: {edge_vs_base:+.1f}пп — "
          f"{'зарабатывает сложность' if edge_vs_base > 0 else 'НЕ бьёт однострочное правило'}")

    # 2. RE-ENTRY (pre-registered)
    print("\n=== 2. RE-ENTRY ТЕСТ (пре-регистрирован) ===")
    print(f"  Критерий: override сработал → exposure_cap ≥{RE_ENTRY_TARGET} "
          f"за ≤{RE_ENTRY_WITHIN_DAYS} логовых дней, без сброса {RE_ENTRY_NO_RESET_DAYS}д")
    res = re_entry_test(df)
    print(f"  Статус: {res['status']} — {res.get('note','')}")

    # 3. FORWARD RETURNS by regime — non-overlapping
    print("\n=== 3. ФОРВАРД-ДОХОДНОСТИ ПО РЕЖИМАМ (неперекрывающиеся 7д) ===")
    thin = non_overlapping(df, window_days=7)
    for regime, grp in thin.groupby("regime"):
        rets = [forward_return(prices, ts.tz_localize(None), 7)
                for ts in grp["timestamp_utc"]]
        rets = [r for r in rets if r is not None]
        if rets:
            mean = np.mean(rets) * 100
            hit = np.mean([r > 0 for r in rets]) * 100
            print(f"  {regime:11s}: n={len(rets):2d} · mean fwd7d {mean:+.1f}% "
                  f"· hit {hit:.0f}%")

    # 4. POWER CHECK
    print("\n=== 4. МОЩНОСТЬ ===")
    n7 = len(non_overlapping(df, 7))
    print(f"  Всего строк {len(df)}, независимых 7д-окон ~{n7}.")
    if n7 < 25:
        print("  ⚠️ Этого мало для вердикта по режимам. Вердикт-чекпойнт — это")
        print("     проверка процесса; статистический вывод — 6–12 месяцев лога.")


if __name__ == "__main__":
    main()
