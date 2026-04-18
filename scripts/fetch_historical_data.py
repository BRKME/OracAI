#!/usr/bin/env python3
"""
Historical data fetcher for OracAI 5y backtest & research.

Fetches (all free or free-with-API-key):
  - BTC daily OHLCV (high/low needed for ADX in engine.py)  → Binance public
  - BTC dominance history                                    → CMC /v1/global-metrics
  - Fear & Greed history (real, not synthetic)               → CMC /v3/fear-and-greed
  - Funding rate history                                     → Binance Futures
  - Open Interest history                                    → Binance Futures
  - ETH daily close                                          → Binance public
  - DXY, US10Y, US2Y, M2                                     → FRED
  - Keeps CoinMetrics BTC close (data/btc.csv) as canonical  → already in repo

Writes to data/external/ as incremental CSVs. Re-run is idempotent: appends
new rows only. Designed to run daily via GitHub Actions.

Env vars (set as GitHub Secrets):
  CMC_API_KEY   — get free at https://pro.coinmarketcap.com/signup (10k credits/mo)
  FRED_API_KEY  — get free at https://fred.stlouisfed.org/docs/api/api_key.html

Usage:
  python scripts/fetch_historical_data.py             # full 5y backfill first run
  python scripts/fetch_historical_data.py --incremental  # only append new days

Total credit cost per full 5y backfill:
  CMC: ~50 credits (F&G + BTC.D + margin)
  FRED: no cost (separate free tier)
  Binance: no cost (public endpoints)
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "external"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CMC_KEY = os.getenv("CMC_API_KEY")
FRED_KEY = os.getenv("FRED_API_KEY")


# ════════════════════════════════════════════════════════════════
# Incremental CSV helper
# ════════════════════════════════════════════════════════════════
def load_existing(path: Path) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path, parse_dates=["date"])
        return df
    return pd.DataFrame()


def save_merged(path: Path, new_df: pd.DataFrame, key: str = "date"):
    """Merge new data with existing, dedupe by date, sort, save."""
    if new_df.empty:
        logger.info(f"  (nothing new for {path.name})")
        return
    existing = load_existing(path)
    if not existing.empty:
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=[key], keep="last")
    else:
        combined = new_df
    combined = combined.sort_values(key).reset_index(drop=True)
    combined.to_csv(path, index=False)
    logger.info(f"  ✓ {path.name}: {len(combined)} rows (added {len(new_df)} new)")


# ════════════════════════════════════════════════════════════════
# Binance — BTC/ETH OHLCV, funding, OI
# ════════════════════════════════════════════════════════════════
BINANCE_SPOT = "https://api.binance.com"
BINANCE_FUTURES = "https://fapi.binance.com"


def fetch_binance_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Paginated klines — Binance caps at 1000 per request."""
    rows = []
    current = start_ms
    while current < end_ms:
        url = f"{BINANCE_SPOT}/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "startTime": current,
                  "endTime": end_ms, "limit": 1000}
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        # Next window starts after last kline open time
        current = batch[-1][0] + 1
        time.sleep(0.1)  # rate limit politeness
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    df["date"] = pd.to_datetime(df["open_time"], unit="ms").dt.normalize()
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    return df[["date", "open", "high", "low", "close", "volume", "quote_volume"]]


def fetch_btc_ohlcv(start: datetime, end: datetime) -> pd.DataFrame:
    logger.info(f"Fetching BTC OHLCV from Binance: {start.date()} → {end.date()}")
    return fetch_binance_klines("BTCUSDT", "1d",
                                 int(start.timestamp() * 1000),
                                 int(end.timestamp() * 1000))


def fetch_eth_ohlcv(start: datetime, end: datetime) -> pd.DataFrame:
    logger.info(f"Fetching ETH OHLCV from Binance: {start.date()} → {end.date()}")
    return fetch_binance_klines("ETHUSDT", "1d",
                                 int(start.timestamp() * 1000),
                                 int(end.timestamp() * 1000))


def fetch_binance_funding(start: datetime, end: datetime) -> pd.DataFrame:
    """Funding rate history — Binance Futures. 8-hour resolution, aggregated to daily mean."""
    logger.info(f"Fetching funding rate: {start.date()} → {end.date()}")
    rows = []
    current = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    while current < end_ms:
        url = f"{BINANCE_FUTURES}/fapi/v1/fundingRate"
        params = {"symbol": "BTCUSDT", "startTime": current, "endTime": end_ms, "limit": 1000}
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        current = batch[-1]["fundingTime"] + 1
        time.sleep(0.1)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["fundingTime"], unit="ms").dt.normalize()
    df["fundingRate"] = df["fundingRate"].astype(float)
    return df.groupby("date", as_index=False)["fundingRate"].mean()


def fetch_binance_oi_history(start: datetime, end: datetime) -> pd.DataFrame:
    """Open Interest — daily. Note: Binance only keeps ~30 days; for deeper history
    this will only cover recent data. Engine accepts short OI history."""
    logger.info(f"Fetching open interest (last 30d max): {start.date()} → {end.date()}")
    url = f"{BINANCE_FUTURES}/futures/data/openInterestHist"
    params = {"symbol": "BTCUSDT", "period": "1d", "limit": 500}
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        batch = r.json()
        df = pd.DataFrame(batch)
        if df.empty:
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.normalize()
        df["open_interest"] = df["sumOpenInterest"].astype(float)
        return df[["date", "open_interest"]]
    except Exception as e:
        logger.warning(f"OI fetch failed: {e}")
        return pd.DataFrame()


# ════════════════════════════════════════════════════════════════
# CMC — Fear & Greed, BTC dominance
# ════════════════════════════════════════════════════════════════
def fetch_cmc_fear_greed_history() -> pd.DataFrame:
    """CMC v3 F&G historical. 1 credit per 100 records."""
    if not CMC_KEY:
        logger.warning("CMC_API_KEY not set — skipping F&G")
        return pd.DataFrame()
    logger.info("Fetching CMC Fear & Greed history...")
    url = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/historical"
    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    all_rows = []
    start = 1
    while True:
        params = {"start": start, "limit": 500}
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            logger.warning(f"CMC F&G HTTP {r.status_code}: {r.text[:200]}")
            break
        data = r.json().get("data", [])
        if not data:
            break
        all_rows.extend(data)
        if len(data) < 500:
            break
        start += 500
        time.sleep(1)
    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["timestamp"]).dt.normalize()
    df["fear_greed"] = df["value"].astype(int)
    df["classification"] = df["value_classification"]
    return df[["date", "fear_greed", "classification"]].drop_duplicates("date")


def fetch_cmc_btc_dominance_history(start: datetime, end: datetime) -> pd.DataFrame:
    """CMC /v1/global-metrics/quotes/historical. 1 credit per 100 data points."""
    if not CMC_KEY:
        logger.warning("CMC_API_KEY not set — skipping BTC.D")
        return pd.DataFrame()
    logger.info(f"Fetching BTC dominance history: {start.date()} → {end.date()}")
    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/historical"
    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    params = {
        "time_start": start.strftime("%Y-%m-%d"),
        "time_end": end.strftime("%Y-%m-%d"),
        "interval": "daily",
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            logger.warning(f"CMC BTC.D HTTP {r.status_code}: {r.text[:200]}")
            return pd.DataFrame()
        quotes = r.json().get("data", {}).get("quotes", [])
        rows = []
        for q in quotes:
            ts = pd.to_datetime(q["timestamp"]).normalize()
            usd = q.get("quote", {}).get("USD", {})
            rows.append({
                "date": ts,
                "btc_dominance": usd.get("btc_dominance"),
                "eth_dominance": usd.get("eth_dominance"),
                "total_market_cap": usd.get("total_market_cap"),
            })
        return pd.DataFrame(rows)
    except Exception as e:
        logger.warning(f"BTC.D fetch failed: {e}")
        return pd.DataFrame()


# ════════════════════════════════════════════════════════════════
# FRED — macro
# ════════════════════════════════════════════════════════════════
def fetch_fred_macro(start: datetime) -> pd.DataFrame:
    if not FRED_KEY:
        logger.warning("FRED_API_KEY not set — skipping macro")
        return pd.DataFrame()
    try:
        from fredapi import Fred
    except ImportError:
        logger.warning("fredapi not installed (pip install fredapi)")
        return pd.DataFrame()
    logger.info(f"Fetching FRED macro series from {start.date()}...")
    fred = Fred(api_key=FRED_KEY)
    series_ids = {
        "DXY": "DTWEXBGS",   # USD index (trade-weighted, broad goods)
        "US10Y": "DGS10",
        "US2Y": "DGS2",
        "M2": "M2SL",        # monthly, will forward-fill
    }
    obs_start = start.strftime("%Y-%m-%d")
    frames = {}
    for name, sid in series_ids.items():
        try:
            s = fred.get_series(sid, observation_start=obs_start)
            s.name = name
            frames[name] = s
        except Exception as e:
            logger.warning(f"FRED {name} failed: {e}")
    if not frames:
        return pd.DataFrame()
    df = pd.DataFrame(frames)
    df.index = pd.to_datetime(df.index).normalize()
    df.index.name = "date"
    df = df.ffill()  # forward-fill weekends + sparse series
    return df.reset_index()


# ════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5, help="How many years to backfill on first run")
    ap.add_argument("--incremental", action="store_true",
                    help="Only fetch from latest saved date (for daily cron)")
    args = ap.parse_args()

    end = datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    full_start = end - timedelta(days=args.years * 365 + 30)

    def window_for(path_name: str) -> datetime:
        """If incremental and file exists, start from last saved date - 3d (safety overlap)."""
        if not args.incremental:
            return full_start
        p = DATA_DIR / path_name
        if not p.exists():
            return full_start
        try:
            existing = pd.read_csv(p, parse_dates=["date"])
            last = existing["date"].max()
            return last - timedelta(days=3)
        except Exception:
            return full_start

    # Track what was fetched for status report
    status = {}

    # ── BTC OHLCV ──
    try:
        start = window_for("btc_ohlcv.csv")
        df = fetch_btc_ohlcv(start, end)
        save_merged(DATA_DIR / "btc_ohlcv.csv", df)
        status["btc_ohlcv"] = f"{len(df)} new rows"
    except Exception as e:
        logger.error(f"BTC OHLCV failed: {e}")
        status["btc_ohlcv"] = f"FAIL: {e}"

    # ── ETH OHLCV ──
    try:
        start = window_for("eth_ohlcv.csv")
        df = fetch_eth_ohlcv(start, end)
        save_merged(DATA_DIR / "eth_ohlcv.csv", df)
        status["eth_ohlcv"] = f"{len(df)} new rows"
    except Exception as e:
        logger.error(f"ETH OHLCV failed: {e}")
        status["eth_ohlcv"] = f"FAIL: {e}"

    # ── Funding rate ──
    try:
        start = window_for("funding_rate.csv")
        df = fetch_binance_funding(start, end)
        save_merged(DATA_DIR / "funding_rate.csv", df)
        status["funding_rate"] = f"{len(df)} new rows"
    except Exception as e:
        logger.error(f"Funding failed: {e}")
        status["funding_rate"] = f"FAIL: {e}"

    # ── OI (limited by Binance to ~30d) ──
    try:
        df = fetch_binance_oi_history(end - timedelta(days=30), end)
        save_merged(DATA_DIR / "open_interest.csv", df)
        status["open_interest"] = f"{len(df)} rows (Binance ~30d window)"
    except Exception as e:
        logger.error(f"OI failed: {e}")
        status["open_interest"] = f"FAIL: {e}"

    # ── Fear & Greed ──
    try:
        df = fetch_cmc_fear_greed_history()
        # F&G API returns all history; we still merge to dedupe
        save_merged(DATA_DIR / "fear_greed.csv", df)
        status["fear_greed"] = f"{len(df)} total rows"
    except Exception as e:
        logger.error(f"F&G failed: {e}")
        status["fear_greed"] = f"FAIL: {e}"

    # ── BTC dominance ──
    try:
        start = window_for("btc_dominance.csv")
        df = fetch_cmc_btc_dominance_history(start, end)
        save_merged(DATA_DIR / "btc_dominance.csv", df)
        status["btc_dominance"] = f"{len(df)} new rows"
    except Exception as e:
        logger.error(f"BTC.D failed: {e}")
        status["btc_dominance"] = f"FAIL: {e}"

    # ── FRED macro ──
    try:
        start = window_for("fred_macro.csv")
        df = fetch_fred_macro(start)
        save_merged(DATA_DIR / "fred_macro.csv", df)
        status["fred_macro"] = f"{len(df)} new rows"
    except Exception as e:
        logger.error(f"FRED failed: {e}")
        status["fred_macro"] = f"FAIL: {e}"

    # Write status report
    status_path = DATA_DIR / "_status.json"
    status_data = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "mode": "incremental" if args.incremental else f"backfill_{args.years}y",
        "sources": status,
    }
    status_path.write_text(json.dumps(status_data, indent=2))
    logger.info(f"\nStatus saved to {status_path}")
    logger.info(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
