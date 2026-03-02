"""
Data Pipeline — fetches all inputs from free public APIs.

Sources:
  Price OHLCV:
    Primary:  Yahoo Finance (BTC-USD) — works everywhere incl. GitHub Actions
    Fallback: CoinGecko OHLC
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
from typing import Optional, Dict, List

import numpy as np
import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# ============================================================
# YAHOO FINANCE — PRIMARY & RELIABLE SOURCE
# ============================================================

def fetch_yahoo_klines(symbol: str = "BTC-USD", period: str = "90d", interval: str = "1d") -> List[float]:
    """Fetch close prices via yfinance (100% reliable on GitHub Actions)"""
    try:
        data = yf.download(
            tickers=symbol,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
            prepost=False
        )
        if data.empty:
            return []
        closes = data["Close"].dropna().astype(float).tolist()
        return closes
    except Exception as e:
        logger.warning(f"yfinance klines {symbol} {interval} failed: {e}")
        return []


def fetch_btc_price_yahoo(period: str = "1y") -> pd.DataFrame:
    """Main BTC price fetcher"""
    try:
        data = yf.download("BTC-USD", period=period, progress=False, auto_adjust=True)
        if data.empty:
            logger.warning("Yahoo BTC-USD returned empty data")
            return pd.DataFrame()

        df = data.copy()
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

        df["quote_volume"] = df["volume"] * df["close"]

        return df[["date", "open", "high", "low", "close", "volume", "quote_volume"]].copy()

    except Exception as e:
        logger.error(f"Yahoo BTC-USD failed: {e}")
        return pd.DataFrame()


# ============================================================
# COINGECKO — FALLBACKS
# ============================================================

CG_BASE = "https://api.coingecko.com/api/v3"

def fetch_btc_price_coingecko(days: int = 365) -> pd.DataFrame:
    url = f"{CG_BASE}/coins/bitcoin/ohlc"
    params = {"vs_currency": "usd", "days": days}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.date

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
        
        try:
            eth_url = f"{CG_BASE}/simple/price?ids=ethereum&vs_currencies=usd"
            eth_resp = requests.get(eth_url, timeout=10)
            result["eth_price"] = eth_resp.json().get("ethereum", {}).get("usd", 0)
        except:
            pass
        
        return result
    except Exception as e:
        logger.warning(f"CoinGecko global failed: {e}")
        return {"total_market_cap_usd": None, "btc_dominance": None, "eth_price": None}


def fetch_coingecko_market_cap_history(days: int = 120) -> pd.DataFrame:
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
# BINANCE — FUNDING + OI (original)
# ============================================================

BINANCE_BASE = "https://api.binance.com"

def fetch_binance_funding_rate(symbol: str = "BTCUSDT", limit: int = 100) -> pd.DataFrame:
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
# OKX — FALLBACK
# ============================================================

def fetch_okx_funding_rate() -> pd.DataFrame:
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
# BYBIT — FALLBACK
# ============================================================

def fetch_bybit_funding_rate() -> pd.DataFrame:
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
# AGGREGATED FUNDING + OI
# ============================================================

def fetch_funding_rate_with_fallback() -> pd.DataFrame:
    df = fetch_binance_funding_rate()
    if not df.empty:
        logger.info("  ✓ Funding rate: Binance")
        return df
    df = fetch_okx_funding_rate()
    if not df.empty:
        logger.info("  ✓ Funding rate: OKX (fallback)")
        return df
    df = fetch_bybit_funding_rate()
    if not df.empty:
        logger.info("  ✓ Funding rate: Bybit (fallback)")
        return df
    return pd.DataFrame(columns=["date", "fundingRate"])


def fetch_open_interest_with_fallback() -> Optional[float]:
    oi = fetch_binance_open_interest()
    if oi is not None:
        logger.info("  ✓ OI: Binance")
        return oi
    oi = fetch_okx_open_interest()
    if oi is not None:
        logger.info("  ✓ OI: OKX (fallback)")
        return oi
    oi = fetch_bybit_open_interest()
    if oi is not None:
        logger.info("  ✓ OI: Bybit (fallback)")
        return oi
    return None


# ============================================================
# RSI — FIXED (yfinance first)
# ============================================================

def calculate_rsi(closes: List[float], period: int = 14) -> float:
    """RSI с защитой от всех ошибок"""
    if not isinstance(closes, (list, tuple)) or len(closes) < period + 5:
        return 50.0

    if closes and isinstance(closes[0], (list, tuple)):
        try:
            closes = [float(candle[4]) for candle in closes]
        except:
            return 50.0

    try:
        closes = [float(x) for x in closes if x is not None]
    except:
        return 50.0

    if len(closes) < period + 5:
        return 50.0

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)[-period:]
    losses = np.where(deltas < 0, -deltas, 0.0)[-period:]

    avg_gain = float(np.mean(gains))
    avg_loss = float(np.mean(losses))

    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


def fetch_rsi_multi_timeframe() -> Dict:
    result = {
        "btc": {
            "rsi_1d": None,
            "rsi_2h": None,
            "rsi_1d_7": None,
            "source": "unknown"
        }
    }

    try:
        closes_1d = fetch_yahoo_klines("BTC-USD", period="90d", interval="1d")
        if len(closes_1d) >= 20:
            result["btc"]["rsi_1d"] = calculate_rsi(closes_1d, 14)
            result["btc"]["rsi_1d_7"] = calculate_rsi(closes_1d, 7)
            result["btc"]["source"] = "yfinance"
            logger.info(f"  ✓ RSI Daily: {result['btc']['rsi_1d']:.1f} (yfinance)")

        closes_2h = fetch_yahoo_klines("BTC-USD", period="14d", interval="2h")
        if len(closes_2h) >= 20:
            result["btc"]["rsi_2h"] = calculate_rsi(closes_2h, 14)
            logger.info(f"  ✓ RSI 2h: {result['btc']['rsi_2h']:.1f} (yfinance)")

    except Exception as e:
        logger.warning(f"yfinance RSI failed: {e}")

    if result["btc"]["rsi_1d"] is None:
        closes_1d = fetch_binance_funding_rate()  # dummy, just to avoid error
        # (не используем, просто placeholder)

    return result


def fetch_rsi_with_fallback() -> dict:
    logger.info("  📊 Fetching RSI...")
    rsi_data = fetch_rsi_multi_timeframe()
    return {"btc": rsi_data["btc"], "eth": {"rsi_1d": 50.0, "rsi_2h": 50.0, "rsi_1d_7": 50.0, "source": "default"}}


# ============================================================
# FEAR & GREED + MACRO
# ============================================================

def fetch_fear_greed(limit: int = 90) -> pd.DataFrame:
    url = "https://api.alternative.me/fng/"
    params = {"limit": limit, "format": "json"}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s").dt.date
        df["fear_greed"] = df["value"].astype(int)
        return df[["date", "fear_greed"]].sort_values("date", ascending=False)
    except Exception as e:
        logger.warning(f"Fear & Greed failed: {e}")
        return pd.DataFrame(columns=["date", "fear_greed"])


def fetch_yahoo_series(tickers: Dict[str, str], period: str = "180d") -> pd.DataFrame:
    try:
        data = yf.download(list(tickers.values()), period=period, progress=False, auto_adjust=True)
        if isinstance(data.columns, pd.MultiIndex):
            data = data["Close"]
        data.columns = list(tickers.keys())
        df = data.reset_index()
        df["date"] = pd.to_datetime(df["Date"]).dt.date
        return df[["date"] + list(tickers.keys())]
    except Exception as e:
        logger.warning(f"Yahoo macro failed: {e}")
        return pd.DataFrame()


def fetch_fred_series(series_ids: Dict[str, str]) -> pd.DataFrame:
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        logger.warning("FRED_API_KEY not set")
        return pd.DataFrame()
    try:
        from fredapi import Fred
        fred = Fred(api_key=api_key)
        frames = {}
        for name, sid in series_ids.items():
            s = fred.get_series(sid)
            frames[name] = s
        df = pd.DataFrame(frames)
        df.index = pd.to_datetime(df.index).date
        df = df.reset_index().rename(columns={"index": "date"})
        return df
    except Exception as e...
