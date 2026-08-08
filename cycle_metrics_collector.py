"""
Cycle Metrics Collector v1.0

Собирает данные для Cycle Position Engine:
- ATH / ATL tracking
- Moving averages (50, 200)
- RSI calculation
- Volatility percentiles
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
import requests

from common import calculate_rsi

logger = logging.getLogger(__name__)


# ============================================================
# RSI CALCULATION
# ============================================================


def calculate_ma(prices: List[float], period: int) -> float:
    """Рассчитывает простую скользящую среднюю."""
    if len(prices) < period:
        return prices[-1] if prices else 0
    return sum(prices[-period:]) / period


def calculate_ma_slope(prices: List[float], period: int, lookback: int = 5) -> float:
    """
    Рассчитывает наклон MA.
    
    Returns:
        Нормализованный наклон (-1 до +1)
    """
    if len(prices) < period + lookback:
        return 0.0
    
    current_ma = calculate_ma(prices, period)
    past_ma = calculate_ma(prices[:-lookback], period)
    
    if past_ma == 0:
        return 0.0
    
    # Percentage change per day, normalized
    pct_change = (current_ma / past_ma - 1) / lookback
    
    # Normalize to -1 to +1 (±2% per day = ±1)
    normalized = max(-1, min(1, pct_change / 0.02))
    
    return round(normalized, 3)


def calculate_volatility(prices: List[float], period: int = 30) -> float:
    """
    Рассчитывает realized volatility (annualized).
    """
    if len(prices) < period + 1:
        return 0.0
    
    recent = prices[-period-1:]
    returns = [(recent[i] / recent[i-1] - 1) for i in range(1, len(recent))]
    
    if not returns:
        return 0.0
    
    import math
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
    daily_vol = math.sqrt(variance)
    
    # Annualize (crypto = 365 days)
    annual_vol = daily_vol * math.sqrt(365)
    
    return round(annual_vol, 4)


# ============================================================
# DATA FETCHING
# ============================================================

def fetch_price_history(symbol: str, days: int = 365) -> Optional[List[Dict]]:
    """
    Получает историю цен из CoinGecko.
    
    Returns:
        List of {"date": datetime, "price": float}
    """
    try:
        # Map symbol to CoinGecko ID
        symbol_map = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
        }
        coin_id = symbol_map.get(symbol.upper(), symbol.lower())
        
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        params = {
            "vs_currency": "usd",
            "days": days,
            "interval": "daily",
        }
        
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"CoinGecko API error: {resp.status_code}")
            return None
        
        data = resp.json()
        prices = data.get("prices", [])
        
        result = []
        for ts, price in prices:
            dt = datetime.fromtimestamp(ts / 1000)
            result.append({"date": dt, "price": price})
        
        return result
    
    except Exception as e:
        logger.error(f"Failed to fetch price history: {e}")
        return None


def fetch_fear_greed() -> float:
    """Получает Fear & Greed Index."""
    try:
        url = "https://api.alternative.me/fng/"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return float(data["data"][0]["value"])
    except Exception as e:
        logger.warning(f"Fear & Greed fetch failed: {e}")
    return 50.0  # Default neutral


def fetch_current_price(symbol: str) -> Optional[float]:
    """Получает текущую цену."""
    try:
        symbol_map = {"BTC": "bitcoin", "ETH": "ethereum"}
        coin_id = symbol_map.get(symbol.upper(), symbol.lower())
        
        url = f"https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": coin_id, "vs_currencies": "usd"}
        
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get(coin_id, {}).get("usd")
    except Exception as e:
        logger.warning(f"Price fetch failed: {e}")
    return None


# ============================================================
# METRICS BUILDER
# ============================================================

from cycle_position_engine import CycleMetrics


def build_cycle_metrics(symbol: str, price_history: Optional[List[Dict]] = None) -> Optional[CycleMetrics]:
    """
    Собирает все метрики для Cycle Position Engine.
    
    Args:
        symbol: "BTC" or "ETH"
        price_history: Optional pre-fetched history
    
    Returns:
        CycleMetrics or None if data unavailable
    """
    # Fetch data if not provided
    if price_history is None:
        price_history = fetch_price_history(symbol, days=365)
    
    if not price_history or len(price_history) < 200:
        logger.warning(f"Insufficient price history for {symbol}")
        return None
    
    # Extract prices
    prices = [p["price"] for p in price_history]
    current_price = prices[-1]
    
    # Calculate ATH (all-time from available data)
    ath = max(prices)
    
    # 52-week high/low
    prices_52w = prices[-365:] if len(prices) >= 365 else prices
    ath_52w = max(prices_52w)
    atl_52w = min(prices_52w)
    
    # Moving averages
    ma_50 = calculate_ma(prices, 50)
    ma_200 = calculate_ma(prices, 200)
    ma_50_slope = calculate_ma_slope(prices, 50)
    ma_200_slope = calculate_ma_slope(prices, 200)
    
    # RSI
    rsi_14 = calculate_rsi(prices, 14)
    rsi_7 = calculate_rsi(prices, 7)
    
    # Drawdowns
    drawdown_from_ath = (current_price / ath - 1) if ath > 0 else 0
    drawdown_from_52w = (current_price / ath_52w - 1) if ath_52w > 0 else 0
    
    # Rally
    rally_from_atl = (current_price / atl_52w - 1) if atl_52w > 0 else 0
    rally_from_52w_low = rally_from_atl
    
    # Volatility
    realized_vol_30d = calculate_volatility(prices, 30)
    
    # Calculate volatility percentile (where current vol ranks historically)
    vol_history = []
    for i in range(60, len(prices)):
        hist_vol = calculate_volatility(prices[:i], 30)
        vol_history.append(hist_vol)
    
    if vol_history:
        vol_percentile = sum(1 for v in vol_history if v <= realized_vol_30d) / len(vol_history)
    else:
        vol_percentile = 0.5
    
    # Fear & Greed
    fear_greed = fetch_fear_greed()
    
    # Volume ratio (simplified - use 1.0 if not available)
    volume_ratio = 1.0
    
    return CycleMetrics(
        current_price=current_price,
        ath=ath,
        atl_52w=atl_52w,
        ath_52w=ath_52w,
        ma_50=ma_50,
        ma_200=ma_200,
        ma_50_slope=ma_50_slope,
        ma_200_slope=ma_200_slope,
        rsi_14=rsi_14,
        rsi_7=rsi_7,
        drawdown_from_ath=drawdown_from_ath,
        drawdown_from_52w=drawdown_from_52w,
        rally_from_atl=rally_from_atl,
        rally_from_52w_low=rally_from_52w_low,
        realized_vol_30d=realized_vol_30d,
        vol_percentile=vol_percentile,
        fear_greed=fear_greed,
        volume_ratio=volume_ratio,
    )


# ============================================================
# CONVENIENCE FUNCTION
# ============================================================

def get_cycle_position(symbol: str):
    """
    Convenience function: получает CyclePosition для символа.
    
    Returns:
        (CycleMetrics, CyclePosition) or (None, None)
    """
    from cycle_position_engine import CyclePositionEngine
    
    metrics = build_cycle_metrics(symbol)
    if metrics is None:
        return None, None
    
    engine = CyclePositionEngine()
    position = engine.analyze(metrics)
    
    return metrics, position


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("Testing Cycle Metrics Collector...")
    print("=" * 50)
    
    for symbol in ["BTC", "ETH"]:
        print(f"\n{symbol}:")
        metrics, position = get_cycle_position(symbol)
        
        if metrics and position:
            print(f"  Price: ${metrics.current_price:,.0f}")
            print(f"  ATH: ${metrics.ath:,.0f} ({metrics.drawdown_from_ath:.1%})")
            print(f"  200MA: ${metrics.ma_200:,.0f}")
            print(f"  RSI: {metrics.rsi_14:.1f}")
            print(f"  Fear&Greed: {metrics.fear_greed:.0f}")
            print(f"  Volatility: {metrics.realized_vol_30d:.1%} (p{metrics.vol_percentile:.0%})")
            print()
            print(f"  Phase: {position.phase.value}")
            print(f"  Cycle Position: {position.cycle_position:.0f}/100")
            print(f"  Bottom Proximity: {position.bottom_proximity:.0%}")
            print(f"  Top Proximity: {position.top_proximity:.0%}")
            print(f"  Signal: {position.bottom_top_signal.value}")
            print(f"  Action: {position.action.value} (conf: {position.action_confidence:.0%})")
            print(f"  Reasons:")
            for r in position.reasons:
                print(f"    • {r}")
        else:
            print("  Failed to get data")
