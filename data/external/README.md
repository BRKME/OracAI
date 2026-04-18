# Historical data layer

This directory holds the 5-year historical data needed to backtest OracAI's
production `engine.py` on real buckets rather than heuristic approximations.

## Why this exists

`backtest_v5.py` tests a **simplified** regime model, not the real `engine.py`.
The production engine consumes Momentum, Stability, Rotation, Sentiment, and
Macro buckets derived from 10+ time series. Without those series, the full
5y research reported in `HONEST_BACKTEST_REPORT.md` was restricted to a
simplified model. `fetch_historical_data.py` fills that gap.

## Setup (one-time)

1. **Get CMC API key** (free, 10k credits/month):
   https://pro.coinmarketcap.com/signup

2. **Get FRED API key** (free, unlimited):
   https://fred.stlouisfed.org/docs/api/api_key.html

3. **Add both to GitHub Secrets** (Settings → Secrets and variables → Actions):
   - `CMC_API_KEY`
   - `FRED_API_KEY`

4. **First backfill** (manual trigger, ~3 minutes):
   - Go to Actions → Data Refresh → Run workflow
   - Check "Run full 5y backfill" = true
   - Run

After the first backfill, the cron runs daily at 02:00 UTC and only appends
new rows (incremental).

## What gets fetched

| File | Source | Cost | Coverage |
|---|---|---|---|
| `btc_ohlcv.csv` | Kraken public API | free | 2014+ (full history) |
| `eth_ohlcv.csv` | Kraken public API | free | 2015+ (full history) |
| `funding_rate.csv` | Bybit v5 public | free | 2020+ (Bybit launch) |
| `open_interest.csv` | Bybit v5 public | free | ~200 days (API window) |
| `fear_greed.csv` | CMC `/v3/fear-and-greed/historical` | ~5 CMC credits | full CMC history |
| `btc_dominance.csv` | CMC (last 30d) + CoinGecko (up to 365d) | ~5 CMC credits | last ~1 year |
| `fred_macro.csv` | FRED (DXY, US10Y, US2Y, M2) | 0 | 5y |

Total CMC cost per full backfill: **~10 credits** out of 10,000/month.
Incremental daily cost: **<2 credits**.

Why Kraken & Bybit instead of Binance: GitHub Actions runners are US-based, and
`binance.com` returns HTTP 451 (geoblock) from US IPs. Kraken and Bybit have
no such restriction and expose public endpoints that don't require an API key.

Why only 1y BTC dominance: CMC Basic Free tier caps
`/global-metrics/quotes/historical` to a 1-month window. For longer coverage
we fall back to CoinGecko's free `/coins/bitcoin/market_chart` (365 days daily).
If deeper BTC.D history is needed, it requires CMC Hobbyist ($29-33/mo) or
CoinGecko paid tier.

## Schema

### `btc_ohlcv.csv` / `eth_ohlcv.csv`
```
date,open,high,low,close,volume,quote_volume
2020-10-01,10611.2,10865.5,10450.0,10607.3,43211.4,...
```

### `funding_rate.csv`
```
date,fundingRate
2020-10-01,0.0001
```
Daily mean of 3x-per-day funding rates from Binance Futures BTCUSDT perpetual.

### `fear_greed.csv`
```
date,fear_greed,classification
2020-10-01,42,Fear
```
**Real CMC F&G**, not synthetic. Replaces the synthetic F&G used in earlier
backtests (which was computed from price+RSI+vol and therefore correlated
with the regime itself).

### `btc_dominance.csv`
```
date,btc_dominance,eth_dominance,total_market_cap
2020-10-01,57.8,13.1,347000000000.0
```

### `fred_macro.csv`
```
date,DXY,US10Y,US2Y,M2
2020-10-01,93.41,0.68,0.13,18700.0
```
Forward-filled on weekends and for M2 (which is monthly).

## Re-running manually

```bash
# Full 5-year backfill (use once to populate an empty data/external/)
CMC_API_KEY=xxx FRED_API_KEY=yyy python scripts/fetch_historical_data.py --years 5

# Incremental (what the cron does)
CMC_API_KEY=xxx FRED_API_KEY=yyy python scripts/fetch_historical_data.py --incremental
```

## Status

Each run writes `_status.json`. Check it to verify all sources loaded:

```json
{
  "last_run": "2026-04-18T02:00:00+00:00",
  "mode": "incremental",
  "sources": {
    "btc_ohlcv": "3 new rows",
    "fear_greed": "1832 total rows",
    "btc_dominance": "3 new rows",
    ...
  }
}
```

## What's next

Once this data is populated, the next PR will add `backtest_engine_real.py`
which:
1. Reads `data/external/*.csv`
2. Reconstructs historical Momentum/Stability/Rotation/Sentiment/Macro buckets
   by calling the existing `buckets.py` functions on daily slices
3. Drives the real `engine.py` softmax+EMA+confirmation pipeline day-by-day
4. Compares against HODL and honest baselines

This is the definitive test of whether the production model actually has edge,
something the simplified-model backtests could not answer.
