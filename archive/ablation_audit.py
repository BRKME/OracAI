#!/usr/bin/env python3
"""
Independent audit ablation: how much of the engine's HODL-matching return
comes from the regime engine vs the SMA200 recovery override / HODL base?

Loads the already-generated engine_backtest_daily.csv (real engine signals)
and re-runs the rebalancing simulation under several policies that
progressively strip out the engine's contribution.
"""
import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path("data/external")
sig = pd.read_csv("engine_backtest_daily.csv", parse_dates=["date"])

btc = pd.read_csv(DATA / "btc_ohlcv.csv", parse_dates=["date"])[["date", "close", "high"]]
fg = pd.read_csv(DATA / "fear_greed.csv", parse_dates=["date"])[["date", "fear_greed"]]

df = sig.merge(btc.rename(columns={"high": "btc_high"}), on="date", how="left")
df = df.merge(fg, on="date", how="left")
df["fear_greed"] = df["fear_greed"].ffill()
df["btc_hi90"] = df["btc_high"].rolling(90, min_periods=1).max()
df["dd_from_high"] = (df["price"] / df["btc_hi90"] - 1) * 100
df["sma200"] = df["price"].rolling(200, min_periods=30).mean()
df["sma200_ratio"] = df["price"] / df["sma200"]
df["above"] = (df["sma200_ratio"] > 1.0).astype(int)
grp = (df["above"] != df["above"].shift()).cumsum()
df["days_above"] = df.groupby(grp)["above"].cumsum()
df["sma50"] = df["price"].rolling(50, min_periods=1).mean()


def bear_conf(row, fg_v):
    return (row["regime"] == "BEAR"
            or row["risk_level"] < -0.2
            or (fg_v is not None and fg_v > 65 and row["dd_from_high"] < -15))


# ---- policies ----
def p_full(row, fg_v):
    """Phase-4 production policy: engine + DD defender + SMA200 override."""
    t = 0.90
    if row["regime"] == "BEAR" and row["conf"] > 0.30:
        t = min(t, 0.60)
    if row["risk_state"] == "CRISIS":
        t = 0.25
    elif row["risk_state"] == "TAIL":
        t = min(t, 0.55)
    bc = bear_conf(row, fg_v)
    if bc:
        if row["dd_from_high"] < -25:
            t = min(t, 0.30)
        elif row["dd_from_high"] < -15:
            t = min(t, 0.60)
    t = min(t, row["exposure_cap"])
    if row["sma200_ratio"] > 1.0 and row["days_above"] >= 10 and not bc:
        t = max(t, 0.95)
    return round(t * 20) / 20


def p_no_override(row, fg_v):
    """Engine + DD defender but WITHOUT the SMA200 recovery override."""
    t = 0.90
    if row["regime"] == "BEAR" and row["conf"] > 0.30:
        t = min(t, 0.60)
    if row["risk_state"] == "CRISIS":
        t = 0.25
    elif row["risk_state"] == "TAIL":
        t = min(t, 0.55)
    bc = bear_conf(row, fg_v)
    if bc:
        if row["dd_from_high"] < -25:
            t = min(t, 0.30)
        elif row["dd_from_high"] < -15:
            t = min(t, 0.60)
    t = min(t, row["exposure_cap"])
    return round(t * 20) / 20


def p_engine_only(row, fg_v):
    """Pure regime signal, no SMA override, no DD defender, no exposure cap."""
    r = row["regime"]
    if r == "BULL":
        t = 1.00
    elif r == "BEAR":
        t = 0.20
    elif r == "RANGE":
        t = 0.50
    else:  # TRANSITION
        t = 0.60
    return round(t * 20) / 20


def p_dumb_sma(row, fg_v):
    """NO ENGINE AT ALL. 90% base; 95% if above SMA200>=10d; defend on drawdown.
    Uses only price-derived SMA200 + drawdown — engine regime ignored entirely."""
    t = 0.90
    # price-only 'bear': below SMA200
    below = row["sma200_ratio"] < 1.0
    if below and row["dd_from_high"] < -25:
        t = min(t, 0.30)
    elif below and row["dd_from_high"] < -15:
        t = min(t, 0.60)
    if row["sma200_ratio"] > 1.0 and row["days_above"] >= 10:
        t = max(t, 0.95)
    return round(t * 20) / 20


def simulate(policy, rebalance_delta=0.20, tc_bps=10, init=100_000):
    cash = init * 0.10
    held = init * 0.90 / df["price"].iloc[0]
    tc = tc_bps / 10_000
    n_tr = 0
    eqs, tgts = [], []
    for _, row in df.iterrows():
        fg_v = int(row["fear_greed"]) if not pd.isna(row["fear_greed"]) else None
        t = policy(row, fg_v)
        price = row["price"]
        eq = cash + held * price
        w = held * price / eq if eq > 0 else 0
        d = t - w
        if abs(d) > rebalance_delta:
            notional = abs(d) * eq
            fee = notional * tc
            if d > 0:
                buy = min(d * eq, cash - fee)
                if buy > 0:
                    held += buy / price
                    cash -= buy + fee
                    n_tr += 1
            else:
                sell = min(abs(d) * eq / price, held)
                held -= sell
                cash += sell * price - fee
                n_tr += 1
        eqs.append(cash + held * price)
        tgts.append(t)
    eq = pd.Series(eqs, index=df["date"])
    dr = eq.pct_change().dropna()
    sharpe = dr.mean() / dr.std() * np.sqrt(365) if dr.std() > 0 else 0
    mdd = ((eq - eq.cummax()) / eq.cummax() * 100).min()
    return {"ret": (eqs[-1] / init - 1) * 100, "sharpe": sharpe,
            "mdd": mdd, "trades": n_tr, "avg_target": np.mean(tgts)}


hodl = (df["price"].iloc[-1] / df["price"].iloc[0] - 1) * 100
hodl_eq = pd.Series((df["price"] / df["price"].iloc[0]).values, index=df["date"])
hodl_mdd = ((hodl_eq - hodl_eq.cummax()) / hodl_eq.cummax() * 100).min()

print(f"Period: {df['date'].min().date()} → {df['date'].max().date()} ({len(df)} days)\n")
print(f"{'Policy':<34s}{'Return':>9s}{'Alpha':>8s}{'Sharpe':>8s}{'MaxDD':>8s}{'Trades':>7s}{'AvgPos':>8s}")
print("-" * 82)
print(f"{'HODL (benchmark)':<34s}{hodl:>8.1f}%{'—':>8s}{'':>8s}{hodl_mdd:>7.1f}%{'0':>7s}{'1.00':>8s}")
for name, pol in [
    ("1. Full Phase-4 (engine+override)", p_full),
    ("2. Engine + DD, NO SMA override", p_no_override),
    ("3. Pure regime signal only", p_engine_only),
    ("4. DUMB SMA200+DD (NO engine)", p_dumb_sma),
]:
    r = simulate(pol)
    print(f"{name:<34s}{r['ret']:>8.1f}%{r['ret']-hodl:>+7.1f}%{r['sharpe']:>8.2f}{r['mdd']:>7.1f}%{r['trades']:>7d}{r['avg_target']:>8.2f}")

# Correlation: how often does full policy target == dumb policy target?
ft, dt = [], []
for _, row in df.iterrows():
    fg_v = int(row["fear_greed"]) if not pd.isna(row["fear_greed"]) else None
    ft.append(p_full(row, fg_v))
    dt.append(p_dumb_sma(row, fg_v))
ft, dt = np.array(ft), np.array(dt)
agree = (ft == dt).mean() * 100
print(f"\nDays where full-policy target == dumb-SMA target: {agree:.1f}%")
print(f"Mean |target difference| (full vs dumb): {np.abs(ft-dt).mean():.3f}")
