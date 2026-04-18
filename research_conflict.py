#!/usr/bin/env python3
"""
Surgical analysis: the user asked specifically about BULL + Fear conflict.
Let's find every such day in prod history and measure what happened next.
"""
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

d = json.load(open('/home/claude/OracAI/state/engine_state.json'))
bh = d['bucket_history']
regime_log = d['regime_log']
last_run = datetime.fromisoformat(d['last_run'].split('T')[0])
n = len(bh['Momentum'])
dates = [last_run - timedelta(days=n - 1 - i) for i in range(n)]

prod = pd.DataFrame({
    'date': pd.to_datetime(dates),
    'M': bh['Momentum'], 'S': bh['Stability'], 'R': bh['Rotation'],
    'Sent': bh['Sentiment'], 'Mac': bh['Macro'],
    'regime': regime_log[-n:],
})

btc = pd.read_csv('/home/claude/OracAI/data/btc.csv', parse_dates=['time'])[['time', 'PriceUSD']].dropna()
btc.columns = ['date', 'price']
prod = prod.merge(btc, on='date', how='left')

# Forward returns at multiple horizons
for h in [3, 7, 14, 30]:
    prod[f'fwd{h}d'] = (prod['price'].shift(-h) / prod['price'] - 1) * 100

# Identify conflicts: BULL + Sentiment in fear zone
prod['conflict_BULL_fear'] = (prod['regime'] == 'BULL') & (prod['Sent'] < -0.3)
prod['conflict_BEAR_greed'] = (prod['regime'] == 'BEAR') & (prod['Sent'] > 0.3)
prod['clean_BULL'] = (prod['regime'] == 'BULL') & (prod['Sent'] >= -0.3)
prod['clean_BEAR'] = (prod['regime'] == 'BEAR') & (prod['Sent'] <= 0.3)

print("="*80)
print("  CONFLICT ANALYSIS ON REAL PROD DATA")
print(f"  Period: {prod['date'].min().date()} → {prod['date'].max().date()}")
print("="*80)

def summarize(label, mask):
    sub = prod[mask & prod['fwd7d'].notna()]
    if len(sub) == 0:
        print(f"{label:40s} no data")
        return
    print(f"{label:40s} n={len(sub):3d}  "
          f"fwd3d={sub['fwd3d'].mean():+5.1f}%  "
          f"fwd7d={sub['fwd7d'].mean():+5.1f}%  "
          f"fwd14d={sub['fwd14d'].mean():+5.1f}%  "
          f"fwd30d={sub['fwd30d'].mean():+5.1f}%  "
          f"%pos7d={(sub['fwd7d'] > 0).mean()*100:.0f}%")

print("\n▍ALL regimes (baseline):")
summarize("  BULL (any sentiment)", prod['regime'] == 'BULL')
summarize("  BEAR (any sentiment)", prod['regime'] == 'BEAR')
summarize("  RANGE", prod['regime'] == 'RANGE')

print("\n▍CONFLICT — BULL with Fear (Sent < -0.3):")
summarize("  BULL + Fear (conflict)", prod['conflict_BULL_fear'])
summarize("  BULL + no conflict", prod['clean_BULL'])

print("\n▍CONFLICT — BEAR with Greed (Sent > 0.3):")
summarize("  BEAR + Greed (conflict)", prod['conflict_BEAR_greed'])
summarize("  BEAR + no conflict", prod['clean_BEAR'])

# Sharper: Sent < -0.5 (stronger fear)
print("\n▍STRONGER CONFLICT — BULL with Sent<-0.5:")
summarize("  BULL + Strong Fear", (prod['regime'] == 'BULL') & (prod['Sent'] < -0.5))
summarize("  BULL + Strong Fear (Sent<-0.7)", (prod['regime'] == 'BULL') & (prod['Sent'] < -0.7))

# Is sentiment predictive WITHIN a regime?
print("\n▍Does sentiment predict next returns WITHIN each regime?")
# Correlation of Sentiment vs fwd7d
for reg in ['BULL', 'BEAR']:
    sub = prod[(prod['regime'] == reg) & prod['fwd7d'].notna()]
    if len(sub) >= 10:
        corr_3 = sub['Sent'].corr(sub['fwd3d'])
        corr_7 = sub['Sent'].corr(sub['fwd7d'])
        corr_14 = sub['Sent'].corr(sub['fwd14d'])
        print(f"  {reg}: n={len(sub)}  corr(Sent, fwd3d)={corr_3:+.2f}  fwd7d={corr_7:+.2f}  fwd14d={corr_14:+.2f}")

# Does Stability tell us anything? (It was 0.16 in today's message — dropping)
print("\n▍Does low Stability (S<0.3) + BULL predict badly?")
summarize("  BULL + high S(>0.7)", (prod['regime']=='BULL') & (prod['S']>0.7))
summarize("  BULL + mid S(0.3-0.7)", (prod['regime']=='BULL') & (prod['S'].between(0.3,0.7)))
summarize("  BULL + low S(<0.3)", (prod['regime']=='BULL') & (prod['S']<0.3))

# Final killer chart: regression Sentiment → fwd7d in BULL days
print("\n▍Linear regression: fwd7d = a + b*Sentiment, only BULL days")
bull_sub = prod[(prod['regime'] == 'BULL') & prod['fwd7d'].notna()]
if len(bull_sub) >= 15:
    x = bull_sub['Sent'].values
    y = bull_sub['fwd7d'].values
    # Simple OLS
    b1 = np.cov(x, y, ddof=1)[0,1] / np.var(x, ddof=1)
    b0 = y.mean() - b1 * x.mean()
    resid = y - (b0 + b1 * x)
    se = np.sqrt((resid**2).sum() / (len(y) - 2)) / np.sqrt(((x - x.mean())**2).sum())
    t = b1 / se if se > 0 else 0
    print(f"  slope b={b1:+.2f} (%per 1.0 sentiment unit), intercept={b0:+.2f}%, t={t:+.2f}, n={len(bull_sub)}")
    print(f"  interpretation: in BULL days, a 1.0 swing in Sentiment moves fwd7d by {b1:+.2f}%")

prod.to_csv('/home/claude/OracAI/conflict_analysis.csv', index=False)
print("\n→ saved conflict_analysis.csv")
