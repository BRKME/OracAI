# Engine Real Backtest Report — Phase 3 Final

**Period:** 2021-07-19 → 2026-04-19 (1736 days, 4.8 years)
**Engine version:** 3.3
**Data:** `data/external/*.csv` — real 5y historical from CryptoCompare / CMC / FRED / CoinGecko
**Script:** `backtest_engine_real.py`

---

## TL;DR

The production `engine.py` was driven day-by-day across 5 years of real
market data using its own softmax + EMA + asymmetric switching + gap-based
confidence pipeline. Bottom line:

> **Over 5 years, the engine-driven strategy returned +78.8% vs HODL's +145.0%
> — an alpha of −66.2 percentage points.**
>
> **Simple SMA 50/200 crossover returned +152.6% — beating the engine by 74 pp
> and also beating HODL by 7.5 pp.**
>
> **Engine directional accuracy: 55.6% (BULL/BEAR). Mean confidence: 0.20.
> Engine spent 49% of days in TRANSITION state (no conviction).**

The production engine **does not generate alpha**. Its value is as a market
dashboard, not a signal for position sizing.

---

## What actually happened (equity curve reading)

The `engine_backtest_equity.png` visual tells a specific story that pure
return numbers hide:

- **2021 H2 – mid-2022:** Engine underperforms HODL during the late-2021
  top because it remains invested. Expected — no model catches the top.
- **Mid-2022 – end-2023:** **Engine successfully defends** through the
  2022 bear market. By Q4 2022, engine equity is ~$100k while HODL is
  ~$55k — a +$45k absolute defence. **The engine's BEAR detector works
  when it matters.**
- **2024 – end-2025:** Engine **fails to re-engage**. As HODL rallies
  from $90k to $400k+, engine drags along $140k → $190k. Missed bull =
  the entire 5y underperformance.
- **2026 Q1 correction:** HODL gives back $100k in the crash; engine
  stays relatively flat. Small defensive win, but too late to matter.

**The asymmetry is the real finding:** engine de-risks well, re-risks
poorly. In a market with structural upward drift, this produces negative
alpha because the missed bull always dwarfs the avoided bear.

This is also why **max DD is only −37.5% for the engine vs ~−77% for HODL**
during the 2022 bear — the defence worked. But sitting in cash for 18 months
afterwards while BTC 6x'd costs far more than it saved.

---

## Results

| Strategy | Return | Final equity | vs HODL |
|---|---|---|---|
| **Production engine (v3.3, gated DD)** | +78.8% | $178,807 | **−66.2%** |
| HODL | +145.0% | $245,044 | — |
| Fixed 60/40 | +87.0% | $187,026 | −58.0% |
| **SMA 50/200 crossover** | **+152.6%** | **$252,561** | **+7.5%** |

**Risk-adjusted:** Sharpe 0.74, Information Ratio vs HODL −0.51, max DD −37.5%.
**Activity:** 89 rebalance trades over 5 years.

Equity curve: see `engine_backtest_equity.png`.

---

## Regime distribution

| Regime | Days | % of period |
|---|---|---|
| TRANSITION | 856 | 49% |
| BULL | 561 | 32% |
| BEAR | 319 | 18% |
| RANGE | 0 | 0% |

**Half the time, the engine has no conviction.** The TRANSITION state
gets 49% of days — this is not a directional model, it's a "something is
happening" detector.

RANGE was never called across 5 years. The RANGE logit in `settings.py` may
be tuned too restrictively, or the softmax never lets RANGE win against
BULL/BEAR/TRANSITION.

---

## Signal quality — does regime predict forward returns?

| Regime | N days | Mean fwd-7d return | Hit rate |
|---|---|---|---|
| BULL | 561 | +1.36% | 57% positive |
| BEAR | 312 | −0.11% | 54% negative |
| TRANSITION | 856 | +0.39% | 51% positive |

**BULL signals work weakly** — 57% positive is 7pp above coinflip, but the
mean +1.36% is close to the period's base-rate return.

**BEAR signals are essentially noise** — mean fwd-7d of −0.11% is
statistically indistinguishable from zero.

**Confidence-weighted directional accuracy: 55.2%.** Weighting by the engine's
own confidence does not improve accuracy — the engine is not aware of when
it's right vs wrong.

**Mean confidence = 0.20.** Only 24% of days cross a 0.30 confidence
threshold.

---

## Interpretation

### Why does the production engine lose to HODL?

The equity curve reveals the core mechanism: **asymmetric re-engagement
failure**.

1. **Engine de-risks well.** During the 2022 bear, engine equity stayed
   at ~$100k while HODL fell to $55k — the BEAR detector earned its
   keep, a +$45k defence.

2. **Engine fails to re-risk fast enough.** After the bear ended in late
   2022, engine held reduced exposure through most of 2024. During that
   period HODL went from $90k to $400k (4.4×); engine went from $140k to
   $190k (1.4×). The missed bull cost far more than the avoided bear
   saved.

3. **Mechanism:** asymmetric switching (`should_switch` in engine.py)
   requires high conviction to exit BEAR, but keeps sticky exposure
   reduction during TRANSITION. In a market with structural upward drift,
   this is exactly wrong — you want symmetric re-entry, or actually
   biased toward re-entry since base rates favour up.

4. **BEAR signal accuracy near-chance (54% negative for fwd-7d).** Even
   when defending in 2022, the engine was right roughly 54% of the time
   — the defence worked because BEAR was sustained, not because the
   signal was particularly accurate.

5. **TRANSITION bucket dominates (49%).** During TRANSITION, engine holds
   60-90% — most of the "missed bull" happened here, with the engine
   unable to commit to BULL even after the recovery was clear.

### Why does SMA 50/200 crossover beat everything?

SMA crossover is the simplest trend-follower. It wins because:

- Crypto has strong trending behaviour on the monthly-quarterly scale
- SMA crossovers catch those trends with ~30-60 day lag but no churn
- When wrong, it's wrong slowly — not liquidating on a daily flip-flop
- It doesn't try to predict — it follows

The engine **tries to predict**, fails at prediction (55% accuracy), and
pays transaction costs on 89 trades to achieve that.

### What IS the engine good for?

Based on the 200-day real prod data analyzed in Phase 2:

- **Market state awareness** — knowing "we're in BEAR with Greed" is
  meaningfully different from "BEAR with Fear" for context
- **Operational hints** — `operational_hints()` surfaces real conditions
  (vol regime, churn warnings)
- **Risk flags** — `CRISIS` and `TAIL` do correlate with real tail events
- **Educational dashboard** — Russian-language Telegram output that helps
  the user stay informed

None of that requires the model to generate P&L. The model is valuable
as **awareness**, not **alpha**.

---

## Comparison with the simplified model

From `HONEST_BACKTEST_REPORT.md` (Phase 1, simplified heuristic over CoinMetrics
data): returned +186.2% vs HODL's +275.8%. Alpha −89.6pp.

From this backtest (production engine): +78.8% vs HODL +145.0%. Alpha −66.2pp.

Different windows — but the pattern is consistent: both versions of the
model produce **persistent negative alpha**. The production engine's added
machinery (softmax, EMA, asymmetric switching, gap-based confidence, adaptive
temperature, exposure caps) produces a somewhat smaller negative alpha but
still far below zero.

**The complexity does not rescue the signal.** When the underlying regime
predictor has 55% accuracy, no amount of post-processing can turn it into
alpha.

---

## Caveats

1. **Funding rate is a proxy.** Real OKX funding is regionally blocked from
   US IPs; fetcher falls back to momentum-derived proxy. Phase-2 sensitivity
   analysis showed LOGIT Sentiment weights 0.2→1.0 move alpha by ±1pp only,
   so this is not the cause of underperformance.

2. **BTC dominance coverage is only 1 year** (CoinGecko Free cap). Days
   before 2025-04 have empty `btc_dom_history`, so Rotation bucket is 0
   for 80% of the backtest.

3. **Open Interest history starts empty.** OKX is snapshot-only. Sentiment
   bucket's 25% OI weight contributes zero for early days.

4. **No cross-asset data** (SPX, GOLD). Cross-asset correlation adjustments
   are inactive — minor effect on regime decision.

5. **One market cycle.** 5 years covers 2021 bull → 2022 bear → 2023-2025
   recovery → 2026 correction. A full cycle, not multiple.

---

## Recommendations

### Honest positioning

The README should reflect that OracAI is:

> **An educational market state dashboard.** Tracks regime probabilities,
> confidence, and risk flags to help build intuition about market
> conditions. Not a signal for active position management; demonstrates
> negative alpha vs HODL over 5y of historical data.

### Three paths forward

**Option A: Fix the re-entry asymmetry.** The engine's defence works —
the problem is re-engagement. Concrete interventions:
- Lower the BULL-side confirmation threshold in `should_switch`
- Add a "recovery detection" rule: if price > 200-day SMA for N days AND
  regime has been BEAR/TRANSITION, force move toward BULL
- Cap TRANSITION's exposure reduction (stop letting 49% of days run at
  60-90% when underlying BTC is in an uptrend)

This is the surgical fix — preserves the engine's dashboard value AND
keeps defensive value AND fixes the specific failure mode.

**Option B: Replace regime detector with trend-follower.** SMA 50/200
already beats engine by 74pp. Simpler, backtestable, actually works on
this data. But loses all the contextual richness of the regime buckets.

**Option C: Keep current engine for awareness, add SMA overlay for sizing.**
Regime for context, SMA for position size. Two separate systems running
in parallel. Combines awareness with working alpha.

**Option D: Reposition as pure dashboard.** Remove "target %" and "action"
lines from Telegram output. Provide observational summaries only. Stop
implying trade advice the model can't deliver.

My recommendation as senior analyst: **Option A first, then C if A doesn't
close the gap.** Option A directly addresses the mechanism we identified
in the equity curve and would take ~1 day's research work. If post-fix
alpha is still negative, move to Option C.

---

## Files

- `backtest_engine_real.py` — the backtest driver (240 lines)
- `engine_backtest_daily.csv` — per-day regime, confidence, buckets, action
- `engine_backtest_results.json` — summary metrics
- `engine_backtest_equity.png` — equity curves visualization
- `data/external/` — historical data inputs (see its README.md)
