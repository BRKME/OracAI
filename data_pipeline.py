"""
Data Pipeline — fetches all inputs from free public APIs.

Sources:
  Price OHLCV:      Yahoo Finance (primary) + CoinGecko fallback
  Funding / OI:     Binance → OKX → Bybit (graceful degradation)
  Market data:      CoinGecko
  Sentiment:        alternative.me Fear & Greed
  Macro:            Yahoo + FRED
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

# ============================================================
# YAHOO FINANCE — PRIMARY SOURCE
# ============================================================

def fetch_yahoo_klines(symbol: str = "BTC-USD", period: str = "90d", interval: str = "1d") -> List[float]:
    try:
        data = yf.download(tickers=symbol, period=period, interval=interval,
                           progress=False, auto_adjust=True, prepost=False)
        if data.empty:
            return []
        return data["Close"].dropna().astype(float).tolist()
    except Exception as e:
        logger.warning(f"yfinance klines {symbol} {interval} failed: {e}")
        return []


def fetch_btc_price_yahoo(period: str = "1y") -> pd.DataFrame:
    try:
        data = yf.download("BTC-USD", period=period, progress=False, auto_adjust=True)
        if data.empty:
            return pd.DataFrame()

        df = data.reset_index()
        df = df.rename(columns={
            "Date": "date", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume"
        })
        df["date"] = pd.to_datetime(df["date"]).dt.date

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        df["quote_volume"] = df["volume"] * df["close"]
        return df[["date", "open", "high", "low", "close", "volume", "quote_volume"]]
    except Exception as e:
        logger.error(f"Yahoo BTC-USD failed: {e}")
        return pd.DataFrame()


# ============================================================
# COINGECKO FALLBACKS
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
            "open": "first", "high": "max", "low": "min", "close": "last"
        }).reset_index()
        for col in ["open", "high", "low", "close"]:
            daily[col] = daily[col].astype(float)
        daily["volume"] = daily["quote_volume"] = 0.0
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
        return {
            "total_market_cap_usd": data["total_market_cap"].get("usd", 0),
            "btc_dominance": data.get("market_cap_percentage", {}).get("btc", 0),
            "eth_price": None
        }
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
# BINANCE, OKX, BYBIT — FUNDING + OI
# ============================================================

def fetch_binance_funding_rate(symbol: str = "BTCUSDT", limit: int = 100) -> pd.DataFrame:
    url = "https://api.binance.com/fapi/v1/fundingRate"
    params = {"symbol": symbol, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        df = pd.DataFrame(resp.json())
        df["date"] = pd.to_datetime(df["fundingTime"], unit="ms").dt.date
        df["fundingRate"] = df["fundingRate"].astype(float)
        return df.groupby("date")["fundingRate"].mean().reset_index()
    except Exception:
        logger.info("  ○ Binance funding rate unavailable")
        return pd.DataFrame(columns=["date", "fundingRate"])


def fetch_binance_open_interest(symbol: str = "BTCUSDT") -> Optional[float]:
    url = "https://api.binance.com/fapi/v1/openInterest"
    params = {"symbol": symbol}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return float(resp.json()["openInterest"])
    except Exception:
        logger.info("  ○ Binance OI unavailable")
        return None


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
    except Exception:
        logger.info("  ○ OKX funding rate unavailable")
        return pd.DataFrame(columns=["date", "fundingRate"])


def fetch_okx_open_interest() -> Optional[float]:
    url = "https://www.okx.com/api/v5/public/open-interest"
    params = {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return float(data[0]["oi"]) if data else None
    except Exception:
        logger.info("  ○ OKX OI unavailable")
        return None


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
    except Exception:
        logger.info("  ○ Bybit funding rate unavailable")
        return pd.DataFrame(columns=["date", "fundingRate"])


def fetch_bybit_open_interest() -> Optional[float]:
    url = "https://api.bybit.com/v5/market/open-interest"
    params = {"category": "linear", "symbol": "BTCUSDT", "intervalTime": "5min", "limit": "1"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("result", {}).get("list", [])
        return float(data[0]["openInterest"]) if data else None
    except Exception:
        logger.info("  ○ Bybit OI unavailable")
        return None


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
    if avg_loss == 0: return 100.0
    if avg_gain == 0: return 0.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def fetch_rsi_multi_timeframe() -> Dict:
    result = {"btc": {"rsi_1d": None, "rsi_2h": None, "rsi_1d_7": None, "source": "unknown"}}
    try:
        closes_1d = fetch_yahoo_klines("BTC-USD", "90d", "1d")
        if len(closes_1d) >= 20:
            result["btc"]["rsi_1d"] = calculate_rsi(closes_1d, 14)
            result["btc"]["rsi_1d_7"] = calculate_rsi(closes_1d, 7)
            result["btc"]["source"] = "yfinance"
            logger.info(f"  ✓ RSI Daily: {result['btc']['rsi_1d']:.1f} (yfinance)")

        closes_2h = fetch_yahoo_klines("BTC-USD", "14d", "2h")
        if len(closes_2h) >= 20:
            result["btc"]["rsi_2h"] = calculate_rsi(closes_2h, 14)
            logger.info(f"  ✓ RSI 2h: {result['btc']['rsi_2h']:.1f} (yfinance)")
    except Exception as e:
        logger.warning(f"yfinance RSI failed: {e}")
    return result


def fetch_rsi_with_fallback() -> dict:
    logger.info("  📊 Fetching RSI...")
    rsi_data = fetch_rsi_multi_timeframe()
    return {
        "btc": rsi_data["btc"],
        "eth": {"rsi_1d": 50.0, "rsi_2h": 50.0, "rsi_1d_7": 50.0, "source": "default"}
    }


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
        frames = {name: fred.get_series(sid) for name, sid in series_ids.items()}
        df = pd.DataFrame(frames)
        df.index = pd.to_datetime(df.index).date
        df = df.reset_index().rename(columns={"index": "date"})
        return df
    except Exception as e:
        logger.warning(f"FRED failed: {e}")
        return pd.DataFrame()


# ============================================================
# MAIN PIPELINE
# ============================================================

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
    failed_sources = []

    # 1. Price
    logger.info("  [1/9] BTC price (Yahoo Finance)...")
    price_df = fetch_btc_price_yahoo(period="1y")
    if price_df.empty:
        price_df = fetch_btc_price_coingecko(days=365)
    if not price_df.empty:
        result["price"] = price_df
        sources_ok += 1
        logger.info(f"  ✓ BTC price: {len(price_df)} days, last=${price_df['close'].iloc[-1]:,.0f}")
    else:
        failed_sources.append("BTC Price")

    # 2. Funding
    logger.info("  [2/9] Funding rate...")
    result["funding"] = fetch_funding_rate_with_fallback()
    if not result["funding"].empty:
        sources_ok += 1
        logger.info(f"       {len(result['funding'])} days")
    else:
        failed_sources.append("Funding")

    # 3. OI
    logger.info("  [3/9] Open interest...")
    result["open_interest"] = fetch_open_interest_with_fallback()
    if result["open_interest"] is not None:
        sources_ok += 1
        logger.info(f"       OI: {result['open_interest']:,.0f}")
    else:
        failed_sources.append("OI")

    # 4. Global
    logger.info("  [4/9] CoinGecko global...")
    result["global"] = fetch_coingecko_global()
    if result["global"]["total_market_cap_usd"] is not None:
        sources_ok += 1
        logger.info(f"  ✓ TMC=${result['global']['total_market_cap_usd']/1e12:.2f}T, BTC.D={result['global']['btc_dominance']:.1f}%")
    else:
        failed_sources.append("CoinGecko Global")

    # 5. MCap history
    logger.info("  [5/9] Market cap history...")
    result["market_cap_history"] = fetch_coingecko_market_cap_history(days=120)
    if not result["market_cap_history"].empty:
        sources_ok += 1
        logger.info(f"  ✓ MCap history: {len(result['market_cap_history'])} days")
    else:
        failed_sources.append("MCap History")

    # 6. Fear & Greed
    logger.info("  [6/9] Fear & Greed...")
    result["fear_greed"] = fetch_fear_greed()
    if not result["fear_greed"].empty:
        sources_ok += 1
        fg = result["fear_greed"].iloc[0]["fear_greed"]
        logger.info(f"  ✓ Fear & Greed: {len(result['fear_greed'])} days, current={fg}")
    else:
        failed_sources.append("Fear & Greed")

    # 7. RSI
    logger.info("  [7/9] RSI (Daily + 2h)...")
    result["rsi"] = fetch_rsi_with_fallback()
    if result["rsi"]["btc"]["rsi_1d"] is not None:
        sources_ok += 1
        r = result["rsi"]["btc"]
        logger.info(f"  ✓ RSI: 1d={r['rsi_1d']:.1f} | 2h={r.get('rsi_2h', 'N/A')} (source: {r['source']})")
    else:
        failed_sources.append("RSI")

    # 8. Yahoo macro
    logger.info("  [8/9] Yahoo macro (DXY, SPX, Gold)...")
    result["yahoo"] = fetch_yahoo_series({"DXY": "DX-Y.NYB", "SPX": "^GSPC", "GOLD": "GC=F"})
    if not result["yahoo"].empty:
        sources_ok += 1
        logger.info(f"  ✓ Yahoo macro: {len(result['yahoo'])} days")
    else:
        failed_sources.append("Yahoo Macro")

    # 9. FRED
    logger.info("  [9/9] FRED (yields, M2)...")
    result["fred"] = fetch_fred_series({"US_10Y": "DGS10", "US_2Y": "DGS2", "M2": "M2SL"})
    if not result["fred"].empty:
        sources_ok += 1
        logger.info(f"  ✓ FRED: {len(result['fred'])} rows")
    else:
        failed_sources.append("FRED")

    # Quality
    result["quality"]["sources_available"] = sources_ok
    result["quality"]["completeness"] = round(sources_ok / 9, 2)
    result["quality"]["failed_sources"] = failed_sources

    logger.info("=" * 60)
    logger.info(f"DATA PIPELINE COMPLETE — {sources_ok}/9 sources OK ({result['quality']['completeness']:.0%})")
    if failed_sources:
        logger.info(f"FAILED: {', '.join(failed_sources)}")
    logger.info("=" * 60)

    return result


if __name__ == "__main__":
    data = fetch_all_data()
    print("✅ Pipeline test OK")
    print(f"RSI source: {data['rsi']['btc']['source']}")
