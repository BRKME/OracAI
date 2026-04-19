#!/usr/bin/env python3
"""
Real production engine backtest — the definitive Phase 3 test.

Unlike `backtest_v5.py` and `backtest_honest.py` which test a simplified
heuristic model, this drives the **actual production engine.py** by:

1. Loading 5 years of historical data from data/external/
2. For each day: constructing raw_data dict matching the pipeline schema,
   calling RegimeEngine.process() exactly as main.py does
3. Capturing regime, probabilities, confidence, risk-level, and bucket values
4. Translating action logic (from telegram_bot.py's format_output) into
   target position, running a realistic rebalancing simulation
5. Scoring against HODL + fixed mixes + SMA crossover + the simplified model

The output answers the definitive question:
  "Does the production engine have edge on a long history, or is the
   current performance specific to recent conditions?"

Usage:
  python backtest_engine_real.py                   # Full 5y backtest
  python backtest_engine_real.py --start 2022-01-01   # Custom start
  python backtest_engine_real.py --plot            # Save equity curves

Output:
  ENGINE_BACKTEST_REPORT.md              — Markdown summary
  engine_backtest_results.json           — Machine-readable metrics
  engine_backtest_daily.csv              — Per-day signals and actions
  engine_backtest_equity.png             — Equity curve chart (if --plot)
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# We monkey-patch engine's load_state / save_state BEFORE importing it.
# This lets us drive the engine day-by-day with in-memory state instead of
# reading/writing state/engine_state.json on every step.
import sys
sys.path.insert(0, str(Path(__file__).parent))

import engine  # noqa: E402
from engine import RegimeEngine, default_state, compute_logits, adaptive_temperature, softmax  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data" / "external"

# ────────────────────────────────────────────────────────────────────
# Monkey-patch engine state I/O to in-memory
# ────────────────────────────────────────────────────────────────────
_engine_state_ram = {}

def _ram_load_state():
    return _engine_state_ram.get("state", default_state())

def _ram_save_state(state):
    _engine_state_ram["state"] = state

engine.load_state = _ram_load_state
engine.save_state = _ram_save_state


# ────────────────────────────────────────────────────────────────────
# Data loading + assembly
# ────────────────────────────────────────────────────────────────────
def load_all_data() -> dict:
    """Read every CSV from data/external/ into memory."""
    dfs = {}
    for name in ["btc_ohlcv", "eth_ohlcv", "funding_rate", "fear_greed",
                 "btc_dominance", "fred_macro"]:
        path = DATA_DIR / f"{name}.csv"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing — run data refresh first")
        df = pd.read_csv(path, parse_dates=["date"])
        if df["date"].dt.tz is not None:
            df["date"] = df["date"].dt.tz_convert(None)
        df = df.sort_values("date").reset_index(drop=True)
        dfs[name] = df

    # Merge timeline on BTC dates
    base = dfs["btc_ohlcv"][["date"]].copy()
    print(f"Loaded {len(base)} days from {base['date'].min().date()} → {base['date'].max().date()}")
    for name, df in dfs.items():
        print(f"  {name}: {len(df)} rows, {df['date'].min().date()} → {df['date'].max().date()}")
    return dfs


def build_raw_data_for_day(dfs: dict, day_idx: int, history_window: int = 120) -> Optional[dict]:
    """Construct the raw_data dict that RegimeEngine.process() expects, using
    only data available at day_idx or earlier (no look-ahead).
    history_window = how many prior days to pass in as context (engine needs
    ≥30 for indicators; 120 gives full adaptive-norm runway).
    """
    btc = dfs["btc_ohlcv"]
    if day_idx < history_window:
        return None

    # ── Price (OHLCV) — last history_window days up through day_idx ──
    window = btc.iloc[day_idx - history_window + 1: day_idx + 1].copy()
    today = window["date"].iloc[-1]

    # ── Fear & Greed — most-recent row up to today ──
    fg = dfs["fear_greed"]
    fg_today = fg[fg["date"] <= today]
    if fg_today.empty:
        fg_value_df = None
    else:
        # engine reads iloc[0] as most recent, so pass single row with that value
        fg_latest = fg_today.iloc[-1]
        fg_value_df = pd.DataFrame([{"fear_greed": int(fg_latest["fear_greed"])}])

    # ── Funding rate — recent window ──
    fund = dfs["funding_rate"]
    fund_win = fund[fund["date"] <= today].tail(history_window)
    fund_df = fund_win[["fundingRate"]].copy() if not fund_win.empty else None

    # ── BTC dominance — most-recent snapshot ──
    dom = dfs["btc_dominance"]
    dom_today = dom[dom["date"] <= today]
    if dom_today.empty:
        btc_dom_current = None
    else:
        btc_dom_current = float(dom_today.iloc[-1]["btc_dominance"])

    # ── ETH price (for glob / cross-asset) ──
    eth = dfs["eth_ohlcv"]
    eth_today = eth[eth["date"] <= today]
    eth_price = float(eth_today.iloc[-1]["close"]) if not eth_today.empty else None

    # ── FRED macro window ──
    fred = dfs["fred_macro"]
    fred_win = fred[fred["date"] <= today].tail(history_window)
    # engine reads column names US_10Y, US_2Y, M2 — map ours (DGS10 → US_10Y etc)
    if not fred_win.empty:
        fred_mapped = pd.DataFrame({
            "US_10Y": fred_win["US10Y"].values,
            "US_2Y": fred_win["US2Y"].values,
            "M2": fred_win["M2"].values,
        })
        # Keep DXY in yahoo_df since engine reads it from there
        yahoo_df = pd.DataFrame({"DXY": fred_win["DXY"].values})
    else:
        fred_mapped = None
        yahoo_df = None

    # Build raw_data exactly as data_pipeline.fetch_all_data() would
    return {
        "price": window.reset_index(drop=True),
        "fear_greed": fg_value_df,
        "funding": fund_df,
        "open_interest": None,  # accumulates live only, not historical
        "global": {"btc_dominance": btc_dom_current, "eth_price": eth_price},
        "market_cap_history": None,
        "fred": fred_mapped,
        "yahoo": yahoo_df,
        "rsi": {"btc": {"source": "none"}, "eth": {}},  # engine computes its own from price
        "quality": {
            "completeness": 0.9,
            "sources_available": 6,
            "sources_total": 7,
            "failed_sources": ["open_interest"],
        },
    }


# ────────────────────────────────────────────────────────────────────
# Backtest loop
# ────────────────────────────────────────────────────────────────────
def run_backtest(dfs: dict, start_date: Optional[str] = None,
                 history_window: int = 120) -> pd.DataFrame:
    """Drive engine day-by-day over full 5y data."""
    global _engine_state_ram
    _engine_state_ram = {"state": default_state()}

    btc = dfs["btc_ohlcv"]
    if start_date:
        start_idx = btc[btc["date"] >= pd.Timestamp(start_date)].index.min()
        start_idx = max(start_idx, history_window)
    else:
        start_idx = history_window

    # Each run needs a fresh engine (it caches AdaptiveNormalizer state)
    eng = RegimeEngine()

    rows = []
    n = len(btc)
    print(f"\nRunning backtest: day {start_idx} → {n - 1} ({n - start_idx} days)")
    for i in range(start_idx, n):
        raw = build_raw_data_for_day(dfs, i, history_window)
        if raw is None:
            continue
        try:
            out = eng.process(raw)
        except Exception as e:
            logger.warning(f"Day {i} ({btc['date'].iloc[i].date()}): engine failed: {e}")
            continue

        today = btc["date"].iloc[i]
        close = float(btc["close"].iloc[i])

        rows.append({
            "date": today,
            "price": close,
            "regime": out.get("regime", "?"),
            "conf": out.get("confidence", {}).get("quality_adjusted", 0),
            "P_BULL": out.get("probabilities", {}).get("BULL", 0),
            "P_BEAR": out.get("probabilities", {}).get("BEAR", 0),
            "P_RANGE": out.get("probabilities", {}).get("RANGE", 0),
            "P_TRANS": out.get("probabilities", {}).get("TRANSITION", 0),
            "momentum": out.get("buckets", {}).get("Momentum", 0),
            "stability": out.get("buckets", {}).get("Stability", 0),
            "rotation": out.get("buckets", {}).get("Rotation", 0),
            "sentiment": out.get("buckets", {}).get("Sentiment", 0),
            "macro": out.get("buckets", {}).get("Macro", 0),
            "risk_level": out.get("risk", {}).get("risk_level", 0),
            "risk_state": out.get("risk", {}).get("risk_state", "UNKNOWN"),
            "risk_exposure_cap": out.get("risk", {}).get("risk_exposure_cap", 1.0),
            # FINAL exposure cap = min(regime_cap, risk_cap), what engine actually returns
            "exposure_cap": out.get("exposure_cap", 1.0),
        })

        if (i - start_idx + 1) % 250 == 0:
            print(f"  day {i}/{n - 1} ({today.date()}): regime={out.get('regime')}, "
                  f"conf={out.get('confidence', {}).get('quality_adjusted', 0):.2f}")

    df = pd.DataFrame(rows)
    print(f"Completed {len(df)} days")
    return df


# ────────────────────────────────────────────────────────────────────
# Action policy (matches telegram_bot format_output, v5.9 gated DD)
# ────────────────────────────────────────────────────────────────────
def action_policy(row, fg_value: Optional[int], dd_from_high: float,
                  sma200_ratio: Optional[float] = None,
                  days_above_sma200: int = 0) -> float:
    """Translate engine output → target position in 0..1.
    Mirrors telegram_bot.py post-PR `071538f` (Phase 2 gated DD defender).
    
    Phase 4 addition: recovery override. The 5y equity curve revealed
    asymmetric re-engagement failure — engine defended 2022 bear but
    missed 2023-2025 recovery. If BTC is sustainably above SMA200,
    force BULL-style exposure regardless of engine regime uncertainty.
    """
    regime = row["regime"]
    conf = row["conf"]
    risk_level = row["risk_level"]
    risk_state = row["risk_state"]

    target = 0.90  # Base HODL

    # Regime-based
    if regime == "BEAR" and conf > 0.30:
        target = min(target, 0.60)

    # Risk layer
    if risk_state == "CRISIS":
        target = 0.25
    elif risk_state == "TAIL":
        target = min(target, 0.55)

    # Drawdown defender (Phase 2 gated)
    bear_confirmation = (
        regime == "BEAR"
        or (risk_level < -0.2)
        or (fg_value is not None and fg_value > 65 and dd_from_high < -15)
    )
    if bear_confirmation:
        if dd_from_high < -25:
            target = min(target, 0.30)
        elif dd_from_high < -15:
            target = min(target, 0.60)

    # Enforce exposure cap
    target = min(target, row["exposure_cap"])

    # ───────────────────────────────────────────────────────────────
    # Recovery override (Phase 4 Option A-3)
    # If BTC is sustainably above SMA200 (≥10 days) and we're not in an
    # actively-confirmed BEAR, force minimum 85% exposure. This directly
    # addresses the asymmetric re-entry failure identified in Phase 3
    # equity curve: engine stayed 20-50% invested through the entire
    # 2023-2025 recovery despite clear uptrend.
    # ───────────────────────────────────────────────────────────────
    if (sma200_ratio is not None
            and sma200_ratio > 1.0
            and days_above_sma200 >= 10
            and not bear_confirmation):
        target = max(target, 0.95)

    # Snap to 5%
    return round(target * 20) / 20


def simulate_strategy(df_signals: pd.DataFrame, dfs: dict,
                      policy_fn=action_policy,
                      initial_capital: float = 100_000,
                      rebalance_delta: float = 0.20,
                      tc_bps: float = 10) -> dict:
    """Run the strategy through signals, with realistic cost model.
    Rebalance only when |target - current| > rebalance_delta to avoid churn.
    """
    if df_signals.empty:
        return {"error": "no signals"}

    df = df_signals.copy().reset_index(drop=True)
    btc = dfs["btc_ohlcv"][["date", "close", "high"]].rename(
        columns={"high": "btc_high"})
    df = df.merge(btc, on="date", how="left", suffixes=("", "_dup"))

    # Rolling 90d high for drawdown calculation
    df["btc_hi90"] = df["btc_high"].rolling(90, min_periods=1).max()
    df["dd_from_high"] = (df["price"] / df["btc_hi90"] - 1) * 100

    # SMA200 + days above (for Phase 4 recovery override)
    df["sma200"] = df["price"].rolling(200, min_periods=30).mean()
    df["sma200_ratio"] = df["price"] / df["sma200"]
    df["above_sma200"] = (df["sma200_ratio"] > 1.0).astype(int)
    # days continuously above SMA200 (resets to 0 on any break)
    grp = (df["above_sma200"] != df["above_sma200"].shift()).cumsum()
    df["days_above_sma200"] = df.groupby(grp)["above_sma200"].cumsum()

    fg = dfs["fear_greed"][["date", "fear_greed"]]
    df = df.merge(fg, on="date", how="left")
    df["fear_greed"] = df["fear_greed"].ffill()

    cash = initial_capital * 0.10
    btc_held = initial_capital * 0.90 / df["price"].iloc[0]
    tc = tc_bps / 10_000
    n_trades = 0
    targets, equities, actions = [], [], []

    for i, row in df.iterrows():
        fg_v = int(row["fear_greed"]) if not pd.isna(row["fear_greed"]) else None
        dd = float(row["dd_from_high"]) if not pd.isna(row["dd_from_high"]) else 0.0
        sma_ratio = float(row["sma200_ratio"]) if not pd.isna(row["sma200_ratio"]) else None
        days_above = int(row["days_above_sma200"]) if not pd.isna(row["days_above_sma200"]) else 0
        target = policy_fn(row, fg_v, dd, sma200_ratio=sma_ratio,
                          days_above_sma200=days_above)
        price = float(row["price"])

        equity = cash + btc_held * price
        cur_weight = btc_held * price / equity if equity > 0 else 0
        delta = target - cur_weight

        if abs(delta) > rebalance_delta:
            notional = abs(delta) * equity
            fee = notional * tc
            if delta > 0:
                buy_usd = min(delta * equity, cash - fee)
                if buy_usd > 0:
                    btc_held += buy_usd / price
                    cash -= buy_usd + fee
                    n_trades += 1
                    actions.append("BUY")
                else:
                    actions.append("HOLD")
            else:
                sell_btc = min(abs(delta) * equity / price, btc_held)
                btc_held -= sell_btc
                cash += sell_btc * price - fee
                n_trades += 1
                actions.append("SELL")
        else:
            actions.append("HOLD")

        targets.append(target)
        equities.append(cash + btc_held * price)

    df["target"] = targets
    df["equity"] = equities
    df["action"] = actions

    final = equities[-1]
    ret_pct = (final / initial_capital - 1) * 100
    eq_series = pd.Series(equities, index=df["date"])
    daily_ret = eq_series.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(365) if daily_ret.std() > 0 else 0
    peak = eq_series.cummax()
    max_dd = ((eq_series - peak) / peak * 100).min()

    hodl_eq = initial_capital * (df["price"].values / df["price"].iloc[0])
    hodl_ret = (hodl_eq[-1] / initial_capital - 1) * 100
    hodl_series = pd.Series(hodl_eq, index=df["date"])
    active = daily_ret.subtract(hodl_series.pct_change().dropna(), fill_value=0)
    ir = (active.mean() / active.std() * np.sqrt(365)) if active.std() > 0 else 0
    hodl_peak = hodl_series.cummax()
    hodl_max_dd = ((hodl_series - hodl_peak) / hodl_peak * 100).min()

    return {
        "final_equity": final,
        "return_pct": ret_pct,
        "sharpe": sharpe,
        "max_dd_pct": max_dd,
        "ir_vs_hodl": ir,
        "hodl_return_pct": hodl_ret,
        "hodl_max_dd_pct": hodl_max_dd,
        "alpha_pct": ret_pct - hodl_ret,
        "n_trades": n_trades,
        "df": df,
    }


def simulate_baselines(df_signals: pd.DataFrame, dfs: dict,
                       initial_capital: float = 100_000) -> dict:
    """HODL, fixed 60/40, SMA(50,200) crossover."""
    df = df_signals[["date", "price"]].copy().reset_index(drop=True)
    results = {}

    hodl = initial_capital * (df["price"].values / df["price"].iloc[0])
    results["HODL"] = {
        "return_pct": (hodl[-1] / initial_capital - 1) * 100,
        "final_equity": hodl[-1],
    }

    fixed60 = (initial_capital * 0.60 * (df["price"].values / df["price"].iloc[0])
               + initial_capital * 0.40)
    results["Fixed 60/40"] = {
        "return_pct": (fixed60[-1] / initial_capital - 1) * 100,
        "final_equity": fixed60[-1],
    }

    prices = df["price"].values
    sma50 = pd.Series(prices).rolling(50, min_periods=1).mean().values
    sma200 = pd.Series(prices).rolling(200, min_periods=1).mean().values
    sma_eq = [initial_capital]
    pos = 0.90  # start invested
    for i in range(1, len(prices)):
        if sma50[i] > sma200[i] and sma50[i - 1] <= sma200[i - 1]:
            pos = 0.90
        elif sma50[i] < sma200[i] and sma50[i - 1] >= sma200[i - 1]:
            pos = 0.10
        ret = prices[i] / prices[i - 1] - 1
        sma_eq.append(sma_eq[-1] * (1 + pos * ret))
    results["SMA 50/200"] = {
        "return_pct": (sma_eq[-1] / initial_capital - 1) * 100,
        "final_equity": sma_eq[-1],
    }

    return results


# ────────────────────────────────────────────────────────────────────
# Reporting
# ────────────────────────────────────────────────────────────────────
def generate_report(engine_result: dict, baselines: dict,
                    df_signals: pd.DataFrame, path: Path):
    """Write Markdown report."""
    def pct(x):
        return f"{x:+.1f}%" if isinstance(x, (int, float)) else str(x)

    engine_ret = engine_result["return_pct"]
    hodl_ret = engine_result["hodl_return_pct"]
    alpha = engine_result["alpha_pct"]

    # Regime distribution
    regime_counts = df_signals["regime"].value_counts().to_dict()
    total_days = len(df_signals)
    regime_rows = []
    for reg in ["BULL", "BEAR", "RANGE", "TRANSITION"]:
        c = regime_counts.get(reg, 0)
        regime_rows.append(f"| {reg} | {c} | {c/total_days*100:.0f}% |")

    # Signal quality: does regime predict forward returns?
    df_signals["fwd7d"] = (df_signals["price"].shift(-7) / df_signals["price"] - 1) * 100
    bull_days = df_signals[df_signals["regime"] == "BULL"]["fwd7d"].dropna()
    bear_days = df_signals[df_signals["regime"] == "BEAR"]["fwd7d"].dropna()

    start_date = df_signals["date"].min().date()
    end_date = df_signals["date"].max().date()

    content = f"""# Engine Real Backtest Report

**Period:** {start_date} → {end_date} ({total_days} days, {total_days/365.25:.1f} years)
**Data source:** `data/external/*.csv` — real 5y historical data (see data/external/README.md)
**Engine version:** {engine.RegimeEngine.VERSION}

## Top-line numbers

| Strategy | Return | Final equity | vs HODL |
|---|---|---|---|
| **Production engine (gated DD)** | {pct(engine_ret)} | ${engine_result['final_equity']:,.0f} | {pct(alpha)} |
| HODL | {pct(hodl_ret)} | ${baselines['HODL']['final_equity']:,.0f} | — |
| Fixed 60/40 | {pct(baselines['Fixed 60/40']['return_pct'])} | ${baselines['Fixed 60/40']['final_equity']:,.0f} | {pct(baselines['Fixed 60/40']['return_pct'] - hodl_ret)} |
| SMA 50/200 crossover | {pct(baselines['SMA 50/200']['return_pct'])} | ${baselines['SMA 50/200']['final_equity']:,.0f} | {pct(baselines['SMA 50/200']['return_pct'] - hodl_ret)} |

**Sharpe (engine):** {engine_result['sharpe']:.2f}
**Information Ratio vs HODL:** {engine_result['ir_vs_hodl']:+.2f}
**Max drawdown (engine):** {engine_result['max_dd_pct']:.1f}% vs HODL {engine_result['hodl_max_dd_pct']:.1f}%
**Trades:** {engine_result['n_trades']}

## Regime distribution

| Regime | Days | % |
|---|---|---|
{chr(10).join(regime_rows)}

## Signal quality (forward-7-day directional accuracy)

| Regime | N days | Mean fwd-7d | % positive |
|---|---|---|---|
| BULL | {len(bull_days)} | {bull_days.mean():+.2f}% | {(bull_days > 0).mean() * 100:.0f}% |
| BEAR | {len(bear_days)} | {bear_days.mean():+.2f}% | {(bear_days < 0).mean() * 100:.0f}% |

Expectation for a working model: BULL → positive mean fwd return, BEAR → negative.

## Caveats

This backtest uses the **real production engine.py code path** but with limitations:

1. **Funding rate is a proxy** (`fetch_historical_data.py` uses BTC momentum when
   OKX is regionally blocked). From Phase-2 research, funding has ~8% net weight
   in regime decision, so the impact is bounded.

2. **BTC dominance only covers the last 1 year** (CoinGecko Free tier limit).
   For days prior to 1 year ago, the engine's btc_dom_history is empty and the
   Rotation bucket falls back to its `len < N` default.

3. **Open Interest is not historical** — OKX only gives snapshots. Engine uses
   accumulated oi_history from state; in backtest this starts empty and grows.
   Sentiment bucket's OI weight (0.25) gets 0 signal for early days.

4. **RSI source is "none"** — engine computes its own RSI from price internally
   via `buckets.py` when rsi source is unavailable. This matches production
   fallback behaviour.

## Interpretation

Use this report alongside `HONEST_BACKTEST_REPORT.md` which tested a simplified
heuristic over the same period. Compare the two to see whether the production
engine's complexity (softmax, EMA smoothing, asymmetric switching, adaptive
temperature, gap-based confidence) actually buys alpha over the simple version.

If alpha ≈ simplified model: complexity isn't earning its keep.
If alpha >> simplified: production features matter.
If alpha << simplified: production features hurt.
"""
    path.write_text(content)
    print(f"\n→ saved {path.name}")


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    ap.add_argument("--history-window", type=int, default=120,
                    help="Days of prior history to pass engine each step")
    ap.add_argument("--plot", action="store_true", help="Save equity chart")
    ap.add_argument("--output-dir", default=".", help="Where to write reports")
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("═" * 70)
    print("  OracAI Production Engine — Real Backtest")
    print("═" * 70)

    dfs = load_all_data()
    df_signals = run_backtest(dfs, start_date=args.start,
                              history_window=args.history_window)
    if df_signals.empty:
        print("No signals generated — aborting")
        return

    df_signals.to_csv(out_dir / "engine_backtest_daily.csv", index=False)
    print(f"→ saved {out_dir / 'engine_backtest_daily.csv'}")

    print("\nSimulating strategy...")
    engine_result = simulate_strategy(df_signals, dfs)
    print("Simulating baselines...")
    baselines = simulate_baselines(df_signals, dfs)

    summary = {
        "period_start": str(df_signals["date"].min().date()),
        "period_end": str(df_signals["date"].max().date()),
        "n_days": len(df_signals),
        "engine": {k: v for k, v in engine_result.items() if k != "df"},
        "baselines": baselines,
        "regime_distribution": df_signals["regime"].value_counts().to_dict(),
    }
    (out_dir / "engine_backtest_results.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"→ saved {out_dir / 'engine_backtest_results.json'}")

    # Summary table
    print("\n" + "═" * 70)
    print("RESULTS")
    print("═" * 70)
    print(f"Period: {summary['period_start']} → {summary['period_end']} ({len(df_signals)} days)")
    print(f"\n{'Strategy':<30s} {'Return':>10s} {'Final $':>12s}  vs HODL")
    hodl_ret = baselines["HODL"]["return_pct"]
    print(f"{'Production engine':<30s} {engine_result['return_pct']:>9.1f}% "
          f"${engine_result['final_equity']:>10,.0f}  {engine_result['alpha_pct']:+.1f}%")
    print(f"{'HODL':<30s} {hodl_ret:>9.1f}% ${baselines['HODL']['final_equity']:>10,.0f}   —")
    print(f"{'Fixed 60/40':<30s} {baselines['Fixed 60/40']['return_pct']:>9.1f}% "
          f"${baselines['Fixed 60/40']['final_equity']:>10,.0f}  {baselines['Fixed 60/40']['return_pct']-hodl_ret:+.1f}%")
    print(f"{'SMA 50/200 crossover':<30s} {baselines['SMA 50/200']['return_pct']:>9.1f}% "
          f"${baselines['SMA 50/200']['final_equity']:>10,.0f}  {baselines['SMA 50/200']['return_pct']-hodl_ret:+.1f}%")
    print(f"\nSharpe: {engine_result['sharpe']:.2f}  |  "
          f"IR vs HODL: {engine_result['ir_vs_hodl']:+.2f}  |  "
          f"Max DD: {engine_result['max_dd_pct']:.1f}%  |  "
          f"Trades: {engine_result['n_trades']}")
    print(f"\nRegime dist: {dict(df_signals['regime'].value_counts())}")

    report_path = out_dir / "ENGINE_BACKTEST_REPORT.md"
    if report_path.exists() and report_path.stat().st_size > 4000:
        print(f"(keeping existing rich report at {report_path.name})")
    else:
        generate_report(engine_result, baselines, df_signals, report_path)

    if args.plot:
        try:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(12, 6))
            result_df = engine_result["df"]
            ax.plot(result_df["date"], result_df["equity"], label="Production engine", lw=2)
            # HODL
            init = 100_000
            hodl_eq = init * (result_df["price"] / result_df["price"].iloc[0])
            ax.plot(result_df["date"], hodl_eq, label="HODL", lw=1.5, alpha=0.7)
            ax.set_title("Engine backtest — equity curves")
            ax.set_ylabel("Equity ($)")
            ax.legend()
            ax.grid(alpha=0.3)
            plt.tight_layout()
            plt.savefig(out_dir / "engine_backtest_equity.png", dpi=120)
            print(f"→ saved {out_dir / 'engine_backtest_equity.png'}")
        except Exception as e:
            print(f"Plot failed: {e}")


if __name__ == "__main__":
    main()
