# Backtest Results v5.6

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

## Strategy v5.6

Based on backtest, integrated logic:

### Action Logic

| Priority | Condition | Action |
|----------|-----------|--------|
| 1 | CRISIS | ⚫ ЗАЩИТА |
| 2 | Bottom ≥70% | 🟢 ПОКУПАТЬ |
| 3 | Top ≥70% | 🔴 ПРОДАВАТЬ |
| 4 | Bottom ≥50% | 🟡 ДОКУПИТЬ |
| 5 | Top ≥50% | 🟠 ФИКСИРОВАТЬ |
| 6 | EARLY_BULL + bottom≥30% | 🟡 ДОКУПИТЬ |
| 7 | LATE_BULL + top≥30% | 🟠 ФИКСИРОВАТЬ |
| 8 | Default | ⚪ ДЕРЖАТЬ |

### Hedge Logic

| Risk | Confidence | Hedge |
|------|------------|-------|
| CRISIS/TAIL | Any | REQUIRED |
| ELEVATED | <30% | REQUIRED |
| ELEVATED | ≥30% | recommended |
| NORMAL | Any | optional |

### LP Exposure Modifier

| Condition | Modifier |
|-----------|----------|
| BULL + conf>30% | +20% |
| BEAR | -20% |
| CRISIS | max 10% |
| TAIL | max 30% |

## Recommendations

### DO USE for:
- Position sizing (exposure %)
- Bottom/Top detection
- Risk management (hedge decisions)
- Phase-aware entry/exit

### DO NOT USE for:
- Frequent trading signals
- Short-term timing
- BEAR market predictions
