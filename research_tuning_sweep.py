#!/usr/bin/env python3
"""
Research sweep for Phase-2 tuning of OracAI bottom/top_prox and DD defender.

Questions answered:
  Q1. Which FG weight (in bottom/top_prox) is empirically best?
  Q2. Does an Extreme-Fear zone bonus (FG<20) improve bottom timing?
  Q3. Does the current DD defender fire when it shouldn't?
  Q4. Which "conflict block" rule (blocks ФИКСИРОВАТЬ when BULL+Fear) performs best?
  Q5. Does using 90d drawdown as POSITIVE signal in bottom_prox help?

Metrics:
  - Precision of bottom_prox > threshold: fraction of signals within 10% of next-N-day low
  - Recall on 90d-significant lows: how many 90d-relative lows are flagged
  - Risk-adjusted: Sharpe / max DD when the sized position uses the modified formula

Note: synthetic F&G from backtest_honest (same formula as backtest_v5.py).
Real F&G API is blocked in this environment — we replicate the repo's own proxy.
"""

import json
import math
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, '/home/claude/OracAI')
from backtest_honest import (
    load_data, calculate_rsi, synthetic_fg, detect_regime,
    INITIAL_CAPITAL, WARMUP
)

# ═══════════════════════════════════════════════════════════════════════
# STEP 1: Compute signals for every day (independent of action policy)
# ═══════════════════════════════════════════════════════════════════════

def build_signals(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['rsi'] = calculate_rsi(df['close'], 14)
    df['hi90'] = df['close'].rolling(90, min_periods=20).max()
    df['lo90'] = df['close'].rolling(90, min_periods=20).min()
    df['fg'] = synthetic_fg(df).fillna(50).round().astype(int).clip(1, 99)
    df['dd90'] = (df['close'] / df['hi90'] - 1) * 100  # drawdown from 90d hi, negative
    df = df.dropna()
    # We'll build bottom_prox per variant below; store detections here
    closes = df['close'].values
    rsis = df['rsi'].values
    fgs = df['fg'].values
    dates = df.index

    records = []
    for i in range(WARMUP, len(df)):
        prev = closes[:i]
        r = detect_regime(closes[i], prev, rsis[i], fgs[i])
        records.append({
            'date': dates[i], 'price': closes[i], 'rsi': rsis[i], 'fg': fgs[i],
            'dd90': df['dd90'].iloc[i],
            'regime': r['regime'], 'conf': r['conf'], 'risk': r['risk'],
            'direction': r['direction'],
            'prob_bull': r['probs']['BULL'], 'prob_bear': r['probs']['BEAR'],
            'prob_trans': r['probs']['TRANSITION'], 'prob_range': r['probs']['RANGE'],
            # Raw components BEFORE FG/dd blending, for sweeping different weights
            'base_bottom': r['probs']['BEAR'] * .4 + r['probs']['TRANSITION'] * .2 + r['probs']['RANGE'] * .15,
            'base_top': r['probs']['BULL'] * .4 + r['probs']['TRANSITION'] * .2 + r['probs']['RANGE'] * .15,
        })
    return pd.DataFrame(records).set_index('date')


# ═══════════════════════════════════════════════════════════════════════
# STEP 2: Variant recomputes bottom/top_prox with chosen parameters
# ═══════════════════════════════════════════════════════════════════════

def apply_variant(sig: pd.DataFrame,
                  fg_weight_bottom: float = 0.15,  # current prod weight
                  fg_weight_top: float = 0.15,
                  fg_asym_factor: float = 0.33,  # current: top -= fg_factor * 0.05, bottom += fg_factor * 0.15 → asym = 0.05/0.15 = 0.33
                  fg_extreme_boost: float = 0.0,  # bonus when FG<20 or FG>80
                  dd_bottom_weight: float = 0.0,  # add drawdown as positive bottom signal
                  rsi_weight: float = 0.30,       # current prod
                  ) -> pd.DataFrame:
    """Recompute bottom_prox and top_prox with variant parameters."""
    out = sig.copy()
    bottom = sig['base_bottom'].values.copy()
    top = sig['base_top'].values.copy()

    # Direction pressure (same as prod)
    d = sig['direction'].values
    pos = d >= 0
    neg = ~pos
    top[pos] += d[pos] * 0.25
    bottom[pos] -= d[pos] * 0.15
    bottom[neg] += np.abs(d[neg]) * 0.25
    top[neg] -= np.abs(d[neg]) * 0.15

    # RSI — symmetric weight
    rsi = sig['rsi'].values
    below50 = rsi < 50
    above50 = ~below50
    f_down = (50 - rsi[below50]) / 50.0
    f_up = (rsi[above50] - 50) / 50.0
    bottom[below50] += f_down * rsi_weight
    top[below50] -= f_down * rsi_weight * 0.5
    top[above50] += f_up * rsi_weight
    bottom[above50] -= f_up * rsi_weight * 0.5

    # FG — parametrised
    fg = sig['fg'].values
    below50 = fg < 50
    above50 = ~below50
    f_down = (50 - fg[below50]) / 50.0
    f_up = (fg[above50] - 50) / 50.0
    bottom[below50] += f_down * fg_weight_bottom
    top[below50] -= f_down * fg_weight_bottom * fg_asym_factor
    top[above50] += f_up * fg_weight_top
    bottom[above50] -= f_up * fg_weight_top * fg_asym_factor

    # Extreme-zone bonus
    if fg_extreme_boost > 0:
        extreme_fear = fg < 20
        extreme_greed = fg > 80
        bonus_fear = (20 - fg[extreme_fear]) / 20.0
        bonus_greed = (fg[extreme_greed] - 80) / 20.0
        bottom[extreme_fear] += bonus_fear * fg_extreme_boost
        top[extreme_greed] += bonus_greed * fg_extreme_boost

    # Drawdown-as-bottom-signal
    if dd_bottom_weight > 0:
        dd = sig['dd90'].values  # negative numbers
        # Map -15..-40 → 0..1
        dd_factor = np.clip((-dd - 15) / 25, 0, 1)
        bottom += dd_factor * dd_bottom_weight
        top -= dd_factor * dd_bottom_weight * 0.5

    out['bottom'] = np.clip(bottom, 0.05, 0.95)
    out['top'] = np.clip(top, 0.05, 0.95)
    return out


# ═══════════════════════════════════════════════════════════════════════
# STEP 3: Score — how well does bottom_prox flag actual lows?
# ═══════════════════════════════════════════════════════════════════════

def score_signals(sig: pd.DataFrame, fwd_days: int = 30, thr: float = 0.5) -> dict:
    """Forward-only: signal at day i scored against days i..i+fwd_days."""
    n = len(sig)
    prices = sig['price'].values
    bottoms = sig['bottom'].values
    tops = sig['top'].values

    # PRECISION: of signals above threshold, how many are within 10% of future low?
    b_hits = b_sigs = 0
    t_hits = t_sigs = 0
    for i in range(n - fwd_days):
        fut = prices[i:i + fwd_days]
        fut_min = fut.min()
        fut_max = fut.max()
        if bottoms[i] > thr:
            b_sigs += 1
            if prices[i] <= fut_min * 1.1: b_hits += 1
        if tops[i] > thr:
            t_sigs += 1
            if prices[i] >= fut_max * 0.9: t_hits += 1
    b_prec = b_hits / b_sigs if b_sigs else 0
    t_prec = t_hits / t_sigs if t_sigs else 0

    # RECALL on SIGNIFICANT 90d-relative lows (price within 5% of 90d-rolling min)
    # i.e. how many "real" local lows did we flag?
    sig['lo90_fwd'] = sig['price'].rolling(90, min_periods=20).min()
    real_low = sig['price'] <= sig['lo90_fwd'] * 1.05
    real_low_dates = sig[real_low].index
    flagged = sig[sig['bottom'] > thr].index
    # Recall: of real_low dates, how many had bottom>thr within ±7 days?
    caught = 0
    for d in real_low_dates:
        window = sig[(sig.index >= d - pd.Timedelta(days=7)) & (sig.index <= d + pd.Timedelta(days=7))]
        if (window['bottom'] > thr).any():
            caught += 1
    recall = caught / len(real_low_dates) if len(real_low_dates) else 0

    # F1-like
    f1 = 2 * b_prec * recall / (b_prec + recall) if (b_prec + recall) else 0

    return {
        'bottom_signals': b_sigs, 'bottom_precision': b_prec,
        'top_signals': t_sigs, 'top_precision': t_prec,
        'bottom_recall_on_real_lows': recall,
        'bottom_f1': f1,
        'n_real_lows': len(real_low_dates),
    }


# ═══════════════════════════════════════════════════════════════════════
# STEP 4: Simulate position sizing with variant policy
# ═══════════════════════════════════════════════════════════════════════

def simulate_policy(sig: pd.DataFrame,
                    dd_policy: str = "current",
                    conflict_block: bool = False,
                    rebalance_delta: float = 0.20,
                    tc_bps: float = 10) -> dict:
    """
    dd_policy options:
      - "current"    : reduce when dd<-15, dd<-25 (current prod)
      - "gated"      : reduce only if BEAR confirmation or FG>60 (greed-in-drawdown = trap)
      - "flipped"    : dd<-15 BOOSTS bottom_prox instead of killing position
      - "none"       : no DD adjustment
    conflict_block: if BULL>0.65 AND fg<35 AND target<0.7 → force 0.85
    """
    cash = INITIAL_CAPITAL * 0.10
    btc = INITIAL_CAPITAL * 0.90 / sig['price'].iloc[0]
    tc = tc_bps / 10000
    equity, fires, conflicts = [], 0, 0
    dates, prices, bottoms, tops, rsis, fgs, regimes, confs, risks, dd90s = (
        sig.index, sig['price'].values, sig['bottom'].values,
        sig['top'].values, sig['rsi'].values, sig['fg'].values,
        sig['regime'].values, sig['conf'].values, sig['risk'].values,
        sig['dd90'].values,
    )

    for i in range(len(sig)):
        price = prices[i]
        rsi = rsis[i]
        fg = fgs[i]
        bottom = bottoms[i]
        top = tops[i]
        regime = regimes[i]
        conf = confs[i]
        risk = risks[i]
        dd = dd90s[i]

        target = 0.90
        if rsi > 78 and top > 0.70 and conf > 0.20: target = 0.50
        elif rsi > 82 and top > 0.75: target = 0.40
        if regime == "BEAR" and conf > 0.30 and rsi > 40: target = min(target, 0.60)
        if bottom > 0.65 or rsi < 28: target = 1.00
        if risk == "CRISIS": target = 0.25
        elif risk == "TAIL" and rsi > 50: target = min(target, 0.55)

        # DD policy
        strong_bottom = (rsi < 30) or (bottom > 0.70)
        if dd_policy == "current" and not strong_bottom:
            if dd < -25: target = min(target, 0.30)
            elif dd < -15: target = min(target, 0.55)
        elif dd_policy == "gated" and not strong_bottom:
            bear_conf = (regime == "BEAR") or (rsi < 50 and fg > 60)
            if bear_conf:
                if dd < -25: target = min(target, 0.30)
                elif dd < -15: target = min(target, 0.60)
        elif dd_policy == "flipped":
            # don't modify target from DD directly — DD was already folded into bottom_prox upstream
            pass
        # "none" — nothing

        # Conflict block
        if conflict_block:
            bull_prob = sig['prob_bull'].iloc[i]
            if bull_prob > 0.65 and fg < 35 and target < 0.70:
                conflicts += 1
                target = 0.85

        target = round(target * 20) / 20

        equity_val = cash + btc * price
        cur = btc * price / equity_val if equity_val > 0 else 0
        delta = target - cur
        if abs(delta) > rebalance_delta:
            notional = abs(delta) * equity_val
            fee = notional * tc
            if delta > 0:
                buy = min(delta * equity_val, cash - fee)
                if buy > 0:
                    btc += buy / price; cash -= buy + fee; fires += 1
            else:
                sell = min(abs(delta) * equity_val / price, btc)
                btc -= sell; cash += sell * price - fee; fires += 1
        equity.append(cash + btc * price)

    eq = pd.Series(equity, index=sig.index)
    ret = (eq.iloc[-1] / INITIAL_CAPITAL - 1) * 100
    daily = eq.pct_change().dropna()
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0
    peak = eq.cummax(); dd_model = ((eq - peak) / peak * 100).min()
    hodl = (sig['price'].iloc[-1] / sig['price'].iloc[0] - 1) * 100
    hodl_eq = INITIAL_CAPITAL * (sig['price'].values / sig['price'].iloc[0])
    active = daily - pd.Series(hodl_eq, index=sig.index).pct_change().dropna()
    ir = active.mean() / active.std() * np.sqrt(365) if active.std() > 0 else 0
    return {
        'ret': ret, 'hodl_ret': hodl, 'alpha': ret - hodl,
        'sharpe': sharpe, 'max_dd': dd_model,
        'ir': ir, 'trades': fires, 'conflicts_blocked': conflicts,
    }


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    df = load_data()
    print(f"Loaded {len(df)} days. Building baseline signals...")
    sig_base = build_signals(df)
    print(f"Built {len(sig_base)} daily signals, {sig_base.index[0].date()} → {sig_base.index[-1].date()}")

    print("\n" + "=" * 80)
    print("  Q1 + Q2 — FG WEIGHT SWEEP (bottom-signal quality)")
    print("=" * 80)
    print(f"{'fg_bot':>8} {'fg_top':>8} {'extreme':>10} {'dd→bot':>8} {'Bprec':>8} {'Brec':>8} {'F1':>8} {'Bsig':>6}")
    sweep_rows = []
    for fg_w in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
        for extreme in [0.0, 0.15, 0.25]:
            for dd_bot in [0.0, 0.15, 0.25]:
                v = apply_variant(sig_base, fg_weight_bottom=fg_w, fg_weight_top=fg_w,
                                  fg_extreme_boost=extreme, dd_bottom_weight=dd_bot)
                s = score_signals(v, fwd_days=30, thr=0.5)
                row = {'fg_w': fg_w, 'extreme': extreme, 'dd_bot': dd_bot, **s}
                sweep_rows.append(row)
                print(f"{fg_w:>8.2f} {fg_w:>8.2f} {extreme:>10.2f} {dd_bot:>8.2f}"
                      f" {s['bottom_precision']*100:>7.1f}% {s['bottom_recall_on_real_lows']*100:>7.1f}%"
                      f" {s['bottom_f1']*100:>7.1f}% {s['bottom_signals']:>6d}")

    sweep_df = pd.DataFrame(sweep_rows)
    best_f1 = sweep_df.loc[sweep_df['bottom_f1'].idxmax()]
    print(f"\n▍Best F1 row: fg_w={best_f1['fg_w']}, extreme={best_f1['extreme']}, "
          f"dd_bot={best_f1['dd_bot']} → F1={best_f1['bottom_f1']*100:.1f}%")

    print("\n" + "=" * 80)
    print("  Q3 — DD DEFENDER POLICY COMPARISON (full 5y, same bottom/top formula)")
    print("=" * 80)
    # Use current prod-equivalent variant as baseline
    v_prod = apply_variant(sig_base, fg_weight_bottom=0.15, fg_weight_top=0.15)
    v_tuned = apply_variant(sig_base,
                            fg_weight_bottom=best_f1['fg_w'],
                            fg_weight_top=best_f1['fg_w'],
                            fg_extreme_boost=best_f1['extreme'],
                            dd_bottom_weight=best_f1['dd_bot'])

    print(f"{'variant':35s} {'return':>10s} {'alpha':>9s} {'Sharpe':>7s} {'IR':>7s} {'MaxDD':>8s} {'trades':>7s}")
    for label, sig_var in [("PROD (fg=0.15, DD=current)", v_prod),
                           ("TUNED bot/top signals", v_tuned)]:
        for dd_pol in ["current", "gated", "flipped", "none"]:
            for cblock in [False, True]:
                r = simulate_policy(sig_var, dd_policy=dd_pol, conflict_block=cblock)
                name = f"{label} dd={dd_pol} conflict={'Y' if cblock else 'n'}"
                print(f"{name:35s} {r['ret']:>9.1f}% {r['alpha']:>+8.1f}%"
                      f" {r['sharpe']:>7.2f} {r['ir']:>+7.2f} {r['max_dd']:>7.1f}% {r['trades']:>7d}")

    print("\n" + "=" * 80)
    print("  Q5 — HOW OFTEN DOES CURRENT DD DEFENDER FIRE 'WRONG'?")
    print("=" * 80)
    # Count days where DD defender fires (dd<-15) AND other signals disagree (BULL+fear)
    disagreement = (sig_base['dd90'] < -15) & (sig_base['prob_bull'] > 0.5) & (sig_base['fg'] < 35)
    total_dd_fires = (sig_base['dd90'] < -15).sum()
    print(f"Days where dd<-15: {total_dd_fires}")
    print(f"Of those, days where BULL>50% AND FG<35 (disagreement): {disagreement.sum()} "
          f"({disagreement.sum() / max(total_dd_fires,1) * 100:.0f}%)")
    # What price did these days lead to? Check next-30d return
    disag_days = sig_base[disagreement].index
    print(f"On disagreement days, next-30d BTC mean return:")
    rets = []
    prices = sig_base['price']
    for d in disag_days:
        fwd_date_idx = sig_base.index.get_loc(d)
        if fwd_date_idx + 30 < len(sig_base):
            rets.append((prices.iloc[fwd_date_idx + 30] / prices.iloc[fwd_date_idx] - 1) * 100)
    if rets:
        rets = np.array(rets)
        print(f"  mean: {rets.mean():+.1f}%, median: {np.median(rets):+.1f}%, "
              f"% positive: {(rets > 0).mean() * 100:.0f}%")
    # Compare to baseline "all days" next-30d
    fwd_ret_30d = (prices.shift(-30) / prices - 1).dropna() * 100
    print(f"Baseline (all days) next-30d mean: {fwd_ret_30d.mean():+.1f}%, "
          f"% positive: {(fwd_ret_30d > 0).mean() * 100:.0f}%")

    # Save
    sweep_df.to_csv('/home/claude/OracAI/research_sweep_results.csv', index=False)
    print("\n→ saved research_sweep_results.csv")


if __name__ == "__main__":
    main()
