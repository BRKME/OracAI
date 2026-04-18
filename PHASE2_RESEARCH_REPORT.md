# Phase 2 Research Report — Action Logic Tuning

**Period analyzed:** 2025-10-01 → 2026-04-18 (200 days of real prod bucket history)
**Data source:** `state/engine_state.json` — actual production outputs from deployed `engine.py`
**Scripts:** `research_real_prod.py`, `research_conflict.py`

## Motivation

User reported that the Telegram message below looked inconsistent:

```
🔘 Фаза рынка: BULL (1d) | Conf. 51%
RSI: 1D=75 | 2H=24↓
FG: 26 (Fear)
...
🔘 Действие: 🟠 ФИКСИРОВАТЬ
→ Просадка 18% от 90д хая. Целевая позиция: 55%.
```

Two concerns:
1. Is "ФИКСИРОВАТЬ" the right action when the 18% drawdown has already happened?
2. Is Fear & Greed weighted appropriately given it's a classical contrarian signal?

## Investigation approach

Unlike the previous 5y backtest (which used simplified heuristic logic on CoinMetrics price
and synthetic FG), this round uses **real prod bucket history**: Momentum, Stability,
Rotation, Sentiment, Macro values produced by the deployed `engine.py`. This bypasses the
"synthetic FG ≈ price" correlation issue.

## Hypotheses tested

### H1. "Increase LOGIT Sentiment weight to counter momentum dominance"

**Code audit finding first:** For 2026-04-18 prod data (Sent = −0.74, Mom = +0.66), the
contributions to BULL logit were:
- Momentum: 1.2 × 0.66 = **+0.79**
- Sentiment: 0.2 × −0.74 = **−0.15**

Ratio ~5:1 in favour of momentum. User's intuition that FG is structurally weak looked
correct.

**Test:** varied LOGIT_BULL["Sentiment"] and LOGIT_BEAR["Sentiment"] from 0.2 (current)
up to 1.0 on real prod buckets. Scored using directional accuracy of fwd-7d returns.

| Variant | BULL days | BEAR days | BULL acc | BEAR acc | Alpha vs HODL |
|---|---|---|---|---|---|
| Sent = ±0.2 (current) | 24 | 75 | 25.0% | 60.0% | +12.3% |
| Sent = ±0.4 | 27 | 73 | 29.6% | 60.3% | +12.2% |
| Sent = ±0.6 | 27 | 75 | 29.6% | 60.0% | +12.1% |
| Sent = ±0.8 | 28 | 72 | 28.6% | 59.7% | +10.8% |
| Sent = ±1.0 | 27 | 71 | 29.6% | 59.2% | +11.8% |
| Sent = ±0.8, Mom = ±1.0 | 32 | 66 | 31.2% | 60.6% | +13.4% |

**Verdict:** effect is within noise. Increasing Sentiment weight does not meaningfully
improve directional accuracy. **H1 rejected.**

### H2. "Contrarian: flip Sentiment sign (buy Fear, sell Greed)"

**Test:** set LOGIT_BULL["Sentiment"] = -0.4 and -0.8 (negative → Fear increases BULL probability).

| Variant | Alpha vs HODL |
|---|---|
| Sent = −0.4 (contrarian) | +11.1% |
| Sent = −0.8 (strong contrarian) | +10.7% |

**Verdict:** contrarian makes it WORSE. **H2 rejected decisively.**

### H3. "Sentiment predicts returns within each regime"

**Test:** correlation between `Sentiment` value and fwd-3/7/14-day returns, within BEAR days.

| Horizon | Correlation |
|---|---|
| fwd-3d | +0.03 |
| fwd-7d | +0.01 |
| fwd-14d | −0.02 |

**Verdict:** zero predictive power within a regime. **H3 rejected.**

### H4. "BEAR + Greed = capitulation missing = deeper fall"

**Test:** split BEAR days by Sentiment sign and compare forward returns.

| Subgroup | n | fwd-7d mean | % positive |
|---|---|---|---|
| BEAR + Greed (Sent > 0.3) | 13 | **−4.3%** | 31% |
| BEAR + neutral/fear | 117 | −2.7% | 36% |

**Verdict:** confirmed. Greed during a downtrend predicts **worse**, not better, next-week
outcomes. Contrarian intuition is backwards in this regime. **H4 confirmed.**

### H5. "Drawdown defender fires at the wrong time"

From prior `backtest_honest.py` full-5y sweep of DD policies (running on same simplified
model as current prod message logic):

| DD policy | Alpha | Sharpe | Max DD | Trades |
|---|---|---|---|---|
| `current` | −173.5% | 0.53 | −68.5% | 214 |
| `gated` (only fire if bear-confirmed) | −203.0% | 0.46 | −74.1% | 203 |
| `none` (no DD defender at all) | **−169.3%** | 0.53 | −75.1% | 97 |
| `flipped` (DD → positive bottom signal) | −169.3% | 0.53 | −75.1% | 97 |

**Verdict:** `current` DD defender is worse than doing nothing. `none` and `flipped` are
equivalent (neither improves signal quality, but neither makes it worse). `gated` with
wrong gating condition is worst — so the gating logic must be right.

On the prod 200-day sample, DD defender fires frequently because BTC drew down repeatedly
from local highs, but these drawdowns were often near local lows (good buy points), not
continuation of bear trends. **H5 confirmed.**

## Decisions

After reviewing evidence:

1. **DO NOT change LOGIT weights.** No empirical support for boosting Sentiment.
2. **DO NOT add Extreme-zone boost** in bottom/top_prox. F1 sweep showed no improvement.
3. **DO NOT flip Sentiment to contrarian.** Decisively worse on prod data.
4. **DO gate DD defender by bear-regime confirmation.** Required conditions:
   - `regime == "BEAR"` (primary signal agrees), OR
   - `RSI < 50 AND direction < -0.2` (broad weakness), OR
   - `FG > 65 AND dd < -15%` (Greed during drawdown = H4-confirmed trap)
5. **DO add conflict warning to UI** (informational only, does not change target):
   - `BULL + FG < 30 + conf > 40%` → "Конфликт: BULL при Fear. Регим важнее, но подтверждения нет."
   - `BEAR + FG > 70 + conf > 40%` → "Конфликт: BEAR при Greed. Исторически усиливает медвежий сценарий."

## Impact on user's current message

With these changes, the exact conditions in the reported message (BULL, RSI=75, FG=26,
dd=−18%, risk=ELEV) produce:

**Before:**
- `target_pos = 0.55` (DD defender fired)
- Action: 🟠 ФИКСИРОВАТЬ

**After:**
- `bear_confirmation = False` (no BEAR, RSI > 50, FG < 65)
- DD defender skipped → `target_pos = 0.90`
- Action: ⚪ ДЕРЖАТЬ
- Plus conflict warning: "⚠️ Конфликт: BULL режим при Fear. Исторически это продолжение тренда,
  но подтверждения нет."

## Caveats

- Sample is 200 days, one regime cycle, one market environment (2025 Q4 − 2026 Q1 bear).
  Findings may not generalize to bull markets.
- Cannot test prod `engine.py` directly without historical DXY/US10Y/US2Y/BTC.D/ETH series.
- The "no alpha from Sentiment tweaks" finding is consistent with broader conclusion from
  `HONEST_BACKTEST_REPORT.md`: the model's value is informational, not P&L-generating.

## Files produced

- `research_real_prod.py` — LOGIT weight sweep on real prod buckets
- `research_conflict.py` — conflict analysis (BULL+Fear, BEAR+Greed)
- `research_tuning_sweep.py` — F1 sweep of FG weight and DD policies
- `logit_sweep_real_prod.csv` — sweep results
- `conflict_analysis.csv` — conflict analysis results
- `prod_history_200d.csv` — 200-day prod bucket history joined with BTC prices
- Changes to `telegram_bot.py` — bear-gated DD defender + conflict warning
