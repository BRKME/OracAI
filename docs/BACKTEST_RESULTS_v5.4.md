# Backtest Results v5.4

## Summary

**Period:** 3 years (Jan 2023 — Mar 2026)
**Data:** 1095 days, 76 trades

## Performance

| Metric | Model | HODL | Diff |
|--------|-------|------|------|
| Return | +23.7% | +131.2% | -107.5% |
| Sharpe | 0.49 | — | — |
| Max DD | -22.1% | ~-50% | +28% |

**Conclusion:** HODL outperforms active trading in bull market.

## Regime Accuracy

| Regime | Accuracy | Status |
|--------|----------|--------|
| Overall | 60.1% | ✅ >55% |
| BULL | 59.0% | ✅ |
| BEAR | 49.6% | ⚠️ Near random |

## Signal Quality

| Signal | Win Rate | Status |
|--------|----------|--------|
| BUY (+5% in 7d) | 20.7% | ❌ Poor |
| SELL (-5% in 7d) | 13.6% | ❌ Poor |

**Conclusion:** Active signals are not profitable.

## Timing Accuracy

| Metric | Accuracy | Status |
|--------|----------|--------|
| Bottom detection | 65.0% | ✅ Useful |
| Top detection | 74.4% | ✅ Good |

**Conclusion:** Model is good at detecting extremes.

## Risk Warnings

| Metric | Value | Status |
|--------|-------|--------|
| TAIL before crash | 55% | ⚠️ |
| Crashes missed | 45% | ⚠️ |

**Conclusion:** Risk warnings need improvement.

## Model Strengths

1. ✅ Bottom timing (65%)
2. ✅ Top timing (74%)
3. ✅ Regime detection (60%)
4. ✅ Lower drawdown vs HODL

## Model Weaknesses

1. ❌ Active trading loses to HODL
2. ❌ Low BUY win rate (21%)
3. ❌ Missing 45% of crashes
4. ❌ BEAR accuracy near random

## Recommendations

### DO USE for:
- Risk management (exposure %)
- Bottom/Top detection
- Crisis warnings

### DO NOT USE for:
- Active trading signals
- Frequent BUY/SELL decisions
- Short-term timing

## Strategy v5.4

Based on backtest:

1. **Default HOLD** — Don't trade actively
2. **Reduce exposure** in TAIL/CRISIS
3. **BUY only** at extreme bottoms (RSI<25)
4. **Trust Bottom/Top** signals more than regime
