#!/usr/bin/env python3
"""
Research using REAL 200-day prod bucket history.

This data is actual production output from engine.py — it bypasses the
'synthetic FG ≈ price' problem that plagued research_tuning_sweep.py.

We recompute the regime softmax with variant LOGIT weights and score
against actual BTC prices over the same 200 days.
"""
import json, math
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, '/home/claude/OracAI')

# Load real prod data
d = json.load(open('/home/claude/OracAI/state/engine_state.json'))
bh = d['bucket_history']
regime_log = d['regime_log']
from datetime import datetime, timedelta
last_run = datetime.fromisoformat(d['last_run'].split('T')[0])
n = len(bh['Momentum'])
dates = [last_run - timedelta(days=n - 1 - i) for i in range(n)]

prod = pd.DataFrame({
    'date': pd.to_datetime(dates),
    'M': bh['Momentum'], 'S': bh['Stability'], 'R': bh['Rotation'],
    'Sent': bh['Sentiment'], 'Mac': bh['Macro'],
    'regime_prod': regime_log[-n:],
})

btc = pd.read_csv('/home/claude/OracAI/data/btc.csv', parse_dates=['time'])[['time','PriceUSD']].dropna()
btc.columns = ['date', 'price']
prod = prod.merge(btc, on='date', how='left').dropna(subset=['price'])
print(f"Joined {len(prod)} days with BTC prices")
print(f"Period: {prod['date'].min().date()} → {prod['date'].max().date()}")

# ─── Baseline: current LOGIT weights ───
CURRENT = {
    'BULL':  {"M": 1.2, "S": 0.5, "R": -0.4, "Sent": 0.2, "Mac": 0.3},
    'BEAR':  {"M": -1.2, "S": -0.5, "R": 0.4, "Sent": -0.2, "Mac": -0.3},
}
# Range/Transition simplified to constant (they don't use Sentiment much)
RNG_T, TRANS_T = 0.2, 0.2

def softmax(d):
    vals = np.array(list(d.values()))
    vals -= vals.max()
    e = np.exp(vals)
    p = e / e.sum()
    return {k: p[i] for i, k in enumerate(d.keys())}

def compute_probs(row, w_sent_bull=None, w_sent_bear=None, w_mom_bull=None, w_mom_bear=None):
    """Re-compute regime probabilities with modified logit weights."""
    w_bull = dict(CURRENT['BULL'])
    w_bear = dict(CURRENT['BEAR'])
    if w_sent_bull is not None: w_bull['Sent'] = w_sent_bull
    if w_sent_bear is not None: w_bear['Sent'] = w_sent_bear
    if w_mom_bull is not None: w_bull['M'] = w_mom_bull
    if w_mom_bear is not None: w_bear['M'] = w_mom_bear
    
    bull = sum(w_bull[k] * row[n] for k, n in [('M','M'),('S','S'),('R','R'),('Sent','Sent'),('Mac','Mac')])
    bear = sum(w_bear[k] * row[n] for k, n in [('M','M'),('S','S'),('R','R'),('Sent','Sent'),('Mac','Mac')])
    rng = -0.8 * abs(row['M']) + 0.7 * row['S'] - 0.3 * abs(row['R']) - 0.2 * abs(row['Mac'])
    trans = 0.0  # without vol_z history, approximate
    probs = softmax({'BULL': bull, 'BEAR': bear, 'RANGE': rng, 'TRANSITION': trans})
    top_regime = max(probs, key=probs.get)
    return top_regime, probs

def evaluate(variant_name, **kwargs):
    regimes = []
    conf = []
    bull_probs = []
    bear_probs = []
    for _, row in prod.iterrows():
        reg, probs = compute_probs(row, **kwargs)
        regimes.append(reg)
        # gap-based confidence like engine.py
        sorted_p = sorted(probs.values(), reverse=True)
        conf.append(min(0.9, max(0.1, (sorted_p[0] - sorted_p[1]) * 1.5)))
        bull_probs.append(probs['BULL'])
        bear_probs.append(probs['BEAR'])
    
    df = prod.copy()
    df['regime_variant'] = regimes
    df['conf'] = conf
    df['bull_p'] = bull_probs
    df['bear_p'] = bear_probs
    
    # Score: forward 7d return given regime
    df['fwd7_ret'] = (df['price'].shift(-7) / df['price'] - 1) * 100
    df = df.dropna(subset=['fwd7_ret'])
    
    # Regime accuracy (no partial credit)
    bull_acc = df[df['regime_variant']=='BULL']['fwd7_ret'].apply(lambda x: x > 0).mean()
    bear_acc = df[df['regime_variant']=='BEAR']['fwd7_ret'].apply(lambda x: x < 0).mean()
    
    # Distribution
    dist = df['regime_variant'].value_counts().to_dict()
    
    # Directional accuracy weighted by confidence
    df['dir_correct'] = (
        ((df['regime_variant']=='BULL') & (df['fwd7_ret'] > 0)) |
        ((df['regime_variant']=='BEAR') & (df['fwd7_ret'] < 0))
    ).astype(int)
    weighted_acc = (df['dir_correct'] * df['conf']).sum() / df['conf'].sum()
    
    # Economic: if we always rebalanced to target (bullish=90%, bearish=30%, else 60%)
    target_map = {'BULL': 0.9, 'BEAR': 0.3, 'RANGE': 0.6, 'TRANSITION': 0.6}
    df['target'] = df['regime_variant'].map(target_map)
    df['daily_ret'] = df['price'].pct_change()
    df['strat_ret'] = df['target'].shift(1) * df['daily_ret']
    cum_strat = (1 + df['strat_ret'].fillna(0)).prod() - 1
    cum_hodl = df['price'].iloc[-1] / df['price'].iloc[0] - 1
    
    mean_conf = np.mean(conf)
    
    return {
        'variant': variant_name,
        'n_days': len(df),
        'BULL_days': dist.get('BULL', 0),
        'BEAR_days': dist.get('BEAR', 0),
        'RANGE_days': dist.get('RANGE', 0),
        'TRANS_days': dist.get('TRANSITION', 0),
        'BULL_acc%': bull_acc*100 if not np.isnan(bull_acc) else 0,
        'BEAR_acc%': bear_acc*100 if not np.isnan(bear_acc) else 0,
        'weighted_acc%': weighted_acc*100,
        'mean_conf': mean_conf,
        'cum_strat%': cum_strat*100,
        'cum_hodl%': cum_hodl*100,
        'alpha%': (cum_strat - cum_hodl) * 100,
    }

# Sweep
variants = [
    ('current (sent=0.2)', {}),
    ('sent_bull=0.4, bear=-0.4', {'w_sent_bull': 0.4, 'w_sent_bear': -0.4}),
    ('sent_bull=0.6, bear=-0.6', {'w_sent_bull': 0.6, 'w_sent_bear': -0.6}),
    ('sent_bull=0.8, bear=-0.8', {'w_sent_bull': 0.8, 'w_sent_bear': -0.8}),
    ('sent_bull=1.0, bear=-1.0', {'w_sent_bull': 1.0, 'w_sent_bear': -1.0}),
    ('sent=0.8 mom=1.0 (boost sent, lower mom)', {'w_sent_bull': 0.8, 'w_sent_bear': -0.8, 'w_mom_bull': 1.0, 'w_mom_bear': -1.0}),
    ('sent=0.6 mom=0.8', {'w_sent_bull': 0.6, 'w_sent_bear': -0.6, 'w_mom_bull': 0.8, 'w_mom_bear': -0.8}),
    ('contrarian: sent_bull=-0.4', {'w_sent_bull': -0.4, 'w_sent_bear': 0.4}),  # FLIP: fear → bull logit boost
    ('contrarian: sent_bull=-0.8', {'w_sent_bull': -0.8, 'w_sent_bear': 0.8}),
]

rows = [evaluate(name, **kw) for name, kw in variants]
df_out = pd.DataFrame(rows)
pd.set_option('display.width', 200)
pd.set_option('display.max_columns', None)
print("\n" + "="*110)
print(df_out.to_string(index=False, float_format=lambda x: f'{x:.1f}'))
print("="*110)
df_out.to_csv('/home/claude/OracAI/logit_sweep_real_prod.csv', index=False)
print("\n→ saved logit_sweep_real_prod.csv")

# How often does current model disagree with contrarian FG model?
print("\n─── Cases where REAL sentiment was extreme (<-0.5) ───")
extreme = prod[abs(prod['Sent']) > 0.5].copy()
extreme['fwd7'] = extreme['date'].map(
    lambda d: (btc[btc['date'] > d + timedelta(days=6)].iloc[0]['price'] / 
               btc[btc['date'] == d].iloc[0]['price'] - 1) * 100
    if len(btc[btc['date'] > d + timedelta(days=6)]) > 0 else None
)
print(f"N days with |sentiment| > 0.5: {len(extreme)}")
print(f"  mean fwd-7d return: {extreme['fwd7'].dropna().mean():.2f}%")
print(f"  % positive:         {(extreme['fwd7'] > 0).mean()*100:.0f}%")
# Split by sign of sentiment
neg = extreme[extreme['Sent'] < 0]
pos = extreme[extreme['Sent'] > 0]
print(f"\nNegative sentiment (fear): n={len(neg)}, mean fwd7={neg['fwd7'].dropna().mean():.2f}%, %pos={(neg['fwd7']>0).mean()*100:.0f}%")
print(f"Positive sentiment (greed): n={len(pos)}, mean fwd7={pos['fwd7'].dropna().mean():.2f}%, %pos={(pos['fwd7']>0).mean()*100:.0f}%")
