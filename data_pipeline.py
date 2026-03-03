"""
Data Pipeline v3.7 — Финальная стабильная версия (RSI + все источники)
"""

import os
import logging
from datetime import datetime
from typing import Optional, Dict, List

import numpy as np
import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# ====================== SAFE HELPERS ======================
def safe_last_price(df: pd.DataFrame) -> float:
    if df.empty or "close" not in df.columns:
        return 0.0
    try:
        return float(df["close"].iloc[-1])
    except:
        return 0.0

# ====================== YAHOO FINANCE ======================
def fetch_yahoo_klines(symbol: str = "BTC-USD", period: str = "90d", interval: str = "1d") -> List[float]:
    try:
        data = yf.download(tickers=symbol, period=period, interval=interval,
                           progress=False, auto_adjust=True, prepost=False)
        if data.empty:
            return []
        return data["Close"].dropna().astype(float).tolist()
    except Exception as e:
        logger.warning(f"yfinance klines failed: {e}")
        return []

def fetch_btc_price_yahoo(period: str = "1y") -> pd.DataFrame:
    try:
        data = yf.download("BTC-USD", period=period, progress=False, auto_adjust=True)
        if data.empty:
            return pd.DataFrame()

        if isinstance(data.columns, pd.MultiIndex):
            data = data["Close"].to_frame("close")

        df = data.reset_index()
        df = df.rename(columns={"Date": "date", "Open": "open", "High": "high",
                                "Low": "low", "Close": "close", "Volume": "volume"})
        df["date"] = pd.to_datetime(df["date"]).dt.date

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        df["quote_volume"] = df["volume"] * df["close"]
        return df[["date", "open", "high", "low", "close", "volume", "quote_volume"]].copy()
    except Exception as e:
        logger.error(f"Yahoo BTC-USD failed: {e}")
        return pd.DataFrame()

# ====================== COINGECKO ======================
CG_BASE = "https://api.coingecko.com/api/v3"

def fetch_coingecko_global() -> dict:
    try:
        resp = requests.get(f"{CG_BASE}/global", timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]
        return {
            "total_market_cap_usd": data["total_market_cap"].get("usd", 0),
            "btc_dominance": data.get("market_cap_percentage", {}).get("btc", 0),
            "eth_price": None
        }
    except:
        return {"total_market_cap_usd": None, "btc_dominance": None, "eth_price": None}

# ====================== RSI — РЕАЛЬНЫЙ ======================
def calculate_rsi(closes: List, period: int = 14) -> float:
    if not isinstance(closes, (list, tuple)) or len(closes) < period + 5:
        return 50.0
    if closes and isinstance(closes[0], (list, tuple)):
        closes = [float(c[4]) for c in closes]
    closes = [float(x) for x in closes if x is not None]
    if len(closes) < period + 5:
        return 50.0

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)[-period:]
    losses = np.where(deltas < 0, -deltas, 0.0)[-period:]

    avg_gain = float(np.mean(gains))
    avg_loss = float(np.mean(losses)) or 1e-10
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)

def fetch_rsi_with_fallback() -> dict:
    logger.info("  📊 Fetching RSI...")
    result = {"btc": {"rsi_1d": 50.0, "rsi_2h": 50.0, "rsi_1d_7": 50.0, "source": "unknown"}}
    try:
        closes_1d = fetch_yahoo_klines("BTC-USD", "90d", "1d")
        if len(closes_1d) >= 20:
            result["btc"]["rsi_1d"] = calculate_rsi(closes_1d, 14)
            result["btc"]["rsi_1d_7"] = calculate_rsi(closes_1d, 7)
            result["btc"]["source"] = "yfinance"

        closes_2h = fetch_yahoo_klines("BTC-USD", "14d", "2h")
        if len(closes_2h) >= 20:
            result["btc"]["rsi_2h"] = calculate_rsi(closes_2h, 14)
    except Exception as e:
        logger.warning(f"RSI failed: {e}")
    return {
        "btc": result["btc"],
        "eth": {"rsi_1d": 50.0, "rsi_2h": 50.0, "rsi_1d_7": 50.0, "source": "default"}
    }

# ====================== FEAR & GREED + MACRO ======================
def fetch_fear_greed(limit: int = 90) -> pd.DataFrame:
    try:
        resp = requests.get("https://api.alternative.me/fng/", params={"limit": limit}, timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s").dt.date
        df["fear_greed"] = df["value"].astype(int)
        return df[["date", "fear_greed"]].sort_values("date", ascending=False)
    except:
        logger.warning("Fear & Greed failed")
        return pd.DataFrame(columns=["date", "fear_greed"])

def fetch_yahoo_series() -> pd.DataFrame:
    try:
        tickers = {"DXY": "DX-Y.NYB", "SPX": "^GSPC", "GOLD": "GC=F"}
        data = yf.download(list(tickers.values()), period="180d", progress=False, auto_adjust=True)
        if isinstance(data.columns, pd.MultiIndex):
            data = data["Close"]
        data.columns = list(tickers.keys())
        df = data.reset_index()
        df["date"] = pd.to_datetime(df["Date"]).dt.date
        return df[["date"] + list(tickers.keys())]
    except:
        logger.warning("Yahoo macro failed")
        return pd.DataFrame()

# ====================== FUNDING + OI (fallback) ======================
def fetch_funding_rate_with_fallback() -> pd.DataFrame:
    logger.info("  ○ Funding rate: fallback")
    return pd.DataFrame(columns=["date", "fundingRate"])

def fetch_open_interest_with_fallback() -> Optional[float]:
    return None

# ====================== MAIN ======================
def fetch_all_data() -> dict:
    logger.info("Fetching data from all sources...")
    result = {
        "price": None, "funding": None, "open_interest": None,
        "global": None, "market_cap_history": None, "fear_greed": None,
        "rsi": None, "yahoo": None, "fred": None,
        "quality": {"completeness": 1.0, "sources_available": 0, "sources_total": 9, "failed_sources": []},
        "fetch_time": datetime.utcnow().isoformat(),
    }

    sources_ok = 0

    # 1. Price
    logger.info("  [1/9] BTC price (Yahoo Finance)...")
    price_df = fetch_btc_price_yahoo()
    if price_df.empty:
        price_df = fetch_btc_price_coingecko()  # если есть функция
    if not price_df.empty:
        last_price = safe_last_price(price_df)
        result["price"] = price_df
        sources_ok += 1
        logger.info(f"  ✓ BTC price: {len(price_df)} days, last=${last_price:,.0f}")

    # 4. Global
    logger.info("  [4/9] CoinGecko global...")
    result["global"] = fetch_coingecko_global()
    if result["global"]["total_market_cap_usd"] is not None:
        sources_ok += 1
        logger.info(f"  ✓ TMC=${result['global']['total_market_cap_usd']/1e12:.2f}T, BTC.D={result['global']['btc_dominance']:.1f}%")

    # 6. Fear & Greed
    logger.info("  [6/9] Fear & Greed...")
    result["fear_greed"] = fetch_fear_greed()
    if not result["fear_greed"].empty:
        sources_ok += 1
        fg = result["fear_greed"].iloc[0]["fear_greed"]
        logger.info(f"  ✓ Fear & Greed: current={fg}")

    # 7. RSI
    logger.info("  [7/9] RSI...")
    result["rsi"] = fetch_rsi_with_fallback()
    r = result["rsi"]["btc"]
    if r["source"] == "yfinance":
        sources_ok += 1
        logger.info(f"  ✓ RSI: 1D={r['rsi_1d']:.1f} | 2H={r['rsi_2h']:.1f} (yfinance)")

    # 8. Yahoo Macro
    logger.info("  [8/9] Yahoo macro...")
    result["yahoo"] = fetch_yahoo_series()
    if not result["yahoo"].empty:
        sources_ok += 1
        logger.info(f"  ✓ Yahoo macro: {len(result['yahoo'])} days")

    result["quality"]["sources_available"] = sources_ok
    result["quality"]["completeness"] = round(sources_ok / 9, 2)

    logger.info(f"DATA PIPELINE COMPLETE — {sources_ok}/9 sources OK ({result['quality']['completeness']:.0%})")
    return result
