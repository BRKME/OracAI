#!/usr/bin/env python3
"""
Market Regime Engine v5.3 — Comprehensive Backtest

Tests:
1. Regime accuracy (BULL/BEAR/TRANSITION vs actual price moves)
2. Trading signal P&L (BUY/SELL vs HODL)
3. Risk warnings (TAIL/CRISIS before crashes)
4. Bottom/Top timing accuracy

Period: 5 years
Data: BTC price, RSI (calculated), Fear & Greed (API)
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
import math

import pandas as pd
import numpy as np
import yfinance as yf
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════

def load_btc_data(days: int = 1825) -> pd.DataFrame:
    """Load BTC price history from local CSV or Yahoo Finance."""
    logger.info(f"Loading BTC data for {days} days...")
    
    # Try local CSV first
    import os
    csv_path = os.path.join(os.path.dirname(__file__), "data", "btc_5y.csv")
    
    if os.path.exists(csv_path):
        btc = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        btc.columns = [c.lower().replace(' ', '_') for c in btc.columns]
        if 'close' not in btc.columns and len(btc.columns) == 1:
            btc.columns = ['close']
        btc = btc.tail(days)
        logger.info(f"Loaded {len(btc)} days from local CSV")
        logger.info(f"Date range: {btc.index[0]} to {btc.index[-1]}")
        return btc
    
    # Fallback to yfinance
    end = datetime.now()
    start = end - timedelta(days=days + 50)
    
    btc = yf.download("BTC-USD", start=start, end=end, progress=False)
    
    if isinstance(btc.columns, pd.MultiIndex):
        btc.columns = btc.columns.get_level_values(0)
    
    btc.columns = [c.lower().replace(' ', '_') for c in btc.columns]
    
    if 'close' not in btc.columns and 'adj_close' in btc.columns:
        btc['close'] = btc['adj_close']
    
    logger.info(f"Loaded {len(btc)} days of BTC data")
    logger.info(f"Date range: {btc.index[0]} to {btc.index[-1]}")
    
    return btc


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI."""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def load_fear_greed_history() -> Dict[str, int]:
    """Load Fear & Greed Index history from API or generate synthetic."""
    logger.info("Loading Fear & Greed history...")
    
    try:
        url = "https://api.alternative.me/fng/?limit=1825&format=json"
        resp = requests.get(url, timeout=10)
        data = resp.json().get('data', [])
        
        fg_dict = {}
        for item in data:
            timestamp = int(item['timestamp'])
            date = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d')
            fg_dict[date] = int(item['value'])
        
        if len(fg_dict) > 100:
            logger.info(f"Loaded {len(fg_dict)} days of Fear & Greed data from API")
            return fg_dict
    except Exception as e:
        logger.warning(f"F&G API unavailable: {e}")
    
    # Generate synthetic F&G from price data
    logger.info("Generating synthetic Fear & Greed from price data...")
    return {}


def generate_synthetic_fg(df: pd.DataFrame) -> Dict[str, int]:
    """Generate synthetic Fear & Greed index from price + RSI + volatility."""
    fg_dict = {}
    rsi = calculate_rsi(df['close'], 14)
    returns_30d = df['close'].pct_change(30) * 100
    vol_20d = df['close'].pct_change().rolling(20).std() * 100
    
    for i in range(50, len(df)):
        date_str = df.index[i].strftime('%Y-%m-%d')
        r = rsi.iloc[i] if not pd.isna(rsi.iloc[i]) else 50
        ret = returns_30d.iloc[i] if not pd.isna(returns_30d.iloc[i]) else 0
        vol = vol_20d.iloc[i] if not pd.isna(vol_20d.iloc[i]) else 2.5
        
        # RSI component (0-100): RSI maps almost directly
        rsi_component = r * 0.4
        
        # Momentum component: +30% in 30d → greed, -30% → fear
        mom_component = max(0, min(100, 50 + ret * 1.5)) * 0.35
        
        # Volatility component: high vol → fear
        vol_component = max(0, min(100, 100 - vol * 15)) * 0.25
        
        fg = int(max(1, min(99, rsi_component + mom_component + vol_component)))
        fg_dict[date_str] = fg
    
    logger.info(f"Generated {len(fg_dict)} days of synthetic Fear & Greed")
    return fg_dict


# ══════════════════════════════════════════════════════════════════
# REGIME DETECTION (simplified from engine.py)
# ══════════════════════════════════════════════════════════════════

def detect_regime(row: pd.Series, prev_rows: pd.DataFrame, fg_value: int = 50) -> Dict:
    """
    Simplified regime detection based on engine.py logic.
    
    Returns: {regime, probabilities, confidence, risk_state, direction}
    """
    close = row['close']
    rsi = row.get('rsi', 50)
    
    # Calculate momentum indicators
    if len(prev_rows) >= 20:
        ma20 = prev_rows['close'].tail(20).mean()
        ma50 = prev_rows['close'].tail(50).mean() if len(prev_rows) >= 50 else ma20
        volatility = prev_rows['close'].tail(20).pct_change().std() * 100
        returns_7d = (close / prev_rows['close'].iloc[-7] - 1) * 100 if len(prev_rows) >= 7 else 0
        returns_30d = (close / prev_rows['close'].iloc[-30] - 1) * 100 if len(prev_rows) >= 30 else 0
    else:
        ma20 = close
        ma50 = close
        volatility = 2.0
        returns_7d = 0
        returns_30d = 0
    
    # Normalize Fear & Greed to [-1, 1]
    fg_norm = (fg_value - 50) / 50  # -1 = Extreme Fear, +1 = Extreme Greed
    
    # Calculate probabilities
    prob_bull = 0.25
    prob_bear = 0.25
    prob_range = 0.25
    prob_trans = 0.25
    
    # Price vs MAs
    if close > ma20 * 1.05 and close > ma50 * 1.1:
        prob_bull += 0.25
        prob_bear -= 0.15
    elif close < ma20 * 0.95 and close < ma50 * 0.9:
        prob_bear += 0.25
        prob_bull -= 0.15
    
    # Momentum
    if returns_7d > 10:
        prob_bull += 0.15
    elif returns_7d < -10:
        prob_bear += 0.15
    
    if returns_30d > 20:
        prob_bull += 0.2
    elif returns_30d < -20:
        prob_bear += 0.2
    
    # RSI
    if rsi < 30:
        prob_trans += 0.15  # Potential reversal
        prob_bear += 0.1
    elif rsi > 70:
        prob_trans += 0.15
        prob_bull += 0.1
    elif 40 <= rsi <= 60:
        prob_range += 0.1
    
    # Fear & Greed
    if fg_value < 25:
        prob_bear += 0.15
        prob_trans += 0.1
    elif fg_value > 75:
        prob_bull += 0.15
        prob_trans += 0.1
    
    # Volatility
    vol_z = (volatility - 2.5) / 1.5  # Normalize around typical 2.5%
    if vol_z > 2:
        prob_trans += 0.2
        prob_range -= 0.1
    elif vol_z < -1:
        prob_range += 0.15
    
    # Normalize probabilities
    total = prob_bull + prob_bear + prob_range + prob_trans
    prob_bull /= total
    prob_bear /= total
    prob_range /= total
    prob_trans /= total
    
    # Determine regime
    probs = {'BULL': prob_bull, 'BEAR': prob_bear, 'RANGE': prob_range, 'TRANSITION': prob_trans}
    regime = max(probs, key=probs.get)
    
    # Calculate confidence (entropy-based)
    entropy = -sum(p * math.log(p + 1e-10) for p in probs.values())
    max_entropy = math.log(4)
    confidence = 1 - (entropy / max_entropy)
    
    # Determine direction
    if returns_7d > 5:
        direction = returns_7d / 20  # Normalize to roughly [-1, 1]
    elif returns_7d < -5:
        direction = returns_7d / 20
    else:
        direction = fg_norm * 0.3 + (rsi - 50) / 100
    
    direction = max(-1, min(1, direction))
    
    # Determine risk state
    if vol_z > 2.0 or (fg_value < 15 and volatility > 3.5):
        risk_state = "CRISIS"
    elif fg_value < 20 or vol_z > 1.2:
        risk_state = "TAIL"
    elif vol_z > 0.3 or abs(returns_7d) > 8:
        risk_state = "ELEVATED"
    else:
        risk_state = "NORMAL"
    
    # Bottom/Top proximity — continuous calculation using all signals
    bottom_prox = prob_bear * 0.4 + prob_trans * 0.2 + prob_range * 0.15
    top_prox = prob_bull * 0.4 + prob_trans * 0.2 + prob_range * 0.15
    
    # Directional pressure
    if direction < 0:
        bottom_prox += abs(direction) * 0.25
        top_prox -= abs(direction) * 0.15
    else:
        top_prox += direction * 0.25
        bottom_prox -= direction * 0.15
    
    # RSI continuous
    if rsi < 50:
        rsi_factor = (50 - rsi) / 50.0
        bottom_prox += rsi_factor * 0.3
        top_prox -= rsi_factor * 0.15
    else:
        rsi_factor = (rsi - 50) / 50.0
        top_prox += rsi_factor * 0.3
        bottom_prox -= rsi_factor * 0.15
    
    # Fear & Greed continuous
    if fg_value < 50:
        fg_factor = (50 - fg_value) / 50.0
        bottom_prox += fg_factor * 0.15
        top_prox -= fg_factor * 0.05
    else:
        fg_factor = (fg_value - 50) / 50.0
        top_prox += fg_factor * 0.15
        bottom_prox -= fg_factor * 0.05
    
    bottom_prox = max(0.05, min(0.95, bottom_prox))
    top_prox = max(0.05, min(0.95, top_prox))
    
    return {
        'regime': regime,
        'probabilities': probs,
        'confidence': confidence,
        'risk_state': risk_state,
        'direction': direction,
        'bottom_prox': bottom_prox,
        'top_prox': top_prox,
        'rsi': rsi,
        'fg': fg_value,
        'volatility': volatility,
        'returns_7d': returns_7d
    }


def get_target_position(regime: str, confidence: float, direction: float, 
                        risk_state: str, rsi: float, bottom_prox: float, top_prox: float) -> float:
    """
    HODL-biased position sizing.
    
    Default: 90%. Only reduce on real danger signals.
    BTC long-term uptrend → being out of market is the biggest risk.
    """
    # Strong default: 90% invested
    target = 0.90
    
    # Only reduce position on STRONG combined signals
    # Top signal: need extreme RSI + high top_prox + confidence
    if rsi > 78 and top_prox > 0.70 and confidence > 0.20:
        target = 0.50  # Confident top
    elif rsi > 82 and top_prox > 0.75:
        target = 0.40  # Very confident top
    
    # Bear regime: reduce only if confidence is high
    if regime == "BEAR" and confidence > 0.30 and rsi > 40:
        target = min(target, 0.60)
    
    # Bull bottom: max long
    if bottom_prox > 0.65 or rsi < 28:
        target = 1.00
    
    # Risk overrides — only real danger
    if risk_state == "CRISIS":
        target = 0.25
    elif risk_state == "TAIL" and rsi > 50:
        target = min(target, 0.55)
    
    # Snap to 5% steps
    target = round(target * 20) / 20
    
    return max(0.20, min(1.0, target))


# ══════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════

@dataclass
class BacktestMetrics:
    # P&L
    total_return: float
    hodl_return: float
    alpha: float
    sharpe_ratio: float
    max_drawdown: float
    
    # Regime accuracy
    regime_accuracy: float  # % of correct regime calls
    bull_accuracy: float
    bear_accuracy: float
    
    # Signal quality
    buy_win_rate: float  # % of buys followed by +5% in 7d
    sell_win_rate: float  # % of sells followed by -5% in 7d
    
    # Risk warnings
    tail_before_crash: float  # % of >10% drops preceded by TAIL/CRISIS
    crisis_false_positive: float  # % of CRISIS not followed by crash
    
    # Timing
    bottom_accuracy: float  # Bottom signals within 10% of actual lows
    top_accuracy: float
    
    # Stats
    total_trades: int
    total_days: int
    trades_list: List[Dict]


def run_backtest(df: pd.DataFrame, fg_data: Dict[str, int]) -> BacktestMetrics:
    """Run comprehensive backtest."""
    logger.info("Running backtest...")
    
    # Add RSI
    df['rsi'] = calculate_rsi(df['close'], 14)
    
    # 90-day rolling high for drawdown defender
    df['rolling_high_90'] = df['close'].rolling(90, min_periods=20).max()
    
    # Remove warmup period
    df = df.dropna().copy()
    df = df.tail(1825)  # Last 5 years
    
    # Track state — start 90% invested (HODL bias)
    initial_capital = 100000
    position = 0.90
    cash = initial_capital * 0.10
    btc_held = (initial_capital * 0.90) / df.iloc[50]['close']
    entry_price = df.iloc[50]['close']
    
    trades = []
    equity_curve = []
    regime_calls = []
    risk_warnings = []
    
    for i in range(50, len(df)):
        row = df.iloc[i]
        prev_rows = df.iloc[:i]
        date = df.index[i]
        date_str = date.strftime('%Y-%m-%d')
        
        # Get Fear & Greed
        fg = fg_data.get(date_str, 50)
        
        # Detect regime
        result = detect_regime(row, prev_rows, fg)
        regime = result['regime']
        confidence = result['confidence']
        risk_state = result['risk_state']
        direction = result['direction']
        rsi = result['rsi']
        bottom_prox = result['bottom_prox']
        top_prox = result['top_prox']
        
        price = row['close']
        
        # Get target position
        target = get_target_position(regime, confidence, direction, risk_state, 
                                     rsi, bottom_prox, top_prox)
        
        # Drawdown defender: if BTC dropped from 90d high, reduce
        # BUT lift defender if strong bottom signal appears (RSI <30 or bottom_prox high)
        rolling_high = row.get('rolling_high_90', price)
        dd_from_high = (price / rolling_high - 1) * 100 if rolling_high > 0 else 0
        strong_bottom = (rsi < 30) or (bottom_prox > 0.70)
        
        if not strong_bottom:
            if dd_from_high < -25:
                target = min(target, 0.30)
            elif dd_from_high < -15:
                target = min(target, 0.55)
        
        # Current equity and position
        equity = cash + btc_held * price
        current_pos = (btc_held * price) / equity if equity > 0 else 0
        
        # Determine signal for tracking
        if target > current_pos + 0.05:
            signal = "BUY"
        elif target < current_pos - 0.05:
            signal = "SELL"
        else:
            signal = "HOLD"
        
        # Track for analysis
        regime_calls.append({
            'date': date,
            'regime': regime,
            'confidence': confidence,
            'risk_state': risk_state,
            'direction': direction,
            'price': price,
            'rsi': rsi,
            'fg': fg,
            'signal': signal,
            'bottom_prox': bottom_prox,
            'top_prox': top_prox
        })
        
        if risk_state in ('TAIL', 'CRISIS'):
            risk_warnings.append({
                'date': date,
                'risk_state': risk_state,
                'price': price
            })
        
        # Rebalance toward target (only if delta > 20% — strict, no churn)
        delta = target - current_pos
        if abs(delta) > 0.20:
            target_btc_value = equity * target
            current_btc_value = btc_held * price
            trade_value = target_btc_value - current_btc_value
            
            if trade_value > 0:
                # Buy more BTC
                buy_amount = min(trade_value, cash)
                btc_held += buy_amount / price
                cash -= buy_amount
                entry_price = price
                
                trades.append({
                    'date': date,
                    'action': 'BUY',
                    'price': price,
                    'signal': signal,
                    'regime': regime,
                    'confidence': confidence,
                    'target_pos': f"{int(target*100)}%"
                })
            else:
                # Sell some BTC
                sell_btc = min(abs(trade_value) / price, btc_held)
                cash += sell_btc * price
                btc_held -= sell_btc
                
                trades.append({
                    'date': date,
                    'action': 'SELL',
                    'price': price,
                    'signal': signal,
                    'regime': regime,
                    'confidence': confidence,
                    'pnl': (price - entry_price) / entry_price * 100 if entry_price > 0 else 0,
                    'target_pos': f"{int(target*100)}%"
                })
        
        # Track equity
        equity = cash + btc_held * price
        equity_curve.append({'date': date, 'equity': equity, 'price': price})
    
    # Calculate metrics
    df_equity = pd.DataFrame(equity_curve)
    
    # P&L
    final_equity = df_equity['equity'].iloc[-1]
    total_return = (final_equity / 100000 - 1) * 100
    
    hodl_return = (df_equity['price'].iloc[-1] / df_equity['price'].iloc[0] - 1) * 100
    alpha = total_return - hodl_return
    
    # Sharpe (simplified)
    daily_returns = df_equity['equity'].pct_change().dropna()
    if len(daily_returns) > 0 and daily_returns.std() > 0:
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(365)
    else:
        sharpe = 0
    
    # Max Drawdown
    peak = df_equity['equity'].expanding(min_periods=1).max()
    drawdown = (df_equity['equity'] - peak) / peak * 100
    max_dd = drawdown.min()
    
    # Regime Accuracy
    # Check if regime prediction matched actual price move in next 7 days
    correct_calls = 0
    bull_correct = 0
    bull_total = 0
    bear_correct = 0
    bear_total = 0
    
    for i, call in enumerate(regime_calls[:-7]):
        future_price = regime_calls[i + 7]['price']
        price_change = (future_price / call['price'] - 1) * 100
        
        if call['regime'] == 'BULL':
            bull_total += 1
            if price_change > 0:
                bull_correct += 1
                correct_calls += 1
        elif call['regime'] == 'BEAR':
            bear_total += 1
            if price_change < 0:
                bear_correct += 1
                correct_calls += 1
        elif call['regime'] == 'RANGE':
            if abs(price_change) < 5:
                correct_calls += 1
        else:  # TRANSITION
            correct_calls += 0.5  # Partial credit
    
    regime_accuracy = correct_calls / len(regime_calls[:-7]) * 100 if len(regime_calls) > 7 else 0
    bull_accuracy = bull_correct / bull_total * 100 if bull_total > 0 else 0
    bear_accuracy = bear_correct / bear_total * 100 if bear_total > 0 else 0
    
    # Signal Win Rate
    buy_wins = 0
    buy_total = 0
    sell_wins = 0
    sell_total = 0
    
    for i, call in enumerate(regime_calls[:-7]):
        future_price = regime_calls[i + 7]['price']
        price_change = (future_price / call['price'] - 1) * 100
        
        if call['signal'] == 'BUY':
            buy_total += 1
            if price_change > 5:
                buy_wins += 1
        elif call['signal'] == 'SELL':
            sell_total += 1
            if price_change < -5:
                sell_wins += 1
    
    buy_win_rate = buy_wins / buy_total * 100 if buy_total > 0 else 0
    sell_win_rate = sell_wins / sell_total * 100 if sell_total > 0 else 0
    
    # Risk Warnings Analysis
    crashes = []
    for i in range(len(regime_calls) - 14):
        future_price = regime_calls[i + 14]['price']
        price_change = (future_price / regime_calls[i]['price'] - 1) * 100
        if price_change < -10:
            crashes.append({
                'date': regime_calls[i]['date'],
                'drop': price_change
            })
    
    # Check how many crashes were preceded by TAIL/CRISIS
    tail_before_crash = 0
    for crash in crashes:
        crash_date = crash['date']
        for warning in risk_warnings:
            if warning['date'] <= crash_date and (crash_date - warning['date']).days <= 7:
                tail_before_crash += 1
                break
    
    tail_before_crash_pct = tail_before_crash / len(crashes) * 100 if crashes else 0
    
    # False positives
    crisis_count = sum(1 for w in risk_warnings if w['risk_state'] == 'CRISIS')
    crisis_false_pos = (crisis_count - len(crashes)) / crisis_count * 100 if crisis_count > 0 else 0
    
    # Bottom/Top Accuracy
    # Find actual local lows (within 10% of min in 30-day window)
    prices = [c['price'] for c in regime_calls]
    
    bottom_correct = 0
    bottom_signals = 0
    top_correct = 0
    top_signals = 0
    
    for i, call in enumerate(regime_calls):
        window_start = max(0, i - 15)
        window_end = min(len(prices), i + 15)
        window_min = min(prices[window_start:window_end])
        window_max = max(prices[window_start:window_end])
        
        if call['bottom_prox'] > 0.6:
            bottom_signals += 1
            if call['price'] <= window_min * 1.1:
                bottom_correct += 1
        
        if call['top_prox'] > 0.6:
            top_signals += 1
            if call['price'] >= window_max * 0.9:
                top_correct += 1
    
    bottom_accuracy = bottom_correct / bottom_signals * 100 if bottom_signals > 0 else 0
    top_accuracy = top_correct / top_signals * 100 if top_signals > 0 else 0
    
    return BacktestMetrics(
        total_return=total_return,
        hodl_return=hodl_return,
        alpha=alpha,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        regime_accuracy=regime_accuracy,
        bull_accuracy=bull_accuracy,
        bear_accuracy=bear_accuracy,
        buy_win_rate=buy_win_rate,
        sell_win_rate=sell_win_rate,
        tail_before_crash=tail_before_crash_pct,
        crisis_false_positive=crisis_false_pos,
        bottom_accuracy=bottom_accuracy,
        top_accuracy=top_accuracy,
        total_trades=len(trades),
        total_days=len(df),
        trades_list=trades
    )


# ══════════════════════════════════════════════════════════════════
# REPORT
# ══════════════════════════════════════════════════════════════════

def print_report(metrics: BacktestMetrics):
    """Print backtest report."""
    print("\n" + "=" * 70)
    print("   MARKET REGIME ENGINE v5.8 — BACKTEST REPORT (5 YEARS)")
    print("=" * 70)
    
    print(f"\n📅 Period: {metrics.total_days} days")
    print(f"📊 Total Trades: {metrics.total_trades}")
    
    print("\n" + "-" * 70)
    print("💰 P&L PERFORMANCE")
    print("-" * 70)
    print(f"  Model Return:    {metrics.total_return:>+8.1f}%")
    print(f"  HODL Return:     {metrics.hodl_return:>+8.1f}%")
    print(f"  Alpha:           {metrics.alpha:>+8.1f}%")
    print(f"  Sharpe Ratio:    {metrics.sharpe_ratio:>8.2f}")
    print(f"  Max Drawdown:    {metrics.max_drawdown:>8.1f}%")
    
    if metrics.alpha > 0:
        print(f"\n  ✅ Model outperforms HODL by {metrics.alpha:.1f}%")
    else:
        print(f"\n  ⚠️ Model underperforms HODL by {abs(metrics.alpha):.1f}%")
    
    print("\n" + "-" * 70)
    print("🎯 REGIME ACCURACY")
    print("-" * 70)
    print(f"  Overall:         {metrics.regime_accuracy:>8.1f}%")
    print(f"  BULL calls:      {metrics.bull_accuracy:>8.1f}%")
    print(f"  BEAR calls:      {metrics.bear_accuracy:>8.1f}%")
    
    if metrics.regime_accuracy > 55:
        print(f"\n  ✅ Regime detection better than random (>55%)")
    else:
        print(f"\n  ⚠️ Regime detection near random (<55%)")
    
    print("\n" + "-" * 70)
    print("📈 SIGNAL QUALITY")
    print("-" * 70)
    print(f"  BUY win rate:    {metrics.buy_win_rate:>8.1f}%  (followed by +5% in 7d)")
    print(f"  SELL win rate:   {metrics.sell_win_rate:>8.1f}%  (followed by -5% in 7d)")
    
    if metrics.buy_win_rate > 50:
        print(f"\n  ✅ BUY signals profitable")
    else:
        print(f"\n  ⚠️ BUY signals need improvement")
    
    print("\n" + "-" * 70)
    print("⚠️ RISK WARNINGS")
    print("-" * 70)
    print(f"  TAIL/CRISIS before crash:  {metrics.tail_before_crash:>5.1f}%")
    print(f"  CRISIS false positives:    {metrics.crisis_false_positive:>5.1f}%")
    
    if metrics.tail_before_crash > 60:
        print(f"\n  ✅ Good crash prediction ({metrics.tail_before_crash:.0f}% detected)")
    else:
        print(f"\n  ⚠️ Missing crashes (only {metrics.tail_before_crash:.0f}% detected)")
    
    print("\n" + "-" * 70)
    print("🔻 BOTTOM / TOP TIMING")
    print("-" * 70)
    print(f"  Bottom accuracy: {metrics.bottom_accuracy:>8.1f}%")
    print(f"  Top accuracy:    {metrics.top_accuracy:>8.1f}%")
    
    if metrics.bottom_accuracy > 40:
        print(f"\n  ✅ Bottom detection useful")
    else:
        print(f"\n  ⚠️ Bottom detection weak")
    
    print("\n" + "-" * 70)
    print("📋 RECENT TRADES")
    print("-" * 70)
    for trade in metrics.trades_list[-10:]:
        pnl = trade.get('pnl', 0)
        pnl_str = f" | PnL: {pnl:+.1f}%" if pnl != 0 else ""
        pos_str = f" → {trade.get('target_pos', '?')}" if 'target_pos' in trade else ""
        print(f"  {trade['date'].strftime('%Y-%m-%d')} | {trade['action']:4} @ ${trade['price']:,.0f} | "
              f"{trade['regime']}{pos_str}{pnl_str}")
    
    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    
    score = 0
    issues = []
    strengths = []
    
    if metrics.alpha > 0:
        score += 2
        strengths.append(f"Alpha +{metrics.alpha:.1f}%")
    else:
        issues.append(f"Negative alpha {metrics.alpha:.1f}%")
    
    if metrics.regime_accuracy > 55:
        score += 1
        strengths.append(f"Regime accuracy {metrics.regime_accuracy:.0f}%")
    else:
        issues.append(f"Low regime accuracy {metrics.regime_accuracy:.0f}%")
    
    if metrics.buy_win_rate > 50:
        score += 1
        strengths.append(f"BUY win rate {metrics.buy_win_rate:.0f}%")
    else:
        issues.append(f"Low BUY win rate {metrics.buy_win_rate:.0f}%")
    
    if metrics.tail_before_crash > 60:
        score += 1
        strengths.append(f"Crash detection {metrics.tail_before_crash:.0f}%")
    else:
        issues.append(f"Missing crashes ({100-metrics.tail_before_crash:.0f}% missed)")
    
    if metrics.bottom_accuracy > 40:
        score += 1
        strengths.append(f"Bottom timing {metrics.bottom_accuracy:.0f}%")
    else:
        issues.append(f"Weak bottom timing {metrics.bottom_accuracy:.0f}%")
    
    print(f"\n🏆 Overall Score: {score}/6")
    
    if strengths:
        print(f"\n✅ Strengths:")
        for s in strengths:
            print(f"   • {s}")
    
    if issues:
        print(f"\n⚠️ Areas to improve:")
        for i in issues:
            print(f"   • {i}")
    
    print("\n")


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    print("\n🚀 Starting Market Regime Engine v5.8 Backtest (5 years)...\n")
    
    # Load data - 5 years
    btc_data = load_btc_data(1825)
    fg_data = load_fear_greed_history()
    
    # If API failed, generate synthetic F&G
    if len(fg_data) < 100:
        fg_data = generate_synthetic_fg(btc_data)
    
    # Run backtest
    metrics = run_backtest(btc_data, fg_data)
    
    # Print report
    print_report(metrics)


if __name__ == "__main__":
    main()
