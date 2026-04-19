#!/usr/bin/env python3
"""
Walk-forward validation of Phase 4 engine fixes.

Runs the backtest once over the full 5y with Phase 4 settings, then
computes metrics for sub-windows from the resulting daily CSV. This is
the correct way to do walk-forward with a stateful engine — the engine
accumulates state continuously, we just measure returns on different slices.
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd


SPLITS = [
    ("A_full_5y",      "2021-07-19", "2026-04-19"),
    ("B_bear_2022",    "2021-07-19", "2022-12-31"),
    ("C_recovery",     "2023-01-01", "2024-12-31"),
    ("D_bull_peak",    "2024-01-01", "2025-12-31"),
    ("E_correction",   "2025-10-01", "2026-04-19"),
    ("F_recent_15m",   "2025-01-01", "2026-04-19"),
]


def metrics_for_window(df: pd.DataFrame, label: str, start: str, end: str) -> dict:
    """Compute engine-strategy and HODL returns for a date window."""
    window = df[(df["date"] >= start) & (df["date"] <= end)].copy()
    if len(window) < 30:
        return None
    
    # Normalize both equity and HODL to start=100,000 at window start
    init = 100_000
    eng_equity = window["equity"].values
    eng_equity = init * eng_equity / eng_equity[0]
    
    price = window["price"].values
    hodl_equity = init * price / price[0]
    
    eng_ret = (eng_equity[-1] / init - 1) * 100
    hodl_ret = (hodl_equity[-1] / init - 1) * 100
    
    # Risk metrics on engine
    daily_ret = pd.Series(eng_equity).pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(365) if daily_ret.std() > 0 else 0
    peak = pd.Series(eng_equity).cummax()
    max_dd = ((eng_equity - peak) / peak * 100).min()
    
    hodl_peak = pd.Series(hodl_equity).cummax()
    hodl_max_dd = ((hodl_equity - hodl_peak) / hodl_peak * 100).min()
    
    return {
        "label": label,
        "start": start,
        "end": end,
        "n_days": len(window),
        "engine_ret": eng_ret,
        "hodl_ret": hodl_ret,
        "alpha": eng_ret - hodl_ret,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "hodl_max_dd": hodl_max_dd,
        "mean_target": window["target"].mean(),
    }


def main():
    # Re-run full backtest once to get daily equity with targets
    import subprocess
    print("Running full 5y backtest with Phase 4 settings...")
    subprocess.run(["python3", "backtest_engine_real.py", "--output-dir", "phase4_v7"],
                   check=True, capture_output=True)
    
    # Load saved daily CSV (has engine signals) then re-simulate to get targets/equity
    signals = pd.read_csv("phase4_v7/engine_backtest_daily.csv", parse_dates=["date"])
    
    # Import backtest module and re-simulate to attach equity/target columns
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from backtest_engine_real import simulate_strategy, load_all_data
    dfs = load_all_data()
    result = simulate_strategy(signals, dfs)
    daily = result["df"]  # has equity, target, etc
    
    print(f"\n{'='*95}")
    print(f"  WALK-FORWARD: Phase 4 settings applied continuously, metrics per window")
    print(f"{'='*95}")
    print(f"{'Window':<15} {'Period':<24} {'Days':>5} {'Engine':>8} {'HODL':>8} {'Alpha':>8} {'Sharpe':>7} {'MaxDD':>8} {'HODL DD':>8} {'μ target':>9}")
    
    results = []
    for label, start, end in SPLITS:
        m = metrics_for_window(daily, label, start, end)
        if m:
            results.append(m)
            period = f"{start[2:]}..{end[2:]}"  # shorter
            print(f"{m['label']:<15} {period:<24} {m['n_days']:>5} "
                  f"{m['engine_ret']:>+7.1f}% {m['hodl_ret']:>+7.1f}% "
                  f"{m['alpha']:>+7.1f}% {m['sharpe']:>7.2f} "
                  f"{m['max_dd']:>7.1f}% {m['hodl_max_dd']:>7.1f}% "
                  f"{m['mean_target']:>8.3f}")
    
    Path("phase4_walk").mkdir(exist_ok=True)
    Path("phase4_walk/_summary.json").write_text(json.dumps(results, indent=2))
    print("\n→ saved phase4_walk/_summary.json")


if __name__ == "__main__":
    main()
