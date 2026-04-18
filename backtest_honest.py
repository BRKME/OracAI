#!/usr/bin/env python3
"""
Honest 5-year backtest of OracAI model with methodology fixes.

Fixes vs backtest_v5.py:
  - No look-ahead bias (bottom/top evaluated via forward-only windows after
    the signal, reported as "next-30d extremum proximity")
  - Correct crisis_false_positive formula
  - No partial credit for TRANSITION
  - Transaction costs: 10 bps per rebalance trade (both sides)
  - Walk-forward: in-sample (first 60%) vs out-of-sample (last 40%)
  - Baselines: HODL, 60/40, SMA50/200 crossover, fixed-90%
  - Significance: bootstrap CI on alpha, t-stat on daily active returns
  - Information Ratio (tracking-error-adjusted alpha) — the honest metric
    for a HODL-biased strategy

Same simplified regime logic as backtest_v5.py (so this is apples-to-apples
on model-scoring; the separate issue of "backtest tests simplified model,
not prod engine.py" is called out in the written report).
"""

import logging
import math
import json
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ───────── parameters ─────────
INITIAL_CAPITAL = 100_000
TRANSACTION_COST_BPS = 10          # 10 bps (0.10%) per traded notional
REBALANCE_DELTA = 0.20             # only trade if target deviates by >20%
WARMUP = 50


# ────────────────────────────────────────────────────────────────
# DATA
# ────────────────────────────────────────────────────────────────
def load_data(csv_path: str = "data/btc.csv") -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=['time']).set_index('time')
    df = df[['PriceUSD']].dropna()
    df.columns = ['close']
    df = df[df.index >= '2020-10-01']
    return df


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def synthetic_fg(df: pd.DataFrame) -> pd.Series:
    """Deterministic F&G synth from price+RSI+vol (same formula as backtest_v5)."""
    rsi = calculate_rsi(df['close'], 14)
    ret30 = df['close'].pct_change(30) * 100
    vol20 = df['close'].pct_change().rolling(20).std() * 100
    rsi_c = rsi * 0.4
    mom_c = (50 + ret30 * 1.5).clip(0, 100) * 0.35
    vol_c = (100 - vol20 * 15).clip(0, 100) * 0.25
    return (rsi_c + mom_c + vol_c).clip(1, 99)


def fetch_real_fg() -> Dict[str, int]:
    """Try real F&G from alternative.me (covers 2018-).  Falls back silently."""
    import requests
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=0&format=json", timeout=15)
        data = r.json().get('data', [])
        out = {}
        for it in data:
            d = datetime.fromtimestamp(int(it['timestamp'])).strftime('%Y-%m-%d')
            out[d] = int(it['value'])
        logger.info(f"Loaded {len(out)} days of real F&G")
        return out
    except Exception as e:
        logger.warning(f"Real F&G unavailable: {e}; using synthetic")
        return {}


# ────────────────────────────────────────────────────────────────
# MODEL (identical simplified logic to backtest_v5.py)
# ────────────────────────────────────────────────────────────────
def detect_regime(price: float, prev_closes: np.ndarray, rsi: float, fg: int) -> Dict:
    if len(prev_closes) >= 50:
        ma20 = prev_closes[-20:].mean()
        ma50 = prev_closes[-50:].mean()
        vol = pd.Series(prev_closes[-20:]).pct_change().std() * 100
        ret7 = (price / prev_closes[-7] - 1) * 100 if len(prev_closes) >= 7 else 0
        ret30 = (price / prev_closes[-30] - 1) * 100 if len(prev_closes) >= 30 else 0
    else:
        ma20 = ma50 = price; vol = 2.0; ret7 = ret30 = 0

    fg_norm = (fg - 50) / 50
    p = {'BULL': .25, 'BEAR': .25, 'RANGE': .25, 'TRANSITION': .25}

    if price > ma20 * 1.05 and price > ma50 * 1.1: p['BULL'] += .25; p['BEAR'] -= .15
    elif price < ma20 * 0.95 and price < ma50 * 0.9: p['BEAR'] += .25; p['BULL'] -= .15

    if ret7 > 10: p['BULL'] += .15
    elif ret7 < -10: p['BEAR'] += .15
    if ret30 > 20: p['BULL'] += .2
    elif ret30 < -20: p['BEAR'] += .2

    if rsi < 30: p['TRANSITION'] += .15; p['BEAR'] += .1
    elif rsi > 70: p['TRANSITION'] += .15; p['BULL'] += .1
    elif 40 <= rsi <= 60: p['RANGE'] += .1

    if fg < 25: p['BEAR'] += .15; p['TRANSITION'] += .1
    elif fg > 75: p['BULL'] += .15; p['TRANSITION'] += .1

    vol_z = (vol - 2.5) / 1.5
    if vol_z > 2: p['TRANSITION'] += .2; p['RANGE'] -= .1
    elif vol_z < -1: p['RANGE'] += .15

    s = sum(p.values())
    p = {k: v / s for k, v in p.items()}
    regime = max(p, key=p.get)

    ent = -sum(v * math.log(v + 1e-10) for v in p.values())
    conf = 1 - ent / math.log(4)

    if ret7 > 5 or ret7 < -5:
        direction = ret7 / 20
    else:
        direction = fg_norm * 0.3 + (rsi - 50) / 100
    direction = max(-1, min(1, direction))

    if vol_z > 2 or (fg < 15 and vol > 3.5): risk = "CRISIS"
    elif fg < 20 or vol_z > 1.2: risk = "TAIL"
    elif vol_z > 0.3 or abs(ret7) > 8: risk = "ELEVATED"
    else: risk = "NORMAL"

    bottom = p['BEAR'] * .4 + p['TRANSITION'] * .2 + p['RANGE'] * .15
    top = p['BULL'] * .4 + p['TRANSITION'] * .2 + p['RANGE'] * .15
    if direction < 0:
        bottom += abs(direction) * .25; top -= abs(direction) * .15
    else:
        top += direction * .25; bottom -= direction * .15
    if rsi < 50:
        f = (50 - rsi) / 50; bottom += f * .3; top -= f * .15
    else:
        f = (rsi - 50) / 50; top += f * .3; bottom -= f * .15
    if fg < 50:
        f = (50 - fg) / 50; bottom += f * .15; top -= f * .05
    else:
        f = (fg - 50) / 50; top += f * .15; bottom -= f * .05
    bottom = max(.05, min(.95, bottom))
    top = max(.05, min(.95, top))

    return dict(regime=regime, probs=p, conf=conf, risk=risk, direction=direction,
                rsi=rsi, fg=fg, vol=vol, ret7=ret7, bottom=bottom, top=top)


def get_target(regime, conf, direction, risk, rsi, bottom, top, dd_from_high):
    """Same HODL-biased sizing as backtest_v5.py."""
    target = 0.90
    if rsi > 78 and top > .70 and conf > .20: target = .50
    elif rsi > 82 and top > .75: target = .40
    if regime == "BEAR" and conf > .30 and rsi > 40: target = min(target, .60)
    if bottom > .65 or rsi < 28: target = 1.00
    if risk == "CRISIS": target = .25
    elif risk == "TAIL" and rsi > 50: target = min(target, .55)
    strong_bot = (rsi < 30) or (bottom > .70)
    if not strong_bot:
        if dd_from_high < -25: target = min(target, .30)
        elif dd_from_high < -15: target = min(target, .55)
    target = round(target * 20) / 20
    return max(.20, min(1.0, target))


# ────────────────────────────────────────────────────────────────
# BACKTEST with cost-aware rebalancing
# ────────────────────────────────────────────────────────────────
def run(df: pd.DataFrame, fg_real: Dict[str, int], label: str = "full") -> Dict:
    df = df.copy()
    df['rsi'] = calculate_rsi(df['close'], 14)
    df['hi90'] = df['close'].rolling(90, min_periods=20).max()
    df['fg_synth'] = synthetic_fg(df)
    df = df.dropna()

    cash = INITIAL_CAPITAL * 0.10
    btc = (INITIAL_CAPITAL * 0.90) / df.iloc[0]['close']
    equity_curve, signals, trades = [], [], []
    tc_rate = TRANSACTION_COST_BPS / 10_000

    closes = df['close'].values
    rsis = df['rsi'].values
    his = df['hi90'].values
    fgs_synth = df['fg_synth'].values
    dates = df.index

    for i in range(WARMUP, len(df)):
        price = closes[i]
        rsi = rsis[i]
        d = dates[i].strftime('%Y-%m-%d')
        fg = fg_real.get(d, int(fgs_synth[i]))
        prev = closes[:i]

        r = detect_regime(price, prev, rsi, fg)
        dd = (price / his[i] - 1) * 100 if his[i] > 0 else 0
        target = get_target(r['regime'], r['conf'], r['direction'],
                            r['risk'], rsi, r['bottom'], r['top'], dd)

        equity = cash + btc * price
        cur_pos = (btc * price) / equity if equity > 0 else 0

        signals.append({
            'date': dates[i], 'price': price,
            'regime': r['regime'], 'conf': r['conf'],
            'risk': r['risk'], 'rsi': rsi, 'fg': fg,
            'bottom': r['bottom'], 'top': r['top'],
            'target': target, 'cur_pos': cur_pos,
        })

        delta = target - cur_pos
        if abs(delta) > REBALANCE_DELTA:
            trade_notional = abs(delta) * equity
            fee = trade_notional * tc_rate
            if delta > 0:  # buy
                buy_value = min(delta * equity, cash - fee)
                if buy_value > 0:
                    btc += buy_value / price
                    cash -= buy_value + fee
                    trades.append({'date': dates[i], 'side': 'BUY', 'price': price,
                                   'notional': buy_value, 'fee': fee})
            else:  # sell
                sell_btc = min(abs(delta) * equity / price, btc)
                proceeds = sell_btc * price
                btc -= sell_btc
                cash += proceeds - fee
                trades.append({'date': dates[i], 'side': 'SELL', 'price': price,
                               'notional': proceeds, 'fee': fee})

        equity_curve.append({'date': dates[i], 'equity': cash + btc * price, 'price': price})

    eq = pd.DataFrame(equity_curve)
    sig_df = pd.DataFrame(signals)

    # ─── Returns ───
    model_ret = (eq['equity'].iloc[-1] / INITIAL_CAPITAL - 1) * 100
    hodl_ret = (eq['price'].iloc[-1] / eq['price'].iloc[0] - 1) * 100
    alpha = model_ret - hodl_ret

    model_daily = eq['equity'].pct_change().dropna()
    hodl_daily = eq['price'].pct_change().dropna()
    active = model_daily - hodl_daily

    sharpe = model_daily.mean() / model_daily.std() * np.sqrt(365) if model_daily.std() > 0 else 0
    hodl_sharpe = hodl_daily.mean() / hodl_daily.std() * np.sqrt(365) if hodl_daily.std() > 0 else 0
    # Information Ratio = annualized alpha / tracking error
    info_ratio = active.mean() / active.std() * np.sqrt(365) if active.std() > 0 else 0
    # t-stat for active daily returns (H0: alpha = 0)
    t_stat = active.mean() / (active.std() / np.sqrt(len(active))) if active.std() > 0 else 0

    peak = eq['equity'].cummax()
    max_dd = ((eq['equity'] - peak) / peak * 100).min()
    hodl_peak = eq['price'].cummax()
    hodl_dd = ((eq['price'] - hodl_peak) / hodl_peak * 100).min()

    # ─── Regime accuracy (NO partial credit) ───
    n = len(sig_df)
    correct = 0; considered = 0
    bull_c = bull_t = bear_c = bear_t = 0
    for i in range(n - 7):
        fut = sig_df.iloc[i + 7]['price']
        pct = (fut / sig_df.iloc[i]['price'] - 1) * 100
        reg = sig_df.iloc[i]['regime']
        if reg == 'BULL':
            bull_t += 1; considered += 1
            if pct > 0: bull_c += 1; correct += 1
        elif reg == 'BEAR':
            bear_t += 1; considered += 1
            if pct < 0: bear_c += 1; correct += 1
        elif reg == 'RANGE':
            considered += 1
            if abs(pct) < 5: correct += 1
        # TRANSITION excluded from accuracy (not scored)
    regime_acc = correct / considered * 100 if considered else 0
    bull_acc = bull_c / bull_t * 100 if bull_t else 0
    bear_acc = bear_c / bear_t * 100 if bear_t else 0

    # ─── Naive baseline: always predict BULL in an uptrending market ───
    # Fraction of 7d windows that were positive — this is what "always BULL" would score
    up_frac = sum(1 for i in range(n - 7)
                  if sig_df.iloc[i + 7]['price'] > sig_df.iloc[i]['price']) / max(n - 7, 1) * 100

    # ─── Signal quality ───
    buys = sells = bw = sw = 0
    for i in range(n - 7):
        fut = sig_df.iloc[i + 7]['price']
        pct = (fut / sig_df.iloc[i]['price'] - 1) * 100
        if sig_df.iloc[i]['target'] > sig_df.iloc[i]['cur_pos'] + 0.05:
            buys += 1
            if pct > 5: bw += 1
        elif sig_df.iloc[i]['target'] < sig_df.iloc[i]['cur_pos'] - 0.05:
            sells += 1
            if pct < -5: sw += 1
    buy_wr = bw / buys * 100 if buys else 0
    sell_wr = sw / sells * 100 if sells else 0

    # ─── Crash detection (FIXED false-positive formula) ───
    crashes = []
    for i in range(n - 14):
        fut = sig_df.iloc[i + 14]['price']
        pct = (fut / sig_df.iloc[i]['price'] - 1) * 100
        if pct < -10:
            crashes.append(sig_df.iloc[i]['date'])

    tail_crisis_dates = sig_df[sig_df['risk'].isin(['TAIL', 'CRISIS'])]['date'].tolist()

    crashes_warned = 0
    for c in crashes:
        if any((c - d).days >= 0 and (c - d).days <= 7 for d in tail_crisis_dates):
            crashes_warned += 1
    crash_detection = crashes_warned / len(crashes) * 100 if crashes else 0

    # FIXED FP: of all CRISIS signals, what fraction were NOT followed by crash in next 14d?
    crisis_dates = sig_df[sig_df['risk'] == 'CRISIS']['date'].tolist()
    crisis_tp = 0
    for cd in crisis_dates:
        # find index
        row_idx = sig_df[sig_df['date'] == cd].index[0]
        if row_idx + 14 < n:
            fut = sig_df.iloc[row_idx + 14]['price']
            cur = sig_df.iloc[row_idx]['price']
            if (fut / cur - 1) * 100 < -10:
                crisis_tp += 1
    crisis_fp_rate = (1 - crisis_tp / len(crisis_dates)) * 100 if crisis_dates else 0

    # ─── Bottom/Top: FORWARD-ONLY window (no look-ahead) ───
    # After a bottom signal at day i, check if day i is within 10% of min
    # in the next 30 days (bounded forward), not ±15 days around i.
    b_sig = b_corr = t_sig = t_corr = 0
    for i in range(n - 30):
        row = sig_df.iloc[i]
        future_30 = sig_df.iloc[i:i + 30]['price']
        fut_min = future_30.min()
        fut_max = future_30.max()
        if row['bottom'] > 0.6:
            b_sig += 1
            if row['price'] <= fut_min * 1.1: b_corr += 1
        if row['top'] > 0.6:
            t_sig += 1
            if row['price'] >= fut_max * 0.9: t_corr += 1
    bottom_acc = b_corr / b_sig * 100 if b_sig else 0
    top_acc = t_corr / t_sig * 100 if t_sig else 0

    # ─── Bootstrap alpha CI ───
    rng = np.random.default_rng(42)
    B = 2000
    n_days = len(active)
    boot_alphas = []
    for _ in range(B):
        idx = rng.integers(0, n_days, n_days)
        sampled = active.values[idx]
        ann_alpha = (1 + sampled.mean()) ** 365 - 1
        boot_alphas.append(ann_alpha * 100)
    boot_alphas = np.array(boot_alphas)
    ci_low, ci_high = np.percentile(boot_alphas, [2.5, 97.5])

    total_fees = sum(t['fee'] for t in trades)

    out = {
        'label': label,
        'period_days': len(eq),
        'start': str(eq['date'].iloc[0].date()),
        'end': str(eq['date'].iloc[-1].date()),
        'n_trades': len(trades),
        'total_fees': total_fees,
        'model_return_pct': model_ret,
        'hodl_return_pct': hodl_ret,
        'alpha_pct': alpha,
        'alpha_annualized_pct': (1 + active.mean()) ** 365 * 100 - 100,
        'alpha_ci95_low_pct_ann': ci_low,
        'alpha_ci95_high_pct_ann': ci_high,
        'alpha_tstat': t_stat,
        'alpha_pvalue_approx': 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / math.sqrt(2)))),
        'model_sharpe': sharpe,
        'hodl_sharpe': hodl_sharpe,
        'information_ratio': info_ratio,
        'model_max_dd_pct': max_dd,
        'hodl_max_dd_pct': hodl_dd,
        'regime_acc_pct_no_partial': regime_acc,
        'naive_always_up_pct': up_frac,
        'bull_acc_pct': bull_acc,
        'bear_acc_pct': bear_acc,
        'buy_win_rate_pct': buy_wr,
        'sell_win_rate_pct': sell_wr,
        'crash_detection_pct': crash_detection,
        'crisis_false_positive_pct_fixed': crisis_fp_rate,
        'n_crashes': len(crashes),
        'n_crisis_signals': len(crisis_dates),
        'bottom_acc_fwd_only_pct': bottom_acc,
        'top_acc_fwd_only_pct': top_acc,
        'bottom_signals': b_sig,
        'top_signals': t_sig,
    }
    return out, eq, sig_df


# ────────────────────────────────────────────────────────────────
# BASELINES
# ────────────────────────────────────────────────────────────────
def baseline_fixed_90(df: pd.DataFrame) -> Dict:
    """Constant 90% BTC, 10% cash. No rebalancing."""
    df = df.iloc[WARMUP:].copy()
    cash = INITIAL_CAPITAL * 0.10
    btc = (INITIAL_CAPITAL * 0.90) / df.iloc[0]['close']
    eq = cash + btc * df['close']
    ret = (eq.iloc[-1] / INITIAL_CAPITAL - 1) * 100
    daily = eq.pct_change().dropna()
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0
    peak = eq.cummax(); dd = ((eq - peak) / peak * 100).min()
    return dict(name="Fixed 90/10", ret=ret, sharpe=sharpe, dd=dd)


def baseline_sma_crossover(df: pd.DataFrame) -> Dict:
    """Classic SMA50/SMA200: long when SMA50 > SMA200, else cash."""
    df = df.copy()
    df['sma50'] = df['close'].rolling(50).mean()
    df['sma200'] = df['close'].rolling(200).mean()
    df = df.dropna()
    cash = INITIAL_CAPITAL; btc = 0; equity = []; trades = 0
    tc = TRANSACTION_COST_BPS / 10_000
    for _, row in df.iterrows():
        long = row['sma50'] > row['sma200']
        price = row['close']; eq = cash + btc * price
        if long and btc == 0:
            fee = cash * tc; btc = (cash - fee) / price; cash = 0; trades += 1
        elif not long and btc > 0:
            proceeds = btc * price; fee = proceeds * tc; cash = proceeds - fee; btc = 0; trades += 1
        equity.append(cash + btc * price)
    eq = pd.Series(equity, index=df.index)
    ret = (eq.iloc[-1] / INITIAL_CAPITAL - 1) * 100
    daily = eq.pct_change().dropna()
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0
    peak = eq.cummax(); dd = ((eq - peak) / peak * 100).min()
    return dict(name="SMA50/200", ret=ret, sharpe=sharpe, dd=dd, trades=trades)


def baseline_6040(df: pd.DataFrame) -> Dict:
    """60% BTC / 40% cash, rebalanced monthly."""
    df = df.iloc[WARMUP:].copy()
    cash = INITIAL_CAPITAL * 0.40
    btc = (INITIAL_CAPITAL * 0.60) / df.iloc[0]['close']
    equity = []; tc = TRANSACTION_COST_BPS / 10_000
    last_rebal = df.index[0]
    for d, row in df.iterrows():
        price = row['close']; eq_val = cash + btc * price
        if (d - last_rebal).days >= 30:
            target_btc_val = eq_val * 0.60
            current_btc_val = btc * price
            diff = target_btc_val - current_btc_val
            if abs(diff) > eq_val * 0.02:
                if diff > 0:
                    fee = diff * tc; btc += (diff - fee) / price; cash -= diff
                else:
                    sell = abs(diff); fee = sell * tc
                    btc -= sell / price; cash += sell - fee
            last_rebal = d
        equity.append(cash + btc * price)
    eq = pd.Series(equity, index=df.index)
    ret = (eq.iloc[-1] / INITIAL_CAPITAL - 1) * 100
    daily = eq.pct_change().dropna()
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0
    peak = eq.cummax(); dd = ((eq - peak) / peak * 100).min()
    return dict(name="60/40 monthly rebal", ret=ret, sharpe=sharpe, dd=dd)


# ────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────
def main():
    df = load_data()
    logger.info(f"Full dataset: {len(df)} days, {df.index[0].date()} → {df.index[-1].date()}")
    fg_real = fetch_real_fg()

    # Full period
    full, eq_full, sig_full = run(df, fg_real, "full_5y")

    # Walk-forward: in-sample 60%, OOS 40%
    split = int(len(df) * 0.6)
    df_is = df.iloc[:split + WARMUP]  # keep warmup in IS
    df_oos = df.iloc[split:]
    is_res, _, _ = run(df_is, fg_real, "in_sample_60pct")
    oos_res, _, _ = run(df_oos, fg_real, "out_of_sample_40pct")

    # Align baselines to same post-warmup period as model
    df_aligned = df.copy()
    df_aligned['rsi'] = calculate_rsi(df_aligned['close'], 14)
    df_aligned['hi90'] = df_aligned['close'].rolling(90, min_periods=20).max()
    df_aligned = df_aligned.dropna()
    b_fixed = baseline_fixed_90(df_aligned)
    b_sma = baseline_sma_crossover(df_aligned)
    b_6040 = baseline_6040(df_aligned)
    # Pure HODL 100% (no cash buffer) baseline
    bh_start = df_aligned.iloc[WARMUP]['close']
    bh_end = df_aligned.iloc[-1]['close']
    bh_series = df_aligned.iloc[WARMUP:]['close']
    bh_daily = bh_series.pct_change().dropna()
    bh_peak = bh_series.cummax(); bh_dd = ((bh_series - bh_peak) / bh_peak * 100).min()
    b_hodl = dict(name="HODL 100% (no cash)",
                  ret=(bh_end/bh_start - 1)*100,
                  sharpe=bh_daily.mean()/bh_daily.std()*np.sqrt(365) if bh_daily.std() > 0 else 0,
                  dd=bh_dd)

    # Save
    results = {
        'full': full, 'in_sample': is_res, 'oos': oos_res,
        'baselines': {'hodl_pure': b_hodl, 'fixed_90': b_fixed,
                      'sma_50_200': b_sma, 'sixty_forty': b_6040}
    }
    with open('honest_backtest_results.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)

    # Print
    def pr(r):
        print(f"  period:          {r['start']} → {r['end']}  ({r['period_days']}d)")
        print(f"  trades:          {r['n_trades']}  (fees paid: ${r['total_fees']:,.0f})")
        print(f"  model return:    {r['model_return_pct']:+.1f}%")
        print(f"  HODL return:     {r['hodl_return_pct']:+.1f}%")
        print(f"  alpha (cum):     {r['alpha_pct']:+.1f}%")
        print(f"  alpha (ann):     {r['alpha_annualized_pct']:+.2f}%  "
              f"95% CI [{r['alpha_ci95_low_pct_ann']:+.2f}%, {r['alpha_ci95_high_pct_ann']:+.2f}%]")
        print(f"  alpha t-stat:    {r['alpha_tstat']:+.2f}  (p≈{r['alpha_pvalue_approx']:.3f})")
        print(f"  information rt:  {r['information_ratio']:+.2f}")
        print(f"  Sharpe model:    {r['model_sharpe']:.2f}  |  HODL Sharpe: {r['hodl_sharpe']:.2f}")
        print(f"  max DD model:    {r['model_max_dd_pct']:.1f}%  |  HODL DD: {r['hodl_max_dd_pct']:.1f}%")
        print(f"  regime acc:      {r['regime_acc_pct_no_partial']:.1f}%  "
              f"(naive always-up baseline: {r['naive_always_up_pct']:.1f}%)")
        print(f"    BULL/BEAR acc: {r['bull_acc_pct']:.1f}% / {r['bear_acc_pct']:.1f}%")
        print(f"  buy/sell WR:     {r['buy_win_rate_pct']:.1f}% / {r['sell_win_rate_pct']:.1f}%")
        print(f"  crash detect:    {r['crash_detection_pct']:.1f}%  "
              f"({r['n_crashes']} crashes, {r['n_crisis_signals']} CRISIS signals)")
        print(f"  CRISIS FP rate:  {r['crisis_false_positive_pct_fixed']:.1f}%  (fixed formula)")
        print(f"  bottom acc fwd:  {r['bottom_acc_fwd_only_pct']:.1f}% ({r['bottom_signals']} signals)")
        print(f"  top acc fwd:     {r['top_acc_fwd_only_pct']:.1f}% ({r['top_signals']} signals)")

    print("\n" + "=" * 70)
    print("  OracAI v5.8 — HONEST BACKTEST")
    print("=" * 70)
    print("\n▍FULL 5Y PERIOD")
    pr(full)
    print("\n▍IN-SAMPLE (first 60%)")
    pr(is_res)
    print("\n▍OUT-OF-SAMPLE (last 40%)")
    pr(oos_res)
    print("\n▍BASELINES (full period, with 10bps costs)")
    for k, b in results['baselines'].items():
        tr = b.get('trades', 'n/a')
        print(f"  {b['name']:22s}  return {b['ret']:+7.1f}%  Sharpe {b['sharpe']:.2f}  DD {b['dd']:.1f}%  trades={tr}")
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
