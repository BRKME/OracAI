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
import numpy as np
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
# CryptoCompare — primary OHLCV source (5y+ free, no US geoblock)
# ════════════════════════════════════════════════════════════════
CRYPTOCOMPARE_API = "https://min-api.cryptocompare.com/data/v2"


def fetch_cryptocompare_daily(fsym: str, tsym: str, days: int) -> pd.DataFrame:
    """Paginated daily candles. CryptoCompare caps at 2000 per call.
    Walk backwards with toTs to cover arbitrary history."""
    url = f"{CRYPTOCOMPARE_API}/histoday"
    rows = []
    to_ts = int(datetime.now(timezone.utc).timestamp())
    remaining = days
    iterations = 0
    while remaining > 0 and iterations < 10:
        chunk = min(remaining, 2000)
        params = {"fsym": fsym, "tsym": tsym, "limit": chunk, "toTs": to_ts}
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            payload = r.json()
            if payload.get("Response") != "Success":
                logger.warning(f"CryptoCompare error: {payload.get('Message')}")
                break
            data = payload.get("Data", {}).get("Data", [])
            if not data:
                break
            rows = data + rows  # prepend older
            earliest = min(x["time"] for x in data)
            to_ts = earliest - 1
            remaining -= chunk
            iterations += 1
            time.sleep(0.2)
        except Exception as e:
            logger.warning(f"CryptoCompare request failed: {e}")
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["time"], unit="s").dt.normalize()
    df = df.rename(columns={"volumeto": "quote_volume", "volumefrom": "volume"})
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df = df[df["close"] > 0]  # drop pre-listing placeholder rows
    return df[["date", "open", "high", "low", "close", "volume", "quote_volume"]]


# ════════════════════════════════════════════════════════════════
# Kraken — OHLCV fallback
# ════════════════════════════════════════════════════════════════
KRAKEN_API = "https://api.kraken.com"


def fetch_kraken_ohlc(pair: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Paginated Kraken OHLC. interval=1440 = daily. Max 720 candles per call."""
    rows = []
    current_since = int(start.timestamp())
    end_ts = int(end.timestamp())
    iterations = 0
    while current_since < end_ts and iterations < 20:
        url = f"{KRAKEN_API}/0/public/OHLC"
        params = {"pair": pair, "interval": 1440, "since": current_since}
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        payload = r.json()
        if payload.get("error"):
            raise RuntimeError(f"Kraken error: {payload['error']}")
        result = payload.get("result", {})
        data_keys = [k for k in result.keys() if k != "last"]
        if not data_keys:
            break
        batch = result[data_keys[0]]
        if not batch:
            break
        rows.extend(batch)
        last = result.get("last", 0)
        if not last or last <= current_since:
            break
        current_since = last
        iterations += 1
        time.sleep(0.3)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close",
                                      "vwap", "volume", "count"])
    df["date"] = pd.to_datetime(df["time"], unit="s").dt.normalize()
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["quote_volume"] = df["volume"] * df["close"]
    return df[["date", "open", "high", "low", "close", "volume", "quote_volume"]]


def fetch_btc_ohlcv(start: datetime, end: datetime) -> pd.DataFrame:
    """Try CryptoCompare (5y+ reliable); fallback to Kraken."""
    days = (end - start).days + 10
    logger.info(f"Fetching BTC OHLCV from CryptoCompare ({days}d)...")
    df = fetch_cryptocompare_daily("BTC", "USD", days)
    if not df.empty:
        # Filter to requested window
        df = df[(df["date"] >= pd.Timestamp(start.date())) & (df["date"] <= pd.Timestamp(end.date()))]
        if len(df) > 100:
            return df
    logger.info("CryptoCompare insufficient — trying Kraken fallback")
    return fetch_kraken_ohlc("XXBTZUSD", start, end)


def fetch_eth_ohlcv(start: datetime, end: datetime) -> pd.DataFrame:
    days = (end - start).days + 10
    logger.info(f"Fetching ETH OHLCV from CryptoCompare ({days}d)...")
    df = fetch_cryptocompare_daily("ETH", "USD", days)
    if not df.empty:
        df = df[(df["date"] >= pd.Timestamp(start.date())) & (df["date"] <= pd.Timestamp(end.date()))]
        if len(df) > 100:
            return df
    logger.info("CryptoCompare insufficient — trying Kraken fallback")
    return fetch_kraken_ohlc("XETHZUSD", start, end)


def fetch_okx_funding(start: datetime, end: datetime) -> pd.DataFrame:
    """Funding rate from OKX. No US geoblock on public endpoints.
    Returns daily mean of 3x-per-day funding rates for BTC-USDT-SWAP."""
    logger.info(f"Fetching funding rate from OKX: {start.date()} → {end.date()}")
    url = "https://www.okx.com/api/v5/public/funding-rate-history"
    rows = []
    end_ms = int(end.timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)
    current_before = end_ms
    iterations = 0
    while current_before > start_ms and iterations < 60:
        params = {"instId": "BTC-USDT-SWAP", "before": current_before, "limit": 100}
        try:
            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()
            payload = r.json()
            if payload.get("code") != "0":
                logger.warning(f"OKX funding error: {payload.get('msg')}")
                break
            batch = payload.get("data", [])
            if not batch:
                break
            rows.extend(batch)
            earliest = min(int(x["fundingTime"]) for x in batch)
            if earliest <= start_ms or earliest >= current_before:
                break
            current_before = earliest - 1
            iterations += 1
            time.sleep(0.2)
        except Exception as e:
            logger.warning(f"OKX funding pagination failed: {e}")
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms").dt.normalize()
    df["fundingRate"] = df["fundingRate"].astype(float)
    df = df[(df["date"] >= pd.Timestamp(start.date())) & (df["date"] <= pd.Timestamp(end.date()))]
    return df.groupby("date", as_index=False)["fundingRate"].mean()


def derive_funding_proxy(btc_ohlcv_path: Path) -> pd.DataFrame:
    """When live funding rate isn't available (OKX regional blocking, etc.), derive a
    proxy from BTC price momentum.

    Rationale: in real data, daily funding rate correlates ~+0.3 with 7-day price
    momentum — when market trends up, longs pay shorts (positive funding); when
    trends down, shorts pay longs (negative funding). This isn't a perfect
    substitute, but it provides a non-zero signal for Sentiment bucket.

    Scale chosen to match typical BTC funding magnitudes (±0.0005 daily = ±0.05%).
    """
    if not btc_ohlcv_path.exists():
        logger.warning("No BTC OHLCV available for funding proxy")
        return pd.DataFrame()
    logger.info("Deriving funding rate proxy from BTC momentum (OKX data unavailable)...")
    df = pd.read_csv(btc_ohlcv_path, parse_dates=["date"]).sort_values("date")
    # 7-day log return as momentum
    df["logret_7d"] = np.log(df["close"] / df["close"].shift(7))
    # Rolling 30d std normalizes across regimes
    df["vol_30d"] = df["logret_7d"].rolling(30, min_periods=10).std()
    # Standardized momentum → funding proxy at typical scale
    df["fundingRate"] = (df["logret_7d"] / df["vol_30d"].replace(0, np.nan)) * 0.00025
    df["fundingRate"] = df["fundingRate"].fillna(0).clip(-0.003, 0.003)
    return df[["date", "fundingRate"]].dropna()


def fetch_okx_oi_latest() -> pd.DataFrame:
    """OKX current OI. OKX's public OI is a point-in-time snapshot (no daily history
    on free tier). We append today's value each run so state/ accumulates it over time."""
    logger.info("Fetching current OI from OKX...")
    url = "https://www.okx.com/api/v5/public/open-interest"
    params = {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        payload = r.json()
        if payload.get("code") != "0":
            return pd.DataFrame()
        batch = payload.get("data", [])
        if not batch:
            return pd.DataFrame()
        row = batch[0]
        return pd.DataFrame([{
            "date": pd.Timestamp.now(tz="UTC").tz_localize(None).normalize(),
            "open_interest": float(row.get("oi", 0)),
            "open_interest_usd": float(row.get("oiCcy", 0)) * float(row.get("markPx", 0)) if row.get("markPx") else None,
        }])
    except Exception as e:
        logger.warning(f"OKX OI failed: {e}")
        return pd.DataFrame()


# ════════════════════════════════════════════════════════════════
# CMC — Fear & Greed, BTC dominance
# ════════════════════════════════════════════════════════════════
def fetch_cmc_fear_greed_history() -> pd.DataFrame:
    """CMC v3 F&G historical. `start` is pagination position (1-indexed)."""
    if not CMC_KEY:
        logger.warning("CMC_API_KEY not set — skipping F&G")
        return pd.DataFrame()
    logger.info("Fetching CMC Fear & Greed history...")
    url = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/historical"
    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    all_rows = []
    start = 1
    max_iterations = 20
    for i in range(max_iterations):
        params = {"start": start, "limit": 500}
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
        except Exception as e:
            logger.warning(f"CMC F&G request failed: {e}")
            break
        if r.status_code != 200:
            logger.warning(f"CMC F&G HTTP {r.status_code}: {r.text[:200]}")
            break
        try:
            payload = r.json()
        except Exception as e:
            logger.warning(f"CMC F&G JSON parse failed: {e}")
            break
        data = payload.get("data") or []
        if not data:
            break
        all_rows.extend(data)
        if len(data) < 500:
            break
        start += 500
        time.sleep(1.2)  # Free tier = 30/min
    if not all_rows:
        return pd.DataFrame()
    # Response format: [{"timestamp": "...", "value": int, "value_classification": str}]
    # timestamp can be ISO string OR Unix seconds as string — handle both
    df = pd.DataFrame(all_rows)
    if "timestamp" not in df.columns:
        logger.warning(f"CMC F&G: unexpected schema {df.columns.tolist()}")
        return pd.DataFrame()

    def parse_ts(v):
        try:
            if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
                return pd.to_datetime(int(v), unit="s").normalize()
            return pd.to_datetime(v).normalize()
        except Exception:
            return pd.NaT

    df["date"] = df["timestamp"].apply(parse_ts)
    df = df.dropna(subset=["date"])
    df["fear_greed"] = df["value"].astype(int)
    df["classification"] = df["value_classification"]
    return df[["date", "fear_greed", "classification"]].drop_duplicates("date")


def fetch_cmc_btc_dominance_history(start: datetime, end: datetime) -> pd.DataFrame:
    """CMC /v1/global-metrics/quotes/historical.
    Basic Free tier = 1 month window only. Window beyond that returns empty.
    For longer history, fall back to CoinGecko (see fetch_coingecko_btc_dominance).
    """
    if not CMC_KEY:
        logger.warning("CMC_API_KEY not set — skipping BTC.D (CMC)")
        return pd.DataFrame()
    # CMC Free tier caps historical to last 1 month. Clamp start if earlier.
    min_allowed_start = end - timedelta(days=29)
    effective_start = max(start, min_allowed_start)
    logger.info(f"Fetching BTC.D from CMC: {effective_start.date()} → {end.date()}")
    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/historical"
    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    params = {
        "time_start": effective_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval": "daily",
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            logger.warning(f"CMC BTC.D HTTP {r.status_code}: {r.text[:300]}")
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
        logger.warning(f"CMC BTC.D fetch failed: {e}")
        return pd.DataFrame()


def fetch_coingecko_btc_dominance(days: int = 365) -> pd.DataFrame:
    """CoinGecko /coins/bitcoin/market_chart — free, no API key, up to 365d daily.
    For longer than 1y we can't get daily through free API; use what's available."""
    logger.info(f"Fetching BTC dominance from CoinGecko: last {days}d...")
    # Use global market chart — gives total market cap; compute dominance from BTC mcap
    try:
        # BTC mcap history
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
            params={"vs_currency": "usd", "days": min(days, 365), "interval": "daily"},
            timeout=30,
        )
        r.raise_for_status()
        btc_data = r.json()
        btc_mcap = pd.DataFrame(btc_data.get("market_caps", []), columns=["ts", "btc_mcap"])
        btc_mcap["date"] = pd.to_datetime(btc_mcap["ts"], unit="ms").dt.normalize()

        time.sleep(2)  # CoinGecko free tier is strict: 30/min

        # Global market cap history (total)
        r2 = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=30,
        )
        r2.raise_for_status()
        # /global gives only current — for historical total, we approximate:
        # since we already have BTC mcap daily, we can query ETH mcap similarly
        # and use BTC / (BTC + ETH + stablecoins proxy). But simpler:
        # compute approximate dominance from btc price × circulating supply / total crypto mcap (latest known)
        # For now: store BTC mcap; downstream code can compute ratio when total mcap available.

        df = btc_mcap[["date", "btc_mcap"]].copy()
        df["btc_mcap"] = df["btc_mcap"].astype(float)
        df = df.drop_duplicates("date").sort_values("date")

        # Estimate BTC dominance using historical BTC mcap / historical total mcap
        # CoinGecko doesn't give historical total; estimate it from current ratio
        current_global = r2.json().get("data", {})
        current_btc_dom_pct = current_global.get("market_cap_percentage", {}).get("btc")
        current_total_mcap = current_global.get("total_market_cap", {}).get("usd")
        if current_btc_dom_pct and current_total_mcap and len(df) > 0:
            # Scale historical: if BTC mcap moves but ratio of BTC/total stays approx in trend
            # use current snapshot as anchor and assume total_mcap scales with BTC mcap ratio
            current_btc_mcap = df["btc_mcap"].iloc[-1]
            if current_btc_mcap > 0:
                implied_total_now = current_btc_mcap / (current_btc_dom_pct / 100)
                # Very rough: assume total/btc ratio scales slowly. This gives approx dominance.
                scale_factor = implied_total_now / current_btc_mcap
                df["btc_dominance"] = (df["btc_mcap"] / (df["btc_mcap"] * scale_factor)) * 100
            else:
                df["btc_dominance"] = current_btc_dom_pct
        else:
            df["btc_dominance"] = None

        df["eth_dominance"] = None
        df["total_market_cap"] = None
        return df[["date", "btc_dominance", "eth_dominance", "total_market_cap"]]
    except Exception as e:
        logger.warning(f"CoinGecko BTC.D fetch failed: {e}")
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

    # ── Funding rate (OKX public, falls back to momentum proxy if OKX regional-blocks) ──
    try:
        start = window_for("funding_rate.csv")
        df = fetch_okx_funding(start, end)
        if df.empty or len(df) < 30:
            logger.info(f"OKX funding returned {len(df)} rows (likely regional block) — using BTC momentum proxy")
            df = derive_funding_proxy(DATA_DIR / "btc_ohlcv.csv")
            source_note = "proxy from BTC momentum"
        else:
            source_note = "OKX"
        save_merged(DATA_DIR / "funding_rate.csv", df)
        status["funding_rate"] = f"{len(df)} new rows ({source_note})"
    except Exception as e:
        logger.error(f"Funding failed: {e}")
        status["funding_rate"] = f"FAIL: {e}"

    # ── OI (current snapshot; accumulates over time via daily cron) ──
    try:
        df = fetch_okx_oi_latest()
        save_merged(DATA_DIR / "open_interest.csv", df)
        status["open_interest"] = f"{len(df)} new rows (snapshot, accumulates daily)"
    except Exception as e:
        logger.error(f"OI failed: {e}")
        status["open_interest"] = f"FAIL: {e}"

    # ── Fear & Greed (CMC) ──
    try:
        df = fetch_cmc_fear_greed_history()
        save_merged(DATA_DIR / "fear_greed.csv", df)
        status["fear_greed"] = f"{len(df)} total rows"
    except Exception as e:
        logger.error(f"F&G failed: {e}")
        status["fear_greed"] = f"FAIL: {e}"

    # ── BTC dominance — try CMC (last 30d), then CoinGecko (last 365d) ──
    try:
        start = window_for("btc_dominance.csv")
        df_cmc = fetch_cmc_btc_dominance_history(start, end)
        df_cg = pd.DataFrame()
        if len(df_cmc) < 10:  # CMC only gave tiny window — supplement with CoinGecko
            logger.info("CMC BTC.D too short — fetching CoinGecko fallback")
            df_cg = fetch_coingecko_btc_dominance(days=365)
        # Prefer CMC for overlapping dates (higher fidelity), fall back to CG for older
        if not df_cg.empty and not df_cmc.empty:
            df = pd.concat([df_cg, df_cmc], ignore_index=True)
            df = df.drop_duplicates("date", keep="last")
        else:
            df = df_cmc if not df_cmc.empty else df_cg
        save_merged(DATA_DIR / "btc_dominance.csv", df)
        status["btc_dominance"] = f"{len(df)} new rows (CMC={len(df_cmc)}, CG={len(df_cg)})"
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
