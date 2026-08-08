#!/usr/bin/env python3
"""Generate visualizations for honest backtest: equity curves, DDs, regime timeline."""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import json
import sys
sys.path.insert(0, '/home/claude/OracAI')
from backtest_honest import load_data, run, fetch_real_fg, calculate_rsi, WARMUP, INITIAL_CAPITAL

plt.rcParams['figure.facecolor'] = 'white'
plt.rcParams['axes.facecolor'] = 'white'
plt.rcParams['font.family'] = 'DejaVu Sans'

df = load_data()
fg_real = fetch_real_fg()
full, eq, sig = run(df, fg_real, "full_5y")

eq = eq.set_index('date')
sig = sig.set_index('date')

# HODL equity
hodl_eq = INITIAL_CAPITAL * (eq['price'] / eq['price'].iloc[0])
# Fixed 90/10 equity  
fixed90_btc = INITIAL_CAPITAL * 0.9 / eq['price'].iloc[0]
fixed90_cash = INITIAL_CAPITAL * 0.1
fixed90_eq = fixed90_cash + fixed90_btc * eq['price']

# ═══════════════════════════════════════════════════════════════════
# Figure 1: Equity curves + drawdowns
# ═══════════════════════════════════════════════════════════════════
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True, 
                                gridspec_kw={'height_ratios': [2, 1]})

ax1.plot(hodl_eq.index, hodl_eq, label='HODL 100%', color='#2E86AB', lw=2, alpha=0.9)
ax1.plot(fixed90_eq.index, fixed90_eq, label='Fixed 90/10', color='#A23B72', lw=1.5, alpha=0.8, linestyle='--')
ax1.plot(eq.index, eq['equity'], label='OracAI v5.8 model', color='#E63946', lw=2)
ax1.set_ylabel('Equity, $', fontsize=11)
ax1.set_title(f'Equity curves — $100k initial, 2020-12 → 2026-02  (10 bps fees)', fontsize=13, pad=10)
ax1.legend(loc='upper left', fontsize=10, framealpha=0.95)
ax1.grid(alpha=0.3)
ax1.set_yscale('log')
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'${x/1000:.0f}k'))

# Drawdown
peak_model = eq['equity'].cummax()
dd_model = (eq['equity'] - peak_model) / peak_model * 100
peak_hodl = hodl_eq.cummax()
dd_hodl = (hodl_eq - peak_hodl) / peak_hodl * 100

ax2.fill_between(dd_hodl.index, dd_hodl, 0, alpha=0.3, color='#2E86AB', label=f'HODL DD (max {dd_hodl.min():.0f}%)')
ax2.fill_between(dd_model.index, dd_model, 0, alpha=0.45, color='#E63946', label=f'Model DD (max {dd_model.min():.0f}%)')
ax2.set_ylabel('Drawdown, %', fontsize=11)
ax2.set_title('Drawdown from peak', fontsize=11, pad=6)
ax2.legend(loc='lower left', fontsize=10)
ax2.grid(alpha=0.3)
ax2.axhline(0, color='black', lw=0.5)

# Mark OOS split
oos_start = df.index[int(len(df) * 0.6)]
for ax in (ax1, ax2):
    ax.axvline(oos_start, color='gray', linestyle=':', alpha=0.7)
ax1.text(oos_start, ax1.get_ylim()[1] * 0.95, ' OOS split →', fontsize=9, color='gray', va='top')

plt.tight_layout()
plt.savefig('/home/claude/OracAI/chart_equity_dd.png', dpi=140, bbox_inches='tight')
plt.close()
print('Saved chart_equity_dd.png')

# ═══════════════════════════════════════════════════════════════════
# Figure 2: Regime timeline + position + crashes missed
# ═══════════════════════════════════════════════════════════════════
fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True,
                         gridspec_kw={'height_ratios': [2, 1, 1]})

ax = axes[0]
ax.plot(sig.index, sig['price'], color='black', lw=1.2, label='BTC price')
ax.set_yscale('log')
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'${x/1000:.0f}k'))

# Overlay regime as colored background
colors = {'BULL': '#90be6d', 'BEAR': '#e63946', 'RANGE': '#f9c74f', 'TRANSITION': '#577590'}
for regime, color in colors.items():
    mask = sig['regime'] == regime
    if mask.any():
        # Draw thin dots at the top for each day's call
        y_top = ax.get_ylim()[1] * 0.92
        ax.scatter(sig.index[mask], [y_top] * mask.sum(), s=2, color=color, alpha=0.5,
                   label=f'{regime} ({mask.sum()}d, {mask.mean()*100:.0f}%)')

ax.set_ylabel('BTC, $', fontsize=11)
ax.set_title('Regime calls over time (top band)', fontsize=12, pad=8)
ax.legend(loc='upper left', ncol=5, fontsize=9)
ax.grid(alpha=0.25)

# Position size
ax = axes[1]
ax.fill_between(sig.index, 0, sig['cur_pos'] * 100, alpha=0.5, color='#4a90e2')
ax.plot(sig.index, sig['target'] * 100, color='#e63946', lw=1, alpha=0.8, label='Target')
ax.set_ylabel('BTC position, %', fontsize=11)
ax.set_ylim(0, 105)
ax.legend(loc='lower left', fontsize=9)
ax.grid(alpha=0.25)
ax.set_title('Position size over time', fontsize=11, pad=6)

# Crash detection
ax = axes[2]
ax.plot(sig.index, sig['price'], color='gray', lw=0.8, alpha=0.4)
ax.set_yscale('log')

# Mark crashes (−10% in 14d)
prices = sig['price'].values
crash_mask = np.zeros(len(sig), dtype=bool)
for i in range(len(sig) - 14):
    if (prices[i + 14] / prices[i] - 1) * 100 < -10:
        crash_mask[i] = True

# TAIL/CRISIS warnings
warn_mask = sig['risk'].isin(['TAIL', 'CRISIS']).values

ax.scatter(sig.index[crash_mask], sig.loc[crash_mask, 'price'], color='#e63946',
           s=10, alpha=0.6, label=f'Actual crash start (−10% in 14d)   n={crash_mask.sum()}')
crisis_mask = (sig['risk'] == 'CRISIS').values
ax.scatter(sig.index[crisis_mask], sig.loc[crisis_mask, 'price'] * 1.15,
           marker='v', color='#8b0000', s=40, alpha=0.8,
           label=f'CRISIS signal   n={crisis_mask.sum()}')

ax.set_ylabel('BTC, $', fontsize=11)
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'${x/1000:.0f}k'))
ax.set_title('Crash detection — how often did the model warn?', fontsize=11, pad=6)
ax.legend(loc='upper left', fontsize=9)
ax.grid(alpha=0.25)

plt.tight_layout()
plt.savefig('/home/claude/OracAI/chart_regime_timeline.png', dpi=140, bbox_inches='tight')
plt.close()
print('Saved chart_regime_timeline.png')

# ═══════════════════════════════════════════════════════════════════
# Figure 3: Regime distribution + confusion with price moves
# ═══════════════════════════════════════════════════════════════════
fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))

# 3a: regime frequency
ax = axes[0]
regime_counts = sig['regime'].value_counts()
bars = ax.bar(regime_counts.index, regime_counts.values, 
              color=[colors.get(r, 'gray') for r in regime_counts.index])
ax.set_title('Regime call distribution (1833 days)', fontsize=11)
ax.set_ylabel('Days')
for bar, val in zip(bars, regime_counts.values):
    ax.text(bar.get_x() + bar.get_width()/2, val + 5, f'{val}\n{val/len(sig)*100:.0f}%',
            ha='center', fontsize=9)

# 3b: Confidence distribution
ax = axes[1]
ax.hist(sig['conf'], bins=30, color='#4a90e2', alpha=0.7, edgecolor='white')
ax.axvline(sig['conf'].mean(), color='red', linestyle='--', label=f"Mean: {sig['conf'].mean():.2f}")
ax.set_xlabel('Confidence')
ax.set_ylabel('Days')
ax.set_title('Regime confidence distribution', fontsize=11)
ax.legend()

# 3c: Actual 7d return given regime
ax = axes[2]
future_ret = sig['price'].pct_change(7).shift(-7) * 100
ret_by_regime = [future_ret[sig['regime'] == r].dropna() for r in ['BULL', 'BEAR', 'RANGE', 'TRANSITION']]
box = ax.boxplot(ret_by_regime, labels=['BULL', 'BEAR', 'RANGE', 'TRANS'], patch_artist=True,
                 showfliers=False, widths=0.6)
for patch, regime in zip(box['boxes'], ['BULL', 'BEAR', 'RANGE', 'TRANSITION']):
    patch.set_facecolor(colors[regime])
    patch.set_alpha(0.7)
ax.axhline(0, color='black', lw=0.5)
ax.set_ylabel('Next-7d BTC return, %')
ax.set_title('Price moves AFTER each regime call', fontsize=11)
ax.grid(alpha=0.3, axis='y')

plt.tight_layout()
plt.savefig('/home/claude/OracAI/chart_regime_stats.png', dpi=140, bbox_inches='tight')
plt.close()
print('Saved chart_regime_stats.png')

# ═══════════════════════════════════════════════════════════════════
# Figure 4: Signal quality heatmap
# ═══════════════════════════════════════════════════════════════════
fig, axes = plt.subplots(1, 2, figsize=(13, 5))

# 4a: Bottom accuracy (forward-only, as different thresholds)
ax = axes[0]
thresholds = np.arange(0.3, 0.91, 0.05)
future_windows = [7, 14, 30, 60]
heat = np.zeros((len(future_windows), len(thresholds)))
for i, w in enumerate(future_windows):
    future_min = sig['price'].rolling(w, min_periods=1).min().shift(-w + 1)
    for j, t in enumerate(thresholds):
        mask = sig['bottom'] > t
        if mask.sum() > 5:
            hits = (sig.loc[mask, 'price'] <= future_min[mask] * 1.1).sum()
            heat[i, j] = hits / mask.sum() * 100
        else:
            heat[i, j] = np.nan

im = ax.imshow(heat, aspect='auto', cmap='RdYlGn', vmin=30, vmax=80,
               extent=[thresholds[0], thresholds[-1], future_windows[-1] + 15, future_windows[0] - 3])
ax.set_yticks(future_windows)
ax.set_xticks(np.arange(0.3, 0.91, 0.1))
ax.set_xlabel('Bottom signal threshold')
ax.set_ylabel('Forward window, days')
ax.set_title('Bottom accuracy — % signals within 10% of next-N-day low', fontsize=11)
for i, w in enumerate(future_windows):
    for j, t in enumerate(thresholds):
        v = heat[i, j]
        if not np.isnan(v):
            ax.text(t, w, f'{v:.0f}', ha='center', va='center', fontsize=7,
                    color='white' if v < 50 else 'black')
fig.colorbar(im, ax=ax, label='Accuracy %')

# 4b: Top accuracy
ax = axes[1]
heat2 = np.zeros((len(future_windows), len(thresholds)))
for i, w in enumerate(future_windows):
    future_max = sig['price'].rolling(w, min_periods=1).max().shift(-w + 1)
    for j, t in enumerate(thresholds):
        mask = sig['top'] > t
        if mask.sum() > 5:
            hits = (sig.loc[mask, 'price'] >= future_max[mask] * 0.9).sum()
            heat2[i, j] = hits / mask.sum() * 100
        else:
            heat2[i, j] = np.nan

im = ax.imshow(heat2, aspect='auto', cmap='RdYlGn', vmin=30, vmax=80,
               extent=[thresholds[0], thresholds[-1], future_windows[-1] + 15, future_windows[0] - 3])
ax.set_yticks(future_windows)
ax.set_xticks(np.arange(0.3, 0.91, 0.1))
ax.set_xlabel('Top signal threshold')
ax.set_ylabel('Forward window, days')
ax.set_title('Top accuracy — % signals within 10% of next-N-day high', fontsize=11)
for i, w in enumerate(future_windows):
    for j, t in enumerate(thresholds):
        v = heat2[i, j]
        if not np.isnan(v):
            ax.text(t, w, f'{v:.0f}', ha='center', va='center', fontsize=7,
                    color='white' if v < 50 else 'black')
fig.colorbar(im, ax=ax, label='Accuracy %')

plt.tight_layout()
plt.savefig('/home/claude/OracAI/chart_bottom_top.png', dpi=140, bbox_inches='tight')
plt.close()
print('Saved chart_bottom_top.png')

# ═══════════════════════════════════════════════════════════════════
# Summary stats for report
# ═══════════════════════════════════════════════════════════════════
summary = {
    'days': len(sig),
    'regime_dist': sig['regime'].value_counts().to_dict(),
    'risk_dist': sig['risk'].value_counts().to_dict(),
    'avg_position': float(sig['cur_pos'].mean()),
    'avg_confidence': float(sig['conf'].mean()),
    'n_trades': full['n_trades'],
    'total_fees': full['total_fees'],
}
print(json.dumps(summary, indent=2))
