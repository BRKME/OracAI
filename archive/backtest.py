"""
Asset Allocation Model Backtest

Цель: проверить текущую модель на истории и найти где она ошибается.

Проблема (гипотеза): модель говорит STRONG_SELL на дне, что контр-продуктивно.

Метрики:
- Total Return vs Buy & Hold
- Sharpe Ratio
- Max Drawdown
- Win Rate (правильных сигналов)
- Timing Analysis (продажи на дне vs на вершине)
"""

import logging
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

import pandas as pd
import numpy as np

try:
    import yfinance as yf
    import ta
except ImportError:
    print("Installing required packages...")
    import subprocess
    subprocess.check_call(['pip', 'install', 'yfinance', 'ta', '--quiet'])
    import yfinance as yf
    import ta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    date: datetime
    action: str  # BUY, SELL, STRONG_BUY, STRONG_SELL
    price: float
    size_pct: float
    regime: str
    confidence: float
    momentum: float
    rsi: float
    reason: str


@dataclass
class BacktestResult:
    total_return: float
    buy_hold_return: float
    alpha: float
    sharpe_ratio: float
    max_drawdown: float
    trades: List[Trade]
    equity_curve: pd.Series
    
    # Timing analysis
    sells_at_bottom_pct: float  # % of sells within 10% of local bottom
    sells_at_top_pct: float     # % of sells within 10% of local top
    buys_at_bottom_pct: float   # % of buys within 10% of local bottom
    buys_at_top_pct: float      # % of buys within 10% of local top


# ══════════════════════════════════════════════════════════════════
# SIMPLIFIED REGIME DETECTION (for backtest)
# ══════════════════════════════════════════════════════════════════

def calculate_regime(df: pd.DataFrame, i: int) -> Dict:
    """
    Simplified regime detection based on engine.py logic.
    Returns regime, confidence, momentum, risk_level.
    """
    if i < 50:
        return {
            'regime': 'TRANSITION',
            'confidence': 0.3,
            'momentum': 0,
            'risk_level': 0,
            'tail_risk': False
        }
    
    close = df['Close'].iloc[:i+1]
    high = df['High'].iloc[:i+1]
    low = df['Low'].iloc[:i+1]
    volume = df['Volume'].iloc[:i+1]
    
    # EMAs
    ema20 = close.ewm(span=20).mean().iloc[-1]
    ema50 = close.ewm(span=50).mean().iloc[-1]
    ema200 = close.ewm(span=200).mean().iloc[-1] if len(close) >= 200 else ema50
    
    price = close.iloc[-1]
    
    # RSI
    rsi = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
    
    # ATR for volatility
    atr = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range().iloc[-1]
    atr_pct = atr / price * 100
    
    # Momentum (7d change)
    momentum = (price / close.iloc[-8] - 1) if i >= 8 else 0
    
    # Volume trend
    vol_ma = volume.rolling(20).mean().iloc[-1]
    vol_ratio = volume.iloc[-1] / vol_ma if vol_ma > 0 else 1
    
    # ══════════════════════════════════════════════════════
    # REGIME SCORING (simplified from engine.py)
    # ══════════════════════════════════════════════════════
    score = 0
    
    # Trend structure
    if price > ema20 > ema50:
        score += 2
    elif price < ema20 < ema50:
        score -= 2
    elif price > ema20:
        score += 1
    else:
        score -= 1
    
    # Momentum
    if momentum > 0.08:
        score += 2
    elif momentum > 0.03:
        score += 1
    elif momentum < -0.08:
        score -= 2
    elif momentum < -0.03:
        score -= 1
    
    # RSI
    if rsi > 60:
        score += 1
    elif rsi < 40:
        score -= 1
    
    # 200 EMA (long-term trend)
    if len(close) >= 200:
        if price > ema200:
            score += 1
        else:
            score -= 1
    
    # Determine regime
    if score >= 4:
        regime = "BULL"
    elif score >= 2:
        regime = "BULL"  # early
    elif score <= -4:
        regime = "BEAR"
    elif score <= -2:
        regime = "BEAR"  # early
    else:
        regime = "TRANSITION" if abs(momentum) > 0.02 else "RANGE"
    
    # Confidence
    confidence = min(abs(score) * 0.12 + 0.20, 0.80)
    if regime in ["TRANSITION", "RANGE"]:
        confidence *= 0.7
    
    # Risk level
    risk_level = score / 5  # Normalize to [-1, 1]
    
    # Tail risk (extreme conditions)
    tail_risk = False
    tail_polarity = None
    
    if regime == "BEAR" and rsi < 25:
        tail_risk = True
        tail_polarity = "downside"
    elif regime == "BEAR" and momentum < -0.12:
        tail_risk = True
        tail_polarity = "downside"
    elif regime == "BULL" and rsi > 80:
        tail_risk = True
        tail_polarity = "upside"
    
    return {
        'regime': regime,
        'confidence': confidence,
        'momentum': momentum,
        'risk_level': risk_level,
        'rsi': rsi,
        'tail_risk': tail_risk,
        'tail_polarity': tail_polarity,
        'atr_pct': atr_pct
    }


# ══════════════════════════════════════════════════════════════════
# CURRENT MODEL (v1.3.1 - conservative)
# ══════════════════════════════════════════════════════════════════

def current_model_action(regime_data: Dict, last_action: str = None, 
                         last_action_date: datetime = None, 
                         current_date: datetime = None) -> Tuple[str, float, str]:
    """
    Current Asset Allocation model (v1.3.1).
    Returns: (action, size_pct, reason)
    """
    regime = regime_data['regime']
    conf = regime_data['confidence']
    mom = regime_data['momentum']
    risk = regime_data['risk_level']
    tail_risk = regime_data['tail_risk']
    tail_polarity = regime_data.get('tail_polarity')
    
    # 1. Tail risk override
    if tail_risk and tail_polarity == "downside":
        return ("STRONG_SELL", -0.50, "tail_risk_downside")
    
    # 2. Confidence gate
    if conf < 0.40:
        return ("HOLD", 0, "low_confidence")
    
    # 3. Cooldown check (simplified)
    if last_action and last_action_date and current_date:
        days_since = (current_date - last_action_date).days
        if "BUY" in last_action and "SELL" in "SELL" and days_since < 3:
            return ("HOLD", 0, "cooldown")
        if "SELL" in last_action and "BUY" in "BUY" and days_since < 7:
            return ("HOLD", 0, "cooldown")
    
    # 4. Regime logic
    if regime == "BULL":
        if conf >= 0.70 and mom > 0.05:
            return ("STRONG_BUY", 0.20, "bull_strong")
        elif conf >= 0.50 and mom > 0:
            return ("BUY", 0.10, "bull_buy")
        else:
            return ("HOLD", 0, "bull_hold")
    
    elif regime == "BEAR":
        if conf >= 0.60 and mom < -0.05:
            return ("STRONG_SELL", -0.50, "bear_strong")
        elif conf >= 0.50 and mom < 0:
            return ("SELL", -0.15, "bear_sell")
        else:
            return ("HOLD", 0, "bear_hold")
    
    elif regime == "TRANSITION":
        if risk < -0.30 and conf >= 0.50:
            return ("SELL", -0.15, "transition_risk")
        else:
            return ("HOLD", 0, "transition_wait")
    
    else:  # RANGE
        return ("HOLD", 0, "range")


# ══════════════════════════════════════════════════════════════════
# CONTRARIAN MODEL (v2 proposal - more aggressive on bottoms)
# ══════════════════════════════════════════════════════════════════

def contrarian_model_action(regime_data: Dict, last_action: str = None,
                            last_action_date: datetime = None,
                            current_date: datetime = None) -> Tuple[str, float, str]:
    """
    Contrarian model proposal:
    - On panic/bottom: DON'T sell, consider accumulating
    - On euphoria/top: SELL
    
    Returns: (action, size_pct, reason)
    """
    regime = regime_data['regime']
    conf = regime_data['confidence']
    mom = regime_data['momentum']
    risk = regime_data['risk_level']
    rsi = regime_data['rsi']
    tail_risk = regime_data['tail_risk']
    tail_polarity = regime_data.get('tail_polarity')
    
    # ══════════════════════════════════════════════════════
    # KEY DIFFERENCE: Contrarian logic on extremes
    # ══════════════════════════════════════════════════════
    
    # 1. UPSIDE tail risk (euphoria) → SELL
    if tail_risk and tail_polarity == "upside":
        return ("STRONG_SELL", -0.50, "euphoria_exit")
    
    # 2. DOWNSIDE tail risk (panic) → DON'T SELL, maybe accumulate
    if tail_risk and tail_polarity == "downside":
        if rsi < 25:
            # Extreme oversold - accumulate
            return ("BUY", 0.10, "panic_accumulate")
        else:
            # Stressed but not extreme - hold
            return ("HOLD", 0, "panic_hold")
    
    # 3. RSI extremes (even without full tail risk)
    if rsi > 75 and regime == "BULL":
        return ("SELL", -0.25, "overbought")
    
    if rsi < 30 and regime == "BEAR":
        # Oversold in bear - don't sell more
        return ("HOLD", 0, "oversold_hold")
    
    # 4. Confidence gate (slightly lower)
    if conf < 0.35:
        return ("HOLD", 0, "low_confidence")
    
    # 5. Standard regime logic (similar to current)
    if regime == "BULL":
        if conf >= 0.60 and mom > 0.05:
            return ("STRONG_BUY", 0.20, "bull_strong")
        elif conf >= 0.45 and mom > 0:
            return ("BUY", 0.10, "bull_buy")
        else:
            return ("HOLD", 0, "bull_hold")
    
    elif regime == "BEAR":
        # More cautious about selling in bear
        if conf >= 0.65 and mom < -0.08 and rsi > 40:
            # Only sell if NOT oversold
            return ("STRONG_SELL", -0.50, "bear_strong")
        elif conf >= 0.55 and mom < -0.03 and rsi > 35:
            return ("SELL", -0.15, "bear_sell")
        else:
            return ("HOLD", 0, "bear_hold")
    
    elif regime == "TRANSITION":
        if risk < -0.30 and conf >= 0.50 and rsi > 45:
            return ("SELL", -0.15, "transition_risk")
        else:
            return ("HOLD", 0, "transition_wait")
    
    else:  # RANGE
        return ("HOLD", 0, "range")


# ══════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════

def run_backtest(
    df: pd.DataFrame,
    model_func,
    initial_capital: float = 100000,
    position_pct: float = 0.5  # Start with 50% position
) -> BacktestResult:
    """
    Run backtest on historical data.
    
    Args:
        df: OHLCV DataFrame
        model_func: Action function (current_model_action or contrarian_model_action)
        initial_capital: Starting capital
        position_pct: Initial position size (0-1)
    """
    capital = initial_capital
    position = initial_capital * position_pct  # $ in BTC
    cash = initial_capital * (1 - position_pct)
    
    trades: List[Trade] = []
    equity_curve = []
    
    last_action = None
    last_action_date = None
    
    # Track local highs/lows for timing analysis
    window = 30  # 30 day window for local extremes
    
    for i in range(50, len(df)):
        current_date = df.index[i]
        price = df['Close'].iloc[i]
        
        # Calculate current equity
        current_equity = cash + position
        equity_curve.append({
            'date': current_date,
            'equity': current_equity,
            'price': price,
            'position_value': position,
            'cash': cash
        })
        
        # Get regime data
        regime_data = calculate_regime(df, i)
        
        # Get action from model
        action, size_pct, reason = model_func(
            regime_data, 
            last_action, 
            last_action_date, 
            current_date
        )
        
        # Execute action
        if action != "HOLD" and size_pct != 0:
            if "BUY" in action:
                # Buy with cash
                buy_amount = cash * abs(size_pct) * 2  # size_pct is of total, so multiply
                if buy_amount > cash:
                    buy_amount = cash
                if buy_amount > 0:
                    position += buy_amount
                    cash -= buy_amount
                    
                    trades.append(Trade(
                        date=current_date,
                        action=action,
                        price=price,
                        size_pct=size_pct,
                        regime=regime_data['regime'],
                        confidence=regime_data['confidence'],
                        momentum=regime_data['momentum'],
                        rsi=regime_data['rsi'],
                        reason=reason
                    ))
                    
                    last_action = action
                    last_action_date = current_date
            
            elif "SELL" in action:
                # Sell position
                sell_amount = position * abs(size_pct)
                if sell_amount > position:
                    sell_amount = position
                if sell_amount > 0:
                    position -= sell_amount
                    cash += sell_amount
                    
                    trades.append(Trade(
                        date=current_date,
                        action=action,
                        price=price,
                        size_pct=size_pct,
                        regime=regime_data['regime'],
                        confidence=regime_data['confidence'],
                        momentum=regime_data['momentum'],
                        rsi=regime_data['rsi'],
                        reason=reason
                    ))
                    
                    last_action = action
                    last_action_date = current_date
    
    # Final equity
    final_equity = cash + position
    
    # Create equity DataFrame
    eq_df = pd.DataFrame(equity_curve)
    eq_df.set_index('date', inplace=True)
    
    # Calculate metrics
    total_return = (final_equity / initial_capital - 1) * 100
    
    # Buy & Hold return
    buy_hold_return = (df['Close'].iloc[-1] / df['Close'].iloc[50] - 1) * 100
    
    alpha = total_return - buy_hold_return
    
    # Sharpe Ratio (simplified)
    if len(eq_df) > 1:
        daily_returns = eq_df['equity'].pct_change().dropna()
        if daily_returns.std() > 0:
            sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
        else:
            sharpe = 0
    else:
        sharpe = 0
    
    # Max Drawdown
    rolling_max = eq_df['equity'].cummax()
    drawdown = (eq_df['equity'] - rolling_max) / rolling_max
    max_drawdown = drawdown.min() * 100
    
    # Timing analysis
    sells_at_bottom, sells_at_top = analyze_sell_timing(trades, df)
    buys_at_bottom, buys_at_top = analyze_buy_timing(trades, df)
    
    return BacktestResult(
        total_return=total_return,
        buy_hold_return=buy_hold_return,
        alpha=alpha,
        sharpe_ratio=sharpe,
        max_drawdown=max_drawdown,
        trades=trades,
        equity_curve=eq_df['equity'],
        sells_at_bottom_pct=sells_at_bottom,
        sells_at_top_pct=sells_at_top,
        buys_at_bottom_pct=buys_at_bottom,
        buys_at_top_pct=buys_at_top
    )


def analyze_sell_timing(trades: List[Trade], df: pd.DataFrame, window: int = 30) -> Tuple[float, float]:
    """
    Analyze if sells happened near local bottoms or tops.
    Returns: (pct_at_bottom, pct_at_top)
    """
    sell_trades = [t for t in trades if "SELL" in t.action]
    if not sell_trades:
        return (0, 0)
    
    at_bottom = 0
    at_top = 0
    
    for trade in sell_trades:
        idx = df.index.get_loc(trade.date)
        
        # Get window around trade
        start = max(0, idx - window)
        end = min(len(df), idx + window)
        
        window_prices = df['Close'].iloc[start:end]
        local_min = window_prices.min()
        local_max = window_prices.max()
        price_range = local_max - local_min
        
        if price_range == 0:
            continue
        
        # Where in the range was the sell?
        position_in_range = (trade.price - local_min) / price_range
        
        if position_in_range < 0.20:  # Bottom 20%
            at_bottom += 1
        elif position_in_range > 0.80:  # Top 20%
            at_top += 1
    
    return (
        at_bottom / len(sell_trades) * 100,
        at_top / len(sell_trades) * 100
    )


def analyze_buy_timing(trades: List[Trade], df: pd.DataFrame, window: int = 30) -> Tuple[float, float]:
    """
    Analyze if buys happened near local bottoms or tops.
    Returns: (pct_at_bottom, pct_at_top)
    """
    buy_trades = [t for t in trades if "BUY" in t.action]
    if not buy_trades:
        return (0, 0)
    
    at_bottom = 0
    at_top = 0
    
    for trade in buy_trades:
        idx = df.index.get_loc(trade.date)
        
        start = max(0, idx - window)
        end = min(len(df), idx + window)
        
        window_prices = df['Close'].iloc[start:end]
        local_min = window_prices.min()
        local_max = window_prices.max()
        price_range = local_max - local_min
        
        if price_range == 0:
            continue
        
        position_in_range = (trade.price - local_min) / price_range
        
        if position_in_range < 0.20:
            at_bottom += 1
        elif position_in_range > 0.80:
            at_top += 1
    
    return (
        at_bottom / len(buy_trades) * 100,
        at_top / len(buy_trades) * 100
    )


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

def generate_mock_btc_data(days: int = 1000) -> pd.DataFrame:
    """
    Generate realistic BTC-like price data for backtesting.
    Simulates bull/bear cycles with volatility.
    """
    np.random.seed(42)
    
    dates = pd.date_range(end=datetime.now(), periods=days, freq='D')
    
    # Start price
    price = 20000
    prices = [price]
    
    # Simulate cycles
    cycle_length = 200
    
    for i in range(1, days):
        # Trend component (cycles)
        cycle_position = (i % cycle_length) / cycle_length
        
        if cycle_position < 0.4:
            # Bull phase
            trend = 0.002
        elif cycle_position < 0.6:
            # Top / distribution
            trend = 0.0001
        elif cycle_position < 0.85:
            # Bear phase
            trend = -0.0025
        else:
            # Bottom / accumulation
            trend = 0.0005
        
        # Volatility
        volatility = 0.03
        noise = np.random.normal(0, volatility)
        
        # Calculate new price
        price = prices[-1] * (1 + trend + noise)
        price = max(price, 10000)  # Floor
        prices.append(price)
    
    # Create OHLCV
    df = pd.DataFrame({
        'Open': prices,
        'Close': prices,
        'High': [p * (1 + abs(np.random.normal(0, 0.02))) for p in prices],
        'Low': [p * (1 - abs(np.random.normal(0, 0.02))) for p in prices],
        'Volume': [np.random.uniform(1e9, 5e9) for _ in prices]
    }, index=dates)
    
    # Smooth high/low
    df['High'] = df[['Open', 'Close', 'High']].max(axis=1)
    df['Low'] = df[['Open', 'Close', 'Low']].min(axis=1)
    
    return df


def main():
    print("=" * 60)
    print("ASSET ALLOCATION MODEL BACKTEST")
    print("=" * 60)
    
    # Try to fetch historical BTC data
    print("\nFetching BTC-USD data (3 years)...")
    
    df = None
    try:
        df = yf.download("BTC-USD", period="3y", progress=False)
        if df.empty:
            df = None
    except Exception as e:
        logger.warning(f"Could not fetch real data: {e}")
        df = None
    
    if df is None or df.empty:
        print("⚠️ Real data unavailable, using simulated BTC data...")
        df = generate_mock_btc_data(1000)
        print(f"Generated {len(df)} days of simulated data")
    else:
        print(f"Data: {df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}")
    print(f"Total days: {len(df)}")
    
    # Run backtest: CURRENT MODEL
    print("\n" + "=" * 60)
    print("MODEL 1: CURRENT (v1.3.1 Conservative)")
    print("=" * 60)
    
    result_current = run_backtest(df, current_model_action)
    
    print(f"\nTotal Return: {result_current.total_return:+.1f}%")
    print(f"Buy & Hold:   {result_current.buy_hold_return:+.1f}%")
    print(f"Alpha:        {result_current.alpha:+.1f}%")
    print(f"Sharpe Ratio: {result_current.sharpe_ratio:.2f}")
    print(f"Max Drawdown: {result_current.max_drawdown:.1f}%")
    print(f"Total Trades: {len(result_current.trades)}")
    
    print(f"\nTIMING ANALYSIS:")
    print(f"  Sells at bottom 20%: {result_current.sells_at_bottom_pct:.0f}%")
    print(f"  Sells at top 20%:    {result_current.sells_at_top_pct:.0f}%")
    print(f"  Buys at bottom 20%:  {result_current.buys_at_bottom_pct:.0f}%")
    print(f"  Buys at top 20%:     {result_current.buys_at_top_pct:.0f}%")
    
    # Show recent trades
    print(f"\nRecent Trades (last 10):")
    for trade in result_current.trades[-10:]:
        print(f"  {trade.date.strftime('%Y-%m-%d')} | {trade.action:12} @ ${trade.price:,.0f} | "
              f"RSI: {trade.rsi:.0f} | {trade.reason}")
    
    # Run backtest: CONTRARIAN MODEL
    print("\n" + "=" * 60)
    print("MODEL 2: CONTRARIAN (v2 proposal)")
    print("=" * 60)
    
    result_contrarian = run_backtest(df, contrarian_model_action)
    
    print(f"\nTotal Return: {result_contrarian.total_return:+.1f}%")
    print(f"Buy & Hold:   {result_contrarian.buy_hold_return:+.1f}%")
    print(f"Alpha:        {result_contrarian.alpha:+.1f}%")
    print(f"Sharpe Ratio: {result_contrarian.sharpe_ratio:.2f}")
    print(f"Max Drawdown: {result_contrarian.max_drawdown:.1f}%")
    print(f"Total Trades: {len(result_contrarian.trades)}")
    
    print(f"\nTIMING ANALYSIS:")
    print(f"  Sells at bottom 20%: {result_contrarian.sells_at_bottom_pct:.0f}%")
    print(f"  Sells at top 20%:    {result_contrarian.sells_at_top_pct:.0f}%")
    print(f"  Buys at bottom 20%:  {result_contrarian.buys_at_bottom_pct:.0f}%")
    print(f"  Buys at top 20%:     {result_contrarian.buys_at_top_pct:.0f}%")
    
    # Show recent trades
    print(f"\nRecent Trades (last 10):")
    for trade in result_contrarian.trades[-10:]:
        print(f"  {trade.date.strftime('%Y-%m-%d')} | {trade.action:12} @ ${trade.price:,.0f} | "
              f"RSI: {trade.rsi:.0f} | {trade.reason}")
    
    # Comparison
    print("\n" + "=" * 60)
    print("COMPARISON")
    print("=" * 60)
    
    print(f"\n{'Metric':<25} {'Current':<15} {'Contrarian':<15} {'Diff':<15}")
    print("-" * 70)
    print(f"{'Total Return':<25} {result_current.total_return:>+12.1f}%  {result_contrarian.total_return:>+12.1f}%  {result_contrarian.total_return - result_current.total_return:>+12.1f}%")
    print(f"{'Alpha vs B&H':<25} {result_current.alpha:>+12.1f}%  {result_contrarian.alpha:>+12.1f}%  {result_contrarian.alpha - result_current.alpha:>+12.1f}%")
    print(f"{'Sharpe Ratio':<25} {result_current.sharpe_ratio:>12.2f}   {result_contrarian.sharpe_ratio:>12.2f}   {result_contrarian.sharpe_ratio - result_current.sharpe_ratio:>+12.2f}")
    print(f"{'Max Drawdown':<25} {result_current.max_drawdown:>12.1f}%  {result_contrarian.max_drawdown:>12.1f}%  {result_contrarian.max_drawdown - result_current.max_drawdown:>+12.1f}%")
    print(f"{'Sells at Bottom':<25} {result_current.sells_at_bottom_pct:>12.0f}%  {result_contrarian.sells_at_bottom_pct:>12.0f}%  {result_contrarian.sells_at_bottom_pct - result_current.sells_at_bottom_pct:>+12.0f}%")
    print(f"{'Buys at Bottom':<25} {result_current.buys_at_bottom_pct:>12.0f}%  {result_contrarian.buys_at_bottom_pct:>12.0f}%  {result_contrarian.buys_at_bottom_pct - result_current.buys_at_bottom_pct:>+12.0f}%")
    
    print("\n" + "=" * 60)
    print("CONCLUSION")
    print("=" * 60)
    
    if result_contrarian.alpha > result_current.alpha:
        print(f"\n✅ Contrarian model outperforms by {result_contrarian.alpha - result_current.alpha:.1f}% alpha")
    else:
        print(f"\n⚠️ Current model outperforms by {result_current.alpha - result_contrarian.alpha:.1f}% alpha")
    
    if result_current.sells_at_bottom_pct > 30:
        print(f"⚠️ Current model sells at bottom {result_current.sells_at_bottom_pct:.0f}% of time — problematic")
    
    if result_contrarian.buys_at_bottom_pct > result_current.buys_at_bottom_pct:
        print(f"✅ Contrarian model buys more at bottoms: {result_contrarian.buys_at_bottom_pct:.0f}% vs {result_current.buys_at_bottom_pct:.0f}%")


if __name__ == "__main__":
    main()
