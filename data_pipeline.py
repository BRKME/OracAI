"""
Data Pipeline — fetches all inputs from free public APIs.

Sources:
  Price OHLCV:
    Primary:  Yahoo Finance (BTC-USD) — works everywhere incl. GitHub Actions
    Fallback: CoinGecko OHLC (no volume, but OHLC accurate)
  Funding / OI:
    Binance Futures (optional — blocked on US IPs, graceful degradation)
  Market data:
    CoinGecko: total market cap, BTC dominance
  Sentiment:
    alternative.me: Fear & Greed Index
  Macro:
    Yahoo Finance: DXY, SPX, Gold
    FRED: US Treasury yields, M2

Note: Binance returns HTTP 451 from GitHub Actions (US geo-restriction).
"""

import os
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)


# ============================================================
# YAHOO FINANCE — PRIMARY PRICE SOURCE
# ============================================================

def fetch_btc_price_yahoo(period: str = "1y") -> pd.DataFrame:
    """
    Fetch BTC OHLCV from Yahoo Finance (BTC-USD).
    Works on all IPs including GitHub Actions.
    """
    try:
        import yfinance as yf
        data = yf.download("BTC-USD", period=period, progress=False, auto_adjust=True)

        if data.empty:
            logger.warning("Yahoo BTC-USD returned empty data")
            return pd.DataFrame()

        df = data.copy()

        # Handle MultiIndex columns from yfinance
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.reset_index()
        df = df.rename(columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        })
        df["date"] = pd.to_datetime(df["date"]).dt.date

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        # quote_volume ≈ volume_BTC × close_price
        df["quote_volume"] = df["volume"] * df["close"]

        return df[["date", "open", "high", "low", "close", "volume", "quote_volume"]].copy()

    except Exception as e:
        logger.error(f"Yahoo BTC-USD failed: {e}")
        return pd.DataFrame()


# ============================================================
# COINGECKO — FALLBACK PRICE + MARKET DATA
# ============================================================

CG_BASE = "https://api.coingecko.com/api/v3"


def fetch_btc_price_coingecko(days: int = 365) -> pd.DataFrame:
    """
    Fallback: BTC OHLC from CoinGecko.
    For days > 90, returns daily candles.
    """
    url = f"{CG_BASE}/coins/bitcoin/ohlc"
    params = {"vs_currency": "usd", "days": days}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.date

        # Aggregate to daily (CoinGecko may give sub-daily candles)
        daily = df.groupby("date").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
        }).reset_index()

        for col in ["open", "high", "low", "close"]:
            daily[col] = daily[col].astype(float)

        daily["volume"] = 0.0
        daily["quote_volume"] = 0.0

        return daily

    except Exception as e:
        logger.error(f"CoinGecko OHLC failed: {e}")
        return pd.DataFrame()


def fetch_coingecko_global() -> dict:
    """Fetch global market data: total market cap, BTC dominance, ETH price."""
    url = f"{CG_BASE}/global"

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]

        result = {
            "total_market_cap_usd": data["total_market_cap"].get("usd", 0),
            "btc_dominance": data.get("market_cap_percentage", {}).get("btc", 0),
            "eth_price": None,
        }
        
        # Fetch ETH price separately
        try:
            eth_url = f"{CG_BASE}/simple/price?ids=ethereum&vs_currencies=usd"
            eth_resp = requests.get(eth_url, timeout=10)
            eth_resp.raise_for_status()
            result["eth_price"] = eth_resp.json().get("ethereum", {}).get("usd", 0)
        except:
            pass
        
        return result
    except Exception as e:
        logger.warning(f"CoinGecko global failed: {e}")
        return {"total_market_cap_usd": None, "btc_dominance": None, "eth_price": None}


def fetch_coingecko_market_cap_history(days: int = 120) -> pd.DataFrame:
    """Fetch total market cap history."""
    url = f"{CG_BASE}/coins/bitcoin/market_chart"
    params = {"vs_currency": "usd", "days": days, "interval": "daily"}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        mc = pd.DataFrame(data["market_caps"], columns=["timestamp", "market_cap"])
        mc["date"] = pd.to_datetime(mc["timestamp"], unit="ms").dt.date
        return mc[["date", "market_cap"]]
    except Exception as e:
        logger.warning(f"CoinGecko market chart failed: {e}")
        return pd.DataFrame(columns=["date", "market_cap"])


# ============================================================
# BINANCE — FUNDING RATE + OI (optional, geo-restricted)
# ============================================================

BINANCE_BASE = "https://api.binance.com"


def fetch_binance_funding_rate(symbol: str = "BTCUSDT", limit: int = 100) -> pd.DataFrame:
    """Fetch funding rate. Non-critical — will be empty if Binance blocked."""
    url = f"{BINANCE_BASE}/fapi/v1/fundingRate"
    params = {"symbol": symbol, "limit": limit}

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["fundingTime"], unit="ms").dt.date
        df["fundingRate"] = df["fundingRate"].astype(float)
        return df.groupby("date")["fundingRate"].mean().reset_index()
    except Exception as e:
        logger.info(f"  ○ Binance funding rate unavailable: {type(e).__name__}")
        return pd.DataFrame(columns=["date", "fundingRate"])


def fetch_binance_open_interest(symbol: str = "BTCUSDT") -> Optional[float]:
    """Fetch open interest. Non-critical."""
    url = f"{BINANCE_BASE}/fapi/v1/openInterest"
    params = {"symbol": symbol}

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return float(resp.json()["openInterest"])
    except Exception as e:
        logger.info(f"  ○ Binance OI unavailable: {type(e).__name__}")
        return None


# ============================================================
# FALLBACK: OKX — FUNDING RATE + OI
# ============================================================

def fetch_okx_funding_rate() -> pd.DataFrame:
    """Fetch funding rate from OKX. Fallback for Binance."""
    url = "https://www.okx.com/api/v5/public/funding-rate-history"
    params = {"instId": "BTC-USDT-SWAP", "limit": "100"}
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        
        if not data:
            return pd.DataFrame(columns=["date", "fundingRate"])
        
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["fundingTime"].astype(int), unit="ms").dt.date
        df["fundingRate"] = df["fundingRate"].astype(float)
        return df.groupby("date")["fundingRate"].mean().reset_index()
    except Exception as e:
        logger.info(f"  ○ OKX funding rate unavailable: {type(e).__name__}")
        return pd.DataFrame(columns=["date", "fundingRate"])


def fetch_okx_open_interest() -> Optional[float]:
    """Fetch open interest from OKX. Fallback for Binance."""
    url = "https://www.okx.com/api/v5/public/open-interest"
    params = {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        
        if data:
            return float(data[0]["oi"])
        return None
    except Exception as e:
        logger.info(f"  ○ OKX OI unavailable: {type(e).__name__}")
        return None


# ============================================================
# FALLBACK: BYBIT — FUNDING RATE + OI
# ============================================================

def fetch_bybit_funding_rate() -> pd.DataFrame:
    """Fetch funding rate from Bybit. Fallback for Binance/OKX."""
    url = "https://api.bybit.com/v5/market/funding/history"
    params = {"category": "linear", "symbol": "BTCUSDT", "limit": "100"}
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("result", {}).get("list", [])
        
        if not data:
            return pd.DataFrame(columns=["date", "fundingRate"])
        
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["fundingRateTimestamp"].astype(int), unit="ms").dt.date
        df["fundingRate"] = df["fundingRate"].astype(float)
        return df.groupby("date")["fundingRate"].mean().reset_index()
    except Exception as e:
        logger.info(f"  ○ Bybit funding rate unavailable: {type(e).__name__}")
        return pd.DataFrame(columns=["date", "fundingRate"])


def fetch_bybit_open_interest() -> Optional[float]:
    """Fetch open interest from Bybit. Fallback for Binance/OKX."""
    url = "https://api.bybit.com/v5/market/open-interest"
    params = {"category": "linear", "symbol": "BTCUSDT", "intervalTime": "5min", "limit": "1"}
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("result", {}).get("list", [])
        
        if data:
            return float(data[0]["openInterest"])
        return None
    except Exception as e:
        logger.info(f"  ○ Bybit OI unavailable: {type(e).__name__}")
        return None


# ============================================================
# AGGREGATED FETCH WITH FALLBACKS
# ============================================================

def fetch_funding_rate_with_fallback() -> pd.DataFrame:
    """Try Binance → OKX → Bybit for funding rate."""
    # Try Binance first
    df = fetch_binance_funding_rate()
    if not df.empty:
        logger.info("  ✓ Funding rate: Binance")
        return df
    
    # Try OKX
    df = fetch_okx_funding_rate()
    if not df.empty:
        logger.info("  ✓ Funding rate: OKX (fallback)")
        return df
    
    # Try Bybit
    df = fetch_bybit_funding_rate()
    if not df.empty:
        logger.info("  ✓ Funding rate: Bybit (fallback)")
        return df
    
    return pd.DataFrame(columns=["date", "fundingRate"])


def fetch_open_interest_with_fallback() -> Optional[float]:
    """Try Binance → OKX → Bybit for open interest."""
    # Try Binance first
    oi = fetch_binance_open_interest()
    if oi is not None:
        logger.info(f"  ✓ OI: Binance")
        return oi
    
    # Try OKX
    oi = fetch_okx_open_interest()
    if oi is not None:
        logger.info(f"  ✓ OI: OKX (fallback)")
        return oi
    
    # Try Bybit
    oi = fetch_bybit_open_interest()
    if oi is not None:
        logger.info(f"  ✓ OI: Bybit (fallback)")
        return oi
    
    return None


# ============================================================
# RSI — MULTI-TIMEFRAME (Binance SPOT + Bybit fallback)
# ============================================================

def calculate_rsi(closes: list, period: int = 14) -> float:
    """
    Calculate RSI from close prices.
    
    Args:
        closes: List of close prices (oldest to newest)
        period: RSI period (default 14)
    
    Returns:
        RSI value (0-100)
    """
    if len(closes) < period + 1:
        return 50.0  # Neutral default
    
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return round(rsi, 2)


def fetch_binance_klines(symbol: str = "BTCUSDT", interval: str = "1d", limit: int = 100) -> list:
    """
    Fetch klines from Binance SPOT API.
    Works without authentication, no geo-restrictions on SPOT.
    
    Args:
        symbol: Trading pair (BTCUSDT, ETHUSDT)
        interval: 1m, 5m, 15m, 1h, 2h, 4h, 1d, 1w
        limit: Number of candles (max 1000)
    
    Returns:
        List of close prices (oldest to newest)
    """
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        # Kline format: [open_time, open, high, low, close, volume, ...]
        closes = [float(candle[4]) for candle in data]
        return closes
    except Exception as e:
        logger.warning(f"Binance klines failed: {e}")
        return []


def fetch_bybit_klines(symbol: str = "BTCUSDT", interval: str = "D", limit: int = 100) -> list:
    """
    Fetch klines from Bybit API (fallback).
    
    Args:
        symbol: Trading pair
        interval: 1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M
        limit: Number of candles (max 1000)
    
    Returns:
        List of close prices (oldest to newest)
    """
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "spot", "symbol": symbol, "interval": interval, "limit": limit}
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("result", {}).get("list", [])
        
        if not data:
            return []
        
        # Bybit returns newest first, reverse it
        # Format: [startTime, open, high, low, close, volume, turnover]
        closes = [float(candle[4]) for candle in reversed(data)]
        return closes
    except Exception as e:
        logger.warning(f"Bybit klines failed: {e}")
        return []


def fetch_yahoo_klines(symbol: str = "BTC-USD", period: str = "60d", interval: str = "1d") -> list:
    """
    Fetch klines from Yahoo Finance (final fallback).
    Works everywhere including GitHub Actions.
    
    Args:
        symbol: Yahoo ticker (BTC-USD, ETH-USD)
        period: 1d, 5d, 1mo, 3mo, 6mo, 1y
        interval: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo
    
    Returns:
        List of close prices (oldest to newest)
    """
    try:
        import yfinance as yf
        data = yf.download(symbol, period=period, interval=interval, progress=False)
        
        if data.empty:
            return []
        
        closes = data["Close"].values.tolist()
        return closes
    except Exception as e:
        logger.warning(f"Yahoo klines failed: {e}")
        return []


def fetch_rsi_multi_timeframe(symbol: str = "BTCUSDT") -> dict:
    """
    Fetch RSI for multiple timeframes.
    
    Returns:
        {
            "rsi_1d": float,      # Daily RSI-14 (strategy)
            "rsi_2h": float,      # 2-hour RSI-14 (tactical)
            "rsi_1d_7": float,    # Daily RSI-7 (momentum)
            "source": str         # "binance", "bybit", or "yahoo"
        }
    """
    result = {
        "rsi_1d": None,
        "rsi_2h": None,
        "rsi_1d_7": None,
        "source": None
    }
    
    # Map symbol to Yahoo ticker
    yahoo_map = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD"}
    yahoo_symbol = yahoo_map.get(symbol, "BTC-USD")
    
    # ═══ TRY BINANCE FIRST ═══
    
    # Daily RSI (need 30+ candles for stable RSI-14)
    closes_1d = fetch_binance_klines(symbol, "1d", 50)
    if closes_1d and len(closes_1d) >= 15:
        result["rsi_1d"] = calculate_rsi(closes_1d, 14)
        result["rsi_1d_7"] = calculate_rsi(closes_1d, 7)
        result["source"] = "binance"
        logger.info(f"  ✓ RSI Daily: {result['rsi_1d']:.1f} (Binance)")
    
    # 2-hour RSI
    closes_2h = fetch_binance_klines(symbol, "2h", 50)
    if closes_2h and len(closes_2h) >= 15:
        result["rsi_2h"] = calculate_rsi(closes_2h, 14)
        logger.info(f"  ✓ RSI 2h: {result['rsi_2h']:.1f} (Binance)")
    
    # ═══ FALLBACK TO BYBIT ═══
    if result["rsi_1d"] is None:
        closes_1d = fetch_bybit_klines(symbol, "D", 50)
        if closes_1d and len(closes_1d) >= 15:
            result["rsi_1d"] = calculate_rsi(closes_1d, 14)
            result["rsi_1d_7"] = calculate_rsi(closes_1d, 7)
            result["source"] = "bybit"
            logger.info(f"  ✓ RSI Daily: {result['rsi_1d']:.1f} (Bybit fallback)")
    
    if result["rsi_2h"] is None:
        closes_2h = fetch_bybit_klines(symbol, "120", 50)  # 120 = 2 hours
        if closes_2h and len(closes_2h) >= 15:
            result["rsi_2h"] = calculate_rsi(closes_2h, 14)
            logger.info(f"  ✓ RSI 2h: {result['rsi_2h']:.1f} (Bybit fallback)")
    
    # ═══ FINAL FALLBACK: YAHOO FINANCE ═══
    if result["rsi_1d"] is None:
        closes_1d = fetch_yahoo_klines(yahoo_symbol, "60d", "1d")
        if closes_1d and len(closes_1d) >= 15:
            result["rsi_1d"] = calculate_rsi(closes_1d, 14)
            result["rsi_1d_7"] = calculate_rsi(closes_1d, 7)
            result["source"] = "yahoo"
            logger.info(f"  ✓ RSI Daily: {result['rsi_1d']:.1f} (Yahoo fallback)")
    
    if result["rsi_2h"] is None:
        # Yahoo doesn't have good 2h data, try 1h as approximation
        closes_1h = fetch_yahoo_klines(yahoo_symbol, "5d", "1h")
        if closes_1h and len(closes_1h) >= 15:
            result["rsi_2h"] = calculate_rsi(closes_1h, 14)
            logger.info(f"  ✓ RSI 1h (approx 2h): {result['rsi_2h']:.1f} (Yahoo fallback)")
    
    return result


def fetch_rsi_with_fallback() -> dict:
    """
    Fetch RSI for BTC and ETH.
    
    Returns:
        {
            "btc": {"rsi_1d": float, "rsi_2h": float, "rsi_1d_7": float, "source": str},
            "eth": {"rsi_1d": float, "rsi_2h": float, "rsi_1d_7": float, "source": str}
        }
    """
    logger.info("📊 Fetching RSI...")
    
    btc_rsi = fetch_rsi_multi_timeframe("BTCUSDT")
    eth_rsi = fetch_rsi_multi_timeframe("ETHUSDT")
    
    return {"btc": btc_rsi, "eth": eth_rsi}


# ============================================================
# FEAR & GREED (no auth)
# ============================================================

def fetch_fear_greed(limit: int = 90) -> pd.DataFrame:
    """Fetch Crypto Fear & Greed Index."""
    url = "https://api.alternative.me/fng/"
    params = {"limit": limit, "format": "json"}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]

        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s").dt.date
        df["value"] = df["value"].astype(int)
        return df[["date", "value"]].rename(columns={"value": "fear_greed"})
    except Exception as e:
        logger.warning(f"Fear & Greed failed: {e}")
        return pd.DataFrame(columns=["date", "fear_greed"])


# ============================================================
# YAHOO FINANCE — MACRO SERIES
# ============================================================

def fetch_yahoo_series(tickers: dict, period: str = "6mo") -> pd.DataFrame:
    """Fetch daily close for macro tickers."""
    try:
        import yfinance as yf

        frames = {}
        for name, symbol in tickers.items():
            try:
                data = yf.download(symbol, period=period, progress=False, auto_adjust=True)
                if not data.empty:
                    series = data["Close"].copy()
                    if hasattr(series, 'columns'):
                        series = series.iloc[:, 0]
                    series.name = name
                    frames[name] = series
            except Exception as e:
                logger.warning(f"Yahoo {name} ({symbol}) failed: {e}")

        if not frames:
            return pd.DataFrame()

        df = pd.DataFrame(frames)
        df.index = pd.to_datetime(df.index).date
        df.index.name = "date"
        return df.reset_index()

    except ImportError:
        logger.warning("yfinance not installed")
        return pd.DataFrame()


# ============================================================
# FRED (needs free API key)
# ============================================================

def fetch_fred_series(series_ids: dict, observation_start: str = None) -> pd.DataFrame:
    """Fetch FRED economic data series."""
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        logger.warning("FRED_API_KEY not set, skipping macro data")
        return pd.DataFrame()

    if observation_start is None:
        observation_start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

    try:
        from fredapi import Fred
        fred = Fred(api_key=api_key)

        frames = {}
        for name, series_id in series_ids.items():
            try:
                s = fred.get_series(series_id, observation_start=observation_start)
                s.name = name
                frames[name] = s
            except Exception as e:
                logger.warning(f"FRED {name} ({series_id}) failed: {e}")

        if not frames:
            return pd.DataFrame()

        df = pd.DataFrame(frames)
        df.index = pd.to_datetime(df.index).date
        df.index.name = "date"
        df = df.ffill()
        return df.reset_index()

    except ImportError:
        logger.warning("fredapi not installed")
        return pd.DataFrame()


# ============================================================
# AGGREGATE PIPELINE
# ============================================================

def fetch_all_data() -> dict:
    """
    Fetch all data sources. Handles failures gracefully.
    
    Price chain: Yahoo Finance → CoinGecko OHLC
    Binance data (funding/OI): optional, non-blocking
    """
    logger.info("Fetching data from all sources...")
    result = {
        "price": None,
        "funding": None,
        "open_interest": None,
        "global": None,
        "market_cap_history": None,
        "fear_greed": None,
        "rsi": None,  # NEW: Multi-timeframe RSI
        "yahoo": None,
        "fred": None,
        "quality": {
            "completeness": 1.0, 
            "sources_available": 0, 
            "sources_total": 9,  # Updated from 8 to 9
            "failed_sources": []
        },
        "fetch_time": datetime.utcnow().isoformat(),
    }

    sources_ok = 0
    failed_sources = []

    # ── 1. BTC Price (Yahoo → CoinGecko fallback) ──────────
    logger.info("  [1/8] BTC price (Yahoo Finance)...")
    price_df = fetch_btc_price_yahoo(period="1y")

    if price_df.empty:
        logger.warning("  Yahoo failed, trying CoinGecko OHLC...")
        time.sleep(2)
        price_df = fetch_btc_price_coingecko(days=365)

    if not price_df.empty:
        result["price"] = price_df
        sources_ok += 1
        logger.info(f"  ✓ BTC price: {len(price_df)} days, "
                    f"last=${price_df['close'].iloc[-1]:,.0f}")
    else:
        logger.error("  ✗ BTC price: ALL SOURCES FAILED")
        failed_sources.append("BTC Price")

    # ── 2. Funding rate (with fallback: Binance → OKX → Bybit) ─
    logger.info("  [2/8] Funding rate...")
    result["funding"] = fetch_funding_rate_with_fallback()
    if not result["funding"].empty:
        sources_ok += 1
        logger.info(f"       {len(result['funding'])} days")
    else:
        failed_sources.append("Funding")

    # ── 3. Open interest (with fallback: Binance → OKX → Bybit) ─
    logger.info("  [3/8] Open interest...")
    result["open_interest"] = fetch_open_interest_with_fallback()
    if result["open_interest"] is not None:
        sources_ok += 1
        logger.info(f"       OI: {result['open_interest']:,.0f}")
    else:
        failed_sources.append("OI")

    time.sleep(1)

    # ── 4. CoinGecko global ─────────────────────────────────
    logger.info("  [4/8] CoinGecko global...")
    result["global"] = fetch_coingecko_global()
    if result["global"]["total_market_cap_usd"] is not None:
        sources_ok += 1
        logger.info(f"  ✓ TMC=${result['global']['total_market_cap_usd']/1e12:.2f}T, "
                    f"BTC.D={result['global']['btc_dominance']:.1f}%")
    else:
        failed_sources.append("CoinGecko")

    time.sleep(2)

    # ── 5. Market cap history ───────────────────────────────
    logger.info("  [5/8] Market cap history...")
    result["market_cap_history"] = fetch_coingecko_market_cap_history(days=120)
    if not result["market_cap_history"].empty:
        sources_ok += 1
        logger.info(f"  ✓ MCap history: {len(result['market_cap_history'])} days")
    else:
        failed_sources.append("MCap")

    # ── 6. Fear & Greed ─────────────────────────────────────
    logger.info("  [6/8] Fear & Greed...")
    result["fear_greed"] = fetch_fear_greed()
    if not result["fear_greed"].empty:
        sources_ok += 1
        fg_now = result["fear_greed"].iloc[0]["fear_greed"]
        logger.info(f"  ✓ Fear & Greed: {len(result['fear_greed'])} days, current={fg_now}")
    else:
        failed_sources.append("F&G")

    # ── 7. RSI Multi-timeframe (Binance → Bybit) ────────────
    logger.info("  [7/9] RSI (Daily + 2h)...")
    result["rsi"] = fetch_rsi_with_fallback()
    if result["rsi"]["btc"]["rsi_1d"] is not None:
        sources_ok += 1
        btc_rsi = result["rsi"]["btc"]
        logger.info(f"  ✓ BTC RSI: 1d={btc_rsi['rsi_1d']:.1f}, 2h={btc_rsi['rsi_2h']:.1f if btc_rsi['rsi_2h'] else 'N/A'}")
    else:
        failed_sources.append("RSI")

    # ── 8. Yahoo macro ──────────────────────────────────────
    logger.info("  [8/9] Yahoo macro (DXY, SPX, Gold)...")
    result["yahoo"] = fetch_yahoo_series({
        "DXY": "DX-Y.NYB",
        "SPX": "^GSPC",
        "GOLD": "GC=F",
    })
    if not result["yahoo"].empty:
        sources_ok += 1
        logger.info(f"  ✓ Yahoo macro: {len(result['yahoo'])} days")
    else:
        failed_sources.append("Yahoo")

    # ── 9. FRED ─────────────────────────────────────────────
    logger.info("  [9/9] FRED (yields, M2)...")
    result["fred"] = fetch_fred_series({
        "US_10Y": "DGS10",
        "US_2Y": "DGS2",
        "M2": "M2SL",
    })
    if not result["fred"].empty:
        sources_ok += 1
        logger.info(f"  ✓ FRED: {len(result['fred'])} rows")
    else:
        failed_sources.append("FRED")

    result["quality"]["sources_available"] = sources_ok
    result["quality"]["completeness"] = sources_ok / result["quality"]["sources_total"]
    result["quality"]["failed_sources"] = failed_sources

    logger.info("=" * 50)
    logger.info(f"DATA: {sources_ok}/{result['quality']['sources_total']} sources OK "
                f"({result['quality']['completeness']:.0%})")
    if failed_sources:
        logger.info(f"FAILED: {', '.join(failed_sources)}")
    logger.info("=" * 50)

    return result
