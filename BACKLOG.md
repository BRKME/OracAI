# OracAI — BACKLOG

Open work items, honest status, and context for picking things up later.
Current head: Phase 4 Option A + prod logging.

---

## 0. Prod data collection (ACTIVE — DO NOT DISABLE)

**Status:** enabled 2026-04-20. Appends one row per cron-run to
`state/prod_log.csv`. Never breaks main flow (wrapped in try/except in
`prod_logger.py`).

### Why we're collecting this

Our 5y backtest (Phase 1-4) ran on the same data we used to tune settings.
That's the best we could do historically, but it means the +0.4% alpha
figure is **in-sample**. Genuine edge can only be confirmed by measuring
against data the model hasn't seen.

Every cron-run now writes one row with:
- engine's output (regime, probs, confidence, buckets, risk)
- BTC price at decision time
- Phase 4 fields (sma200 ratio, days above, would bear_confirmation fire,
  would recovery_override fire)
- data quality flags

After ~3 months (~2600 rows if hourly, ~90 if daily) we can compute:
- Forward-7d/30d returns given each regime label — does BULL actually
  predict positive? BEAR negative?
- Live hit rate on recovery_override (did target 0.95 actually work?)
- Whether live signals match the patterns seen in backtest

### What we deliberately do NOT collect

This is not telemetry — it's model evaluation data. NOT collected:
- User/chat IDs, Telegram identifiers, personal info of any kind
- Actual positions held, portfolio values, account balances
- Any auth credentials or API keys (obvious, mentioned for completeness)

All fields are either market state (BTC price, volume indicators) or model
outputs. See header in `prod_logger.py` for the exhaustive schema.

### How to analyze (when ready)

There's no analysis script yet — one should be written around
month 3. Rough template:

```python
import pandas as pd
df = pd.read_csv("state/prod_log.csv", parse_dates=["timestamp_utc"])
# Attach forward returns from BTC OHLCV
# Compute: df.groupby("regime")["fwd7d"].agg(["mean", "count", lambda s: (s>0).mean()])
```

### When to revisit this section

After 3 months of accumulated logs, write an analysis script and compare
live hit rates against the backtest numbers in
`ENGINE_BACKTEST_REPORT_PHASE4.md`. If they match → backtest was
directionally honest. If they diverge significantly → investigate the
delta (data drift? settings mis-tuned? market regime shift?).

### Storage notes

CSV in `state/` directory. Current git workflow already commits state/
on state refresh, so logs get versioned. If the file grows past ~5MB,
rotate (`mv prod_log.csv prod_log_YYYY-QN.csv` and start fresh).

---

## 1. Monitor Phase 4 in production (1-2 weeks)

**Status:** freshly deployed, needs observation. Automatic data collection
via §0 above covers most of this checklist now — but eyeballing Telegram
output is still useful for UX regressions.

Phase 4 changed 3 things in `settings.py` and added recovery override in
`telegram_bot.py`. Worth confirming on real prod flow:

- [ ] Telegram output format unchanged (no UX regression)
- [ ] `📈 Устойчивый аптренд: Nд выше SMA200. Удерживаем позицию.` line
      appears when BTC > SMA200 ≥10 days AND not bear_confirmation
      (check via `prod_log.csv`: `recovery_override_would_fire == True`)
- [ ] Regime switches didn't become noticeably more frequent
      (check via `prod_log.csv`: count distinct `regime` values per week)
- [ ] Target position in BULL days lands ~85-95% (was ~40-60% pre-Phase-4)
      (check via `prod_log.csv`: `regime == BULL → exposure_cap > 0.8`)
- [ ] Risk state (CRISIS / TAIL) still fires appropriately on real drops

If any regression: full revert is `git revert` on the two recent commits.

---

## 2. Remove `RISK_CONFIDENCE_GATE = 0.15` (low priority)

**Status:** candidate for removal after ~2 weeks of Phase 4 observation.

`settings.py` line ~361. After Phase 4 fixes, low-conf days are already
naturally capped via regime uncertainty, so this gate may be redundant.

**Action when revisiting:**
1. Run `backtest_engine_real.py` with gate disabled (comment out lines 311-315
   in `engine.py` `compute_risk_level`)
2. If alpha stays ≥ +0.4% and max DD doesn't degrade materially → remove
3. If degrades → keep as-is

Don't touch this yet; Phase 4 is enough change at once.

---

## 3. Real funding rate via CMC API — INVESTIGATED, DEFERRED

**Status:** researched 2026-04-20, not worth the effort. Documenting so we
don't re-waste a session re-investigating.

### Current state
`data/external/funding_rate.csv` is a **momentum proxy** (standardized 7d log
return × 0.00025). Real OKX funding blocked from US IPs (GitHub Actions silent
empty response). Phase-2 research showed funding weight ≈ 8% of net regime
decision, sensitivity ±1pp — so proxy is acceptable, just not ideal.

### What we found out about CMC (2026-04-20)
- **Official Pro API** (`pro-api.coinmarketcap.com`, what our `CMC_API_KEY` works
  with): **no funding rate endpoint.** Documented surface is 40+ endpoints:
  prices, OHLCV, exchange info, market pairs, global metrics, F&G, DEX — no
  derivatives funding. Not on free, not on Enterprise.
- **Public data-api** (`api.coinmarketcap.com/data-api/v3/derivatives/funding-rate/*`):
  undocumented backend of https://coinmarketcap.com/charts/funding-rates/ SPA.
  Technically accessible but:
  - Zero stability guarantees (can break anytime, no SLA)
  - Using it heavily from a keyed account could get the key blocked
  - Would need to maintain a fallback anyway
- **Other free sources with historical BTC funding:** couldn't find any that
  work from US IPs. Binance 451, Bybit 403, CoinGlass paid, CoinAPI paid.

### When to revisit (and only when)
Revisit ONLY if one of these is true:
- [ ] Phase 2 research re-run shows Sentiment bucket weight crossing ~15% of
      regime decision (right now it's ~8%, not material enough to justify)
- [ ] A new free API source becomes available (unlikely)
- [ ] We set up a VPN-proxied fetcher on Alexander's VPS in Lithuania (WG/VLESS
      already running). Then OKX real funding works without needing CMC.

If revisiting via VPS-proxy: safest route, since VPS IP is Lithuanian (EU), OKX
serves real data without regional block. Architecture: add a small HTTP endpoint
on the VPS that proxies `okx.com/api/v5/public/funding-rate-history`, GitHub
Actions calls that endpoint with a shared secret.

### Do NOT do (session ended with us at this exact decision point)
- Do NOT write another API probe script for CMC data-api
- Do NOT try more exchange APIs (Binance, Bybit already tested, blocked)
- Do NOT upgrade CMC plan for funding (not offered even at Enterprise)

**Decision from 2026-04-20 session: keep the proxy. Alpha impact is bounded.**

---

## 4. Phase 5 — potential future work (speculative, not planned)

If Phase 4 performance holds in prod for 1-2 months and there's appetite for
more work, these are natural next steps in decreasing value order:

1. **VPS-proxied real funding** (as described in §3). Replaces proxy with real
   data but expected alpha improvement is small (~1pp).
2. **Extend BTC dominance history** beyond the 1y CoinGecko cap. Rotation
   bucket is ~0 for 80% of historical backtest. CMC paid tier gets it, but
   ~$79/mo for one signal is hard to justify.
3. **Actual open interest history.** Currently snapshot only, accumulates
   live. Would need a paid source; same pricing concern.
4. **Cross-asset features (SPX, GOLD).** Used by `compute_cross_asset` but
   arrays are empty now. Yahoo Finance proxy or FRED equivalents could fill
   this. Low expected impact on regime decision itself, more useful for the
   `macro_boost` adjustment.
5. **Add regime=RANGE calibration.** 5y backtest showed RANGE was never
   triggered (0 days). Either RANGE logit thresholds are wrong, or RANGE
   should be removed as a concept. Investigate on same 5y data.

None of these are urgent. Phase 4 delivered the single largest piece of
alpha recovery already (from -66.2pp to +0.4pp).

---

## Phase history (quick reference)

| Phase | Commit | What it did |
|---|---|---|
| 1 | `fec6dee` | Honest 5y backtest of simplified heuristic (alpha -89.6pp) |
| 2 | `071538f` | Gated DD defender + conflict detection |
| 3 (data) | `eea5bbd` → `c648a81` | 5y data layer (CryptoCompare, CMC F&G, CoinGecko BTC.D, FRED, funding proxy) |
| 3 (test) | `abc9a13` | Real engine backtest diagnosed asymmetric re-entry failure |
| 4 | `1e0da62` | Surgical fix: alpha -66.2pp → +0.4pp |

Detailed write-ups live in repo root:
- `HONEST_BACKTEST_REPORT.md` (Phase 1)
- `PHASE2_RESEARCH_REPORT.md` (Phase 2)
- `ENGINE_BACKTEST_REPORT.md` (Phase 3)
- `ENGINE_BACKTEST_REPORT_PHASE4.md` (Phase 4)
