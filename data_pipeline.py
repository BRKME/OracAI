"""
Data Pipeline v2.0 — Full data sources with 90%+ quality target
"""

import os
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ============================================================
# YAHOO FINANCE — PRIMARY PRICE + RSI SOURCE
# ============================================================

def fetch_btc_price_yahoo(period: str = "1y") -> pd.DataFrame:
    """Fetch BTC OHLCV from Yahoo Finance."""
    try:
        import yfinance as yf
        data = yf.download("BTC-USD", period=period, progress=False, auto_adjust=True)
        
        if data.empty:
            logger.warning("Yahoo BTC-USD returned empty data")
            return pd.DataFrame()

        # Fix MultiIndex if present
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        df = data.reset_index()
        df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
        
        # Rename columns
        rename_map = {"date": "date", "open": "open", "high": "high", 
                      "low": "low", "close": "close", "volume": "volume"}
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"]).dt.date
        
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        
        df = df.dropna(subset=["close"])
        if "volume" in df.columns and "close" in df.columns:
            df["quote_volume"] = df["volume"] * df["close"]
        else:
            df["quote_volume"] = 0.0
            
        return df[["date", "open", "high", "low", "close", "volume", "quote_volume"]].copy()
        
    except Exception as e:
        logger.error(f"Yahoo BTC-USD failed: {e}")
        return pd.DataFrame()


def fetch_yahoo_rsi(symbol: str = "BTC-USD", periods: list = None) -> dict:
    """Fetch RSI from Yahoo Finance for multiple timeframes."""
    if periods is None:
        periods = [14, 7]
    
    result = {"rsi_1d": None, "rsi_2h": None, "rsi_1d_7": None, "source": None}
    
    try:
        import yfinance as yf
        
        # Daily RSI
        data_1d = yf.download(symbol, period="60d", interval="1d", progress=False)
        if not data_1d.empty:
            if isinstance(data_1d.columns, pd.MultiIndex):
                closes = data_1d["Close"][symbol].values.flatten().tolist()
            else:
                closes = data_1d["Close"].values.flatten().tolist()
            if len(closes) >= 20:
                result["rsi_1d"] = calculate_rsi(closes, 14)
                result["rsi_1d_7"] = calculate_rsi(closes, 7)
                result["source"] = "yahoo"
                logger.info(f"  ✓ RSI 1D: {result['rsi_1d']:.1f} (Yahoo)")
        
        # 1-hour RSI (approximation for 2h)
        data_1h = yf.download(symbol, period="5d", interval="1h", progress=False)
        if not data_1h.empty:
            if isinstance(data_1h.columns, pd.MultiIndex):
                closes = data_1h["Close"][symbol].values.flatten().tolist()
            else:
                closes = data_1h["Close"].values.flatten().tolist()
            if len(closes) >= 20:
                result["rsi_2h"] = calculate_rsi(closes, 14)
                logger.info(f"  ✓ RSI 1H: {result['rsi_2h']:.1f} (Yahoo)")
                
    except Exception as e:
        logger.warning(f"Yahoo RSI failed: {e}")
    
    return result


# ============================================================
# COINGECKO — MARKET DATA
# ============================================================

CG_BASE = "https://api.coingecko.com/api/v3"

def fetch_btc_price_coingecko(days: int = 365) -> pd.DataFrame:
    """Fetch BTC OHLC from CoinGecko as fallback."""
    url = f"{CG_BASE}/coins/bitcoin/ohlc"
    params = {"vs_currency": "usd", "days": min(days, 90)}
    
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.date
        
        daily = df.groupby("date").agg({
            "open": "first", "high": "max", "low": "min", "close": "last"
        }).reset_index()
        
        for col in ["open", "high", "low", "close"]:
            daily[col] = daily[col].astype(float)
        daily["volume"] = daily["quote_volume"] = 0.0
        
        logger.info(f"  ✓ CoinGecko OHLC: {len(daily)} days")
        return daily
        
    except Exception as e:
        logger.warning(f"CoinGecko OHLC failed: {e}")
        return pd.DataFrame()


def fetch_coingecko_global() -> dict:
    """Fetch global market data from CoinGecko."""
    try:
        resp = requests.get(f"{CG_BASE}/global", timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]
        
        return {
            "total_market_cap_usd": data["total_market_cap"].get("usd", 0),
            "btc_dominance": data.get("market_cap_percentage", {}).get("btc", 0),
            "eth_price": None
        }
    except Exception as e:
        logger.warning(f"CoinGecko global failed: {e}")
        return {"total_market_cap_usd": None, "btc_dominance": None, "eth_price": None}


def fetch_coingecko_market_cap_history(days: int = 90) -> pd.DataFrame:
    """Fetch market cap history from CoinGecko."""
    url = f"{CG_BASE}/coins/bitcoin/market_chart"
    params = {"vs_currency": "usd", "days": days}
    
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        mc_data = data.get("market_caps", [])
        if not mc_data:
            return pd.DataFrame()
        
        df = pd.DataFrame(mc_data, columns=["timestamp", "market_cap"])
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.date
        df = df.groupby("date")["market_cap"].last().reset_index()
        
        return df
        
    except Exception as e:
        logger.warning(f"CoinGecko market cap history failed: {e}")
        return pd.DataFrame()


# ============================================================
# FEAR & GREED INDEX
# ============================================================

def fetch_fear_greed(limit: int = 30) -> pd.DataFrame:
    """Fetch Crypto Fear & Greed Index."""
    url = "https://api.alternative.me/fng/"
    params = {"limit": limit, "format": "json"}
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        
        if not data:
            return pd.DataFrame()
        
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s").dt.date
        df["fear_greed"] = df["value"].astype(int)
        df["classification"] = df["value_classification"]
        
        return df[["date", "fear_greed", "classification"]]
        
    except Exception as e:
        logger.warning(f"Fear & Greed failed: {e}")
        return pd.DataFrame()


# ============================================================
# BINANCE — FUNDING RATE + OPEN INTEREST + KLINES
# ============================================================

BINANCE_SPOT = "https://api.binance.com/api/v3"
BINANCE_FUTURES = "https://fapi.binance.com/fapi/v1"

def fetch_binance_klines(symbol: str = "BTCUSDT", interval: str = "1d", limit: int = 50) -> list:
    """Fetch klines from Binance SPOT API (no auth, no geo-restrictions)."""
    url = f"{BINANCE_SPOT}/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        closes = [float(candle[4]) for candle in data]
        return closes
    except Exception as e:
        logger.debug(f"Binance klines failed: {e}")
        return []


def fetch_binance_funding_rate(symbol: str = "BTCUSDT", limit: int = 100) -> pd.DataFrame:
    """Fetch funding rate from Binance Futures."""
    url = f"{BINANCE_FUTURES}/fundingRate"
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
        logger.debug(f"Binance funding failed: {e}")
        return pd.DataFrame(columns=["date", "fundingRate"])


def fetch_binance_open_interest(symbol: str = "BTCUSDT") -> Optional[float]:
    """Fetch open interest from Binance Futures."""
    url = f"{BINANCE_FUTURES}/openInterest"
    params = {"symbol": symbol}
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return float(resp.json()["openInterest"])
    except Exception as e:
        logger.debug(f"Binance OI failed: {e}")
        return None


# ============================================================
# BYBIT — FALLBACK
# ============================================================

def fetch_bybit_funding_rate() -> pd.DataFrame:
    """Fetch funding rate from Bybit."""
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
        logger.debug(f"Bybit funding failed: {e}")
        return pd.DataFrame(columns=["date", "fundingRate"])


def fetch_bybit_open_interest() -> Optional[float]:
    """Fetch open interest from Bybit."""
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
        logger.debug(f"Bybit OI failed: {e}")
        return None


# ============================================================
# OKX — FALLBACK
# ============================================================

def fetch_okx_funding_rate() -> pd.DataFrame:
    """Fetch funding rate from OKX."""
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
        logger.debug(f"OKX funding failed: {e}")
        return pd.DataFrame(columns=["date", "fundingRate"])


def fetch_okx_open_interest() -> Optional[float]:
    """Fetch open interest from OKX."""
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
        logger.debug(f"OKX OI failed: {e}")
        return None


# ============================================================
# AGGREGATED FETCH WITH FALLBACKS
# ============================================================

def fetch_funding_rate_with_fallback() -> pd.DataFrame:
    """Try Binance → OKX → Bybit for funding rate."""
    df = fetch_binance_funding_rate()
    if not df.empty:
        logger.info("  ✓ Funding rate: Binance")
        return df
    
    df = fetch_okx_funding_rate()
    if not df.empty:
        logger.info("  ✓ Funding rate: OKX")
        return df
    
    df = fetch_bybit_funding_rate()
    if not df.empty:
        logger.info("  ✓ Funding rate: Bybit")
        return df
    
    logger.warning("  ✗ Funding rate: all sources failed")
    return pd.DataFrame(columns=["date", "fundingRate"])


def fetch_open_interest_with_fallback() -> Optional[float]:
    """Try Binance → OKX → Bybit for open interest."""
    oi = fetch_binance_open_interest()
    if oi is not None:
        logger.info(f"  ✓ OI: Binance ({oi:,.0f})")
        return oi
    
    oi = fetch_okx_open_interest()
    if oi is not None:
        logger.info(f"  ✓ OI: OKX ({oi:,.0f})")
        return oi
    
    oi = fetch_bybit_open_interest()
    if oi is not None:
        logger.info(f"  ✓ OI: Bybit ({oi:,.0f})")
        return oi
    
    logger.warning("  ✗ OI: all sources failed")
    return None


# ============================================================
# RSI CALCULATION
# ============================================================

def calculate_rsi(closes: list, period: int = 14) -> float:
    """Calculate RSI from close prices."""
    if not closes or len(closes) < period + 1:
        return 50.0
    
    try:
        closes = [float(x) for x in closes if x is not None]
    except:
        return 50.0
    
    if len(closes) < period + 1:
        return 50.0
    
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    
    avg_gain = float(np.mean(gains[-period:]))
    avg_loss = float(np.mean(losses[-period:])) or 1e-10
    
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    
    return round(rsi, 2)


def fetch_rsi_multi_timeframe(symbol: str = "BTCUSDT") -> dict:
    """Fetch RSI for multiple timeframes with fallbacks."""
    result = {"rsi_1d": None, "rsi_2h": None, "rsi_1d_7": None, "source": None}
    
    # Try Binance SPOT first
    closes_1d = fetch_binance_klines(symbol, "1d", 50)
    if closes_1d and len(closes_1d) >= 15:
        result["rsi_1d"] = calculate_rsi(closes_1d, 14)
        result["rsi_1d_7"] = calculate_rsi(closes_1d, 7)
        result["source"] = "binance"
        logger.info(f"  ✓ RSI 1D: {result['rsi_1d']:.1f} (Binance)")
    
    closes_2h = fetch_binance_klines(symbol, "2h", 50)
    if closes_2h and len(closes_2h) >= 15:
        result["rsi_2h"] = calculate_rsi(closes_2h, 14)
        logger.info(f"  ✓ RSI 2H: {result['rsi_2h']:.1f} (Binance)")
    
    # Fallback to Yahoo
    if result["rsi_1d"] is None:
        yahoo_symbol = "BTC-USD" if "BTC" in symbol else "ETH-USD"
        yahoo_rsi = fetch_yahoo_rsi(yahoo_symbol)
        if yahoo_rsi["rsi_1d"] is not None:
            result.update(yahoo_rsi)
    
    return result


def fetch_rsi_with_fallback() -> dict:
    """Fetch RSI for BTC and ETH."""
    logger.info("📊 Fetching RSI...")
    
    btc_rsi = fetch_rsi_multi_timeframe("BTCUSDT")
    eth_rsi = fetch_rsi_multi_timeframe("ETHUSDT")
    
    return {"btc": btc_rsi, "eth": eth_rsi}


# ============================================================
# YAHOO MACRO DATA
# ============================================================

def fetch_yahoo_series(tickers: dict, period: str = "6mo") -> pd.DataFrame:
    """Fetch macro data from Yahoo Finance."""
    try:
        import yfinance as yf
        
        frames = {}
        for name, ticker in tickers.items():
            try:
                data = yf.download(ticker, period=period, progress=False)
                if not data.empty:
                    if isinstance(data.columns, pd.MultiIndex):
                        data.columns = data.columns.get_level_values(0)
                    frames[name] = data["Close"]
            except Exception as e:
                logger.debug(f"Yahoo {name} failed: {e}")
        
        if not frames:
            return pd.DataFrame()
        
        df = pd.DataFrame(frames)
        df.index = pd.to_datetime(df.index).date
        df.index.name = "date"
        df = df.ffill()
        
        return df.reset_index()
        
    except Exception as e:
        logger.warning(f"Yahoo macro failed: {e}")
        return pd.DataFrame()


# ============================================================
# FRED DATA
# ============================================================

def fetch_fred_series(series_ids: dict, observation_start: str = None) -> pd.DataFrame:
    """Fetch FRED economic data series."""
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        logger.info("  ○ FRED_API_KEY not set, skipping")
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
                logger.debug(f"FRED {name} failed: {e}")
        
        if not frames:
            return pd.DataFrame()
        
        df = pd.DataFrame(frames)
        df.index = pd.to_datetime(df.index).date
        df.index.name = "date"
        df = df.ffill()
        
        return df.reset_index()
        
    except ImportError:
        logger.info("  ○ fredapi not installed")
        return pd.DataFrame()
    except Exception as e:
        logger.warning(f"FRED failed: {e}")
        return pd.DataFrame()


# ============================================================
# MAIN PIPELINE
# ============================================================

def fetch_all_data() -> dict:
    """Fetch all data sources. Target: 90%+ quality."""
    logger.info("Fetching data from all sources...")
    
    result = {
        "price": None,
        "funding": None,
        "open_interest": None,
        "global": None,
        "market_cap_history": None,
        "fear_greed": None,
        "rsi": None,
        "yahoo": None,
        "fred": None,
        "quality": {
            "completeness": 1.0,
            "sources_available": 0,
            "sources_total": 9,
            "failed_sources": []
        },
        "fetch_time": datetime.utcnow().isoformat(),
    }
    
    sources_ok = 0
    failed_sources = []
    
    # 1. BTC Price
    logger.info("  [1/9] BTC price...")
    price_df = fetch_btc_price_yahoo()
    if price_df.empty:
        logger.info("  → Trying CoinGecko fallback...")
        time.sleep(1)
        price_df = fetch_btc_price_coingecko()
    
    if not price_df.empty:
        result["price"] = price_df
        sources_ok += 1
        last_price = float(price_df["close"].iloc[-1])
        logger.info(f"  ✓ BTC price: {len(price_df)} days, last=${last_price:,.0f}")
    else:
        failed_sources.append("BTC Price")
    
    # 2. Funding rate
    logger.info("  [2/9] Funding rate...")
    result["funding"] = fetch_funding_rate_with_fallback()
    if not result["funding"].empty:
        sources_ok += 1
    else:
        failed_sources.append("Funding")
    
    # 3. Open interest
    logger.info("  [3/9] Open interest...")
    result["open_interest"] = fetch_open_interest_with_fallback()
    if result["open_interest"] is not None:
        sources_ok += 1
    else:
        failed_sources.append("OI")
    
    time.sleep(0.5)
    
    # 4. CoinGecko global
    logger.info("  [4/9] CoinGecko global...")
    result["global"] = fetch_coingecko_global()
    if result["global"]["total_market_cap_usd"] is not None:
        sources_ok += 1
        logger.info(f"  ✓ TMC=${result['global']['total_market_cap_usd']/1e12:.2f}T, "
                    f"BTC.D={result['global']['btc_dominance']:.1f}%")
    else:
        failed_sources.append("CoinGecko")
    
    time.sleep(1)
    
    # 5. Market cap history
    logger.info("  [5/9] Market cap history...")
    result["market_cap_history"] = fetch_coingecko_market_cap_history(90)
    if not result["market_cap_history"].empty:
        sources_ok += 1
        logger.info(f"  ✓ MCap history: {len(result['market_cap_history'])} days")
    else:
        failed_sources.append("MCap")
    
    # 6. Fear & Greed
    logger.info("  [6/9] Fear & Greed...")
    result["fear_greed"] = fetch_fear_greed()
    if not result["fear_greed"].empty:
        sources_ok += 1
        fg_now = int(result["fear_greed"].iloc[0]["fear_greed"])
        fg_class = result["fear_greed"].iloc[0]["classification"]
        logger.info(f"  ✓ Fear & Greed: {fg_now} ({fg_class})")
    else:
        failed_sources.append("F&G")
    
    # 7. RSI
    logger.info("  [7/9] RSI...")
    result["rsi"] = fetch_rsi_with_fallback()
    btc_rsi = result["rsi"].get("btc", {})
    if btc_rsi.get("rsi_1d") is not None:
        sources_ok += 1
    else:
        failed_sources.append("RSI")
    
    # 8. Yahoo macro
    logger.info("  [8/9] Yahoo macro...")
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
    
    # 9. FRED
    logger.info("  [9/9] FRED...")
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
    
    # Quality
    result["quality"]["sources_available"] = sources_ok
    result["quality"]["completeness"] = round(sources_ok / result["quality"]["sources_total"], 2)
    result["quality"]["failed_sources"] = failed_sources
    
    logger.info("=" * 50)
    logger.info(f"DATA: {sources_ok}/{result['quality']['sources_total']} sources OK "
                f"({result['quality']['completeness']:.0%})")
    if failed_sources:
        logger.warning(f"FAILED: {', '.join(failed_sources)}")
    logger.info("=" * 50)
    
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = fetch_all_data()
    print(f"\n✅ Pipeline test OK - Quality: {data['quality']['completeness']:.0%}")
