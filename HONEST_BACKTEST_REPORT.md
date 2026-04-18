# OracAI v5.8 — 5Y Honest Backtest Report

**Period:** 2020-12-20 → 2026-02-14 (1 883 days)
**Data:** CoinMetrics daily BTC (`data/btc.csv`, PriceUSD column)
**Methodology:** same simplified regime logic as `backtest_v5.py`, with bugs fixed, transaction costs (10 bps), walk-forward split (60/40 IS/OOS), bootstrap confidence intervals on alpha.
**Script:** `backtest_honest.py`

## Headline numbers

| Metric | Model | HODL | Fixed 90/10 | 60/40 |
|---|---|---|---|---|
| Cumulative return | +186.2% | **+275.8%** | +248.2% | +191.5% |
| Sharpe ratio | 0.47 | **0.73** | 0.71 | 0.77 |
| Max drawdown | **−66.5%** | −76.7% | −74.4% | −55.1% |

Alpha (cumulative) vs HODL: **−89.6 pp**
Alpha (annualized): **−16.56 %** per year
Bootstrap 95% CI: **[−33.0 %, +4.3 %]**
t-statistic on daily active returns: **−1.68** (p ≈ 0.093)
Information ratio: **−0.74**

Fees paid on 210 trades: **$19 490** — roughly 1.9 pp of annual drag from transaction costs alone.

## The one upside — walk-forward OOS

On the out-of-sample 2024-03-11 → 2026-02-14:

| | Model | HODL |
|---|---|---|
| Return | +41.8% | −3.3% |
| Max DD | −46.5% | −49.1% |

Cumulative alpha **+45 pp** looks impressive, but:
- Daily active-return t-stat: −1.09 (p ≈ 0.28) — not significant
- Information ratio: −0.78
- The OOS outperformance is driven by one path-dependent episode (the Feb 2026 crash) where the drawdown defender reduced position

This is not evidence of a systematic edge; it's one good moment.

## Signal quality

| Signal | Score | Notes |
|---|---|---|
| Overall regime accuracy | 55.6% | Naive "always-up" baseline: 51.5%. Edge: 4 pp. |
| BULL accuracy | 55.2% | BTC was in an uptrend, so this is close to base rate. |
| BEAR accuracy | 44.8% | Worse than coin-flip. |
| BUY signal 7d win rate | 26.7% | Random BUY in an uptrending market averages ~50%. |
| SELL signal 7d win rate | 19.0% | Same — below random. |
| Crash detection (−10% in 14d) | 14.3% | 35 of 245 crashes warned in advance. |
| CRISIS false-positive rate | 98.1% | Of 52 CRISIS signals, only 1 preceded an actual crash. |
| Bottom accuracy (fwd-only, 30d) | 65.9% | Previously reported 85% used ±15d window (look-ahead). |
| Top accuracy (fwd-only, 30d) | 44.8% | Previously reported 67% used ±15d window. |

Most damning: when BULL, BEAR, RANGE, or TRANSITION is called, the **distribution of next-7-day BTC returns is almost identical**. Median next-7d return after BULL call: +1%. After BEAR call: +0.5%. The regime label carries near-zero information about future prices.

## Bugs identified in `backtest_v5.py`

### 1. Backtest tests simplified model, not `engine.py`

`backtest_v5.py:detect_regime()` uses heuristic rules on RSI + moving averages + synthetic F&G. The production `engine.py` uses softmax over logits of 5 normalized buckets (Momentum, Stability, Rotation, Sentiment, Macro) with adaptive temperature, EMA smoothing, asymmetric switching, and gap-based confidence. **These are different models.** Any performance claim based on `backtest_v5.py` does not transfer to what runs in production.

### 2. Look-ahead bias in bottom/top accuracy

```python
# backtest_v5.py line 632
window_start = max(0, i - 15)
window_end = min(len(prices), i + 15)   # peeks 15d into future
```

The model signal at day `i` is scored against a window that extends 15 days after `i`. This is classical look-ahead. Forward-only scoring (next 30d) drops bottom accuracy from 85% → 65.9%, top from 67% → 44.8%.

### 3. `crisis_false_positive` formula is broken

```python
# backtest_v5.py line 620
crisis_false_pos = (crisis_count - len(crashes)) / crisis_count * 100
```

This is neither precision nor FP rate — it's a difference divided by a count, which produces −2000% and −714% values seen in `backtest_5y_results.txt`. Correct definition: fraction of CRISIS signals NOT followed by a crash within 14d. Recomputed: **98.1%**.

### 4. TRANSITION partial credit (0.5) in regime accuracy

```python
# backtest_v5.py line 568
else:  # TRANSITION
    correct_calls += 0.5  # Partial credit
```

A strategy that always predicts TRANSITION would score 50% accuracy with this rule. Without partial credit: 55.6%.

### 5. No transaction costs, no slippage

The existing backtest treats trades as free. At 10 bps per trade × 210 trades over 5 years, that's **$19.5k** (~1.9 pp annual drag on $100k).

### 6. Hindsight-calibrated thresholds

Thresholds like `rsi > 78 AND top_prox > 0.70 AND conf > 0.20` appear post-hoc fitted. There is no parameter freeze between in-sample and out-of-sample. The walk-forward split shows crash detection collapsing from 18.9% (IS) to 1.5% (OOS) — a classic overfitting signature.

### 7. Default 90% invested ≈ HODL

Model is structurally long-biased: target defaults to 90% and only drops on extreme confluences that rarely trigger (average confidence is 0.056 in this backtest, below the 0.20 threshold that most reduction rules require). As a result, model equity ≈ 0.9 × HODL equity most of the time, which explains why Sharpe is always ~0.9× HODL Sharpe and true alpha is hard to achieve.

## What this report is not

- Not a test of the production `engine.py`. Without historical BTC.D, DXY, US10Y, US2Y, ETH, realized high/low, and BTC dominance, the prod model cannot be run. This requires importing historical series (yfinance + FRED + CoinGecko) and feeding them through `data_pipeline.py` in historical mode. That's a separate piece of work.
- Not statistically conclusive against the model's ability to provide informational value as a dashboard. It only rejects its value as a returns-generating strategy at current parameter settings.

## Recommendations (ordered by leverage)

1. **Backtest the actual production engine.** Reconstruct historical buckets. Without this, every existing result in the repo refers to a different model than is deployed.
2. **Decide the model's purpose.** If it's a dashboard — evaluate via calibration (Brier score on probabilities) and lead time, not return-beats-HODL. If it's an alpha engine — current IR −0.74 says re-architect.
3. **Apply the bug fixes listed above** and re-run existing backtests; correct the numbers in README.
4. **Freeze parameters between IS and OOS.** Current thresholds look calibrated to the full sample.
5. **Reduce trading activity.** 210 trades over 5 years is churn, not position management. Consider making the rebalance delta 0.30 or larger, and removing actions that don't meaningfully change exposure.
6. **Stop comparing the model only to HODL 100%.** Include Fixed 90/10 and 60/40 rebalanced — both beat the model over 5 years with less complexity.

## Files produced

- `backtest_honest.py` — the fixed backtest script with walk-forward, baselines, bootstrap CIs
- `honest_backtest_results.json` — full numerical results
- `chart_equity_dd.png` — equity curves and drawdowns
- `chart_regime_timeline.png` — regime calls, position, and crash-detection timeline
- `chart_regime_stats.png` — regime distribution, confidence distribution, next-7d returns by regime
- `chart_bottom_top.png` — bottom/top accuracy heatmap vs threshold × forward window
