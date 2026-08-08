"""
Backtest v1.6 TREND-FOLLOWING vs v1.4.1 CONSERVATIVE
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

np.random.seed(42)

def generate_btc_cycle():
    """Generate realistic BTC 2022-2025 cycle"""
    phases = [
        ("Bear 2022", 180, 47000, 17000, 0.04),
        ("Bottom", 90, 17000, 16000, 0.02),
        ("Recovery 2023", 180, 16000, 44000, 0.03),
        ("ETF Pump", 90, 44000, 73000, 0.04),
        ("Consolidation", 120, 73000, 54000, 0.03),
        ("ATH Rally", 90, 54000, 108000, 0.05),
        ("Correction", 60, 108000, 78000, 0.04),
    ]
    
    prices = []
    for name, days, start, end, vol in phases:
        trend = np.linspace(start, end, days)
        noise = np.random.normal(0, vol * start, days)
        phase_prices = trend + noise
        prices.extend(phase_prices)
    
    return np.array(prices)


def compute_indicators(prices):
    """Compute momentum, vol_z, returns"""
    n = len(prices)
    momentum = np.zeros(n)
    vol_z = np.zeros(n)
    returns_30d = np.zeros(n)
    
    for i in range(30, n):
        ret = (prices[i] / prices[i-20] - 1)
        momentum[i] = np.clip(ret * 5, -1, 1)
        
        rets = np.diff(prices[i-30:i]) / prices[i-30:i-1]
        vol = np.std(rets) * np.sqrt(365)
        vol_z[i] = (vol - 0.6) / 0.3
        
        returns_30d[i] = prices[i] / prices[i-30] - 1
    
    return momentum, vol_z, returns_30d


def detect_regime(momentum, returns_30d):
    """Simple regime detection"""
    if momentum > 0.3 and returns_30d > 0.05:
        return "BULL"
    elif momentum < -0.3 and returns_30d < -0.05:
        return "BEAR"
    elif abs(momentum) < 0.2:
        return "RANGE"
    else:
        return "TRANSITION"


def backtest_v14(prices, momentum, vol_z, returns_30d):
    """v1.4.1 CONSERVATIVE - original params"""
    capital = 100000
    position = 0
    cash = capital
    last_trade = 0
    trades = 0
    sells_at_bottom = 0
    buys_at_top = 0
    
    # Find bottoms and tops for analysis
    bottom_20 = np.percentile(prices, 20)
    top_20 = np.percentile(prices, 80)
    
    # POSITION LIMITS
    MAX_POSITION_VALUE = capital * 1.0  # Max 100% in BTC
    
    for i in range(30, len(prices)):
        price = prices[i]
        mom = momentum[i]
        vol = vol_z[i]
        ret30 = returns_30d[i]
        regime = detect_regime(mom, ret30)
        
        conf = 0.3 + abs(mom) * 0.5
        position_value = position * price
        
        # OLD panic (sensitive)
        is_panic = (
            (mom < -0.50 and vol > 1.5) or
            (mom < -0.60 and vol > 1.0) or
            (ret30 < -0.30)
        )
        
        # OLD cooldown
        if i - last_trade < 7:
            continue
        
        # OLD logic
        if regime == "BULL" and mom > 0.3 and conf >= 0.50:
            if position_value < MAX_POSITION_VALUE and cash > 0:
                buy_amount = min(cash * 0.10, MAX_POSITION_VALUE - position_value)
                if buy_amount > 100:
                    units = buy_amount / price
                    position += units
                    cash -= buy_amount
                    last_trade = i
                    trades += 1
                    if price > top_20:
                        buys_at_top += 1
        elif regime == "BEAR" and mom < -0.3 and conf >= 0.50 and not is_panic:
            if position > 0:
                sell_units = position * 0.15
                cash += sell_units * price
                position -= sell_units
                last_trade = i
                trades += 1
                if price < bottom_20:
                    sells_at_bottom += 1
    
    final_value = cash + position * prices[-1]
    return (final_value / capital - 1) * 100, trades, sells_at_bottom


def backtest_v16(prices, momentum, vol_z, returns_30d):
    """v1.6.0 TREND-FOLLOWING - new aggressive params"""
    capital = 100000
    position = 0
    cash = capital
    last_buy = 0
    last_sell = 0
    trades = 0
    sells_at_bottom = 0
    buys_at_top = 0
    
    bottom_20 = np.percentile(prices, 20)
    top_20 = np.percentile(prices, 80)
    
    # POSITION LIMITS
    MAX_POSITION_VALUE = capital * 1.0  # Max 100% of initial capital in BTC
    
    for i in range(30, len(prices)):
        price = prices[i]
        mom = momentum[i]
        vol = vol_z[i]
        ret30 = returns_30d[i]
        regime = detect_regime(mom, ret30)
        
        conf = 0.3 + abs(mom) * 0.5
        
        # Current position value
        position_value = position * price
        
        # NEW panic (very tight)
        is_panic = mom < -0.85 and vol > 2.5
        
        # ASYMMETRIC cooldown: fast buy, slow sell
        buy_cooldown = i - last_sell < 1
        sell_cooldown = i - last_buy < 7
        
        # v1.6 TREND-FOLLOWING logic
        if regime == "BULL":
            # В бычке - покупаем, но с лимитом
            if not buy_cooldown and conf >= 0.30 and mom > -0.2:
                if position_value < MAX_POSITION_VALUE and cash > 0:
                    buy_size = 0.25 if conf >= 0.40 else 0.15
                    buy_amount = min(cash * buy_size, MAX_POSITION_VALUE - position_value)
                    if buy_amount > 100:  # Min $100
                        units = buy_amount / price
                        position += units
                        cash -= buy_amount
                        last_buy = i
                        trades += 1
                        if price > top_20:
                            buys_at_top += 1
                        
        elif regime == "BEAR":
            # В медведе - продаём только при подтверждении
            if not sell_cooldown and not is_panic:
                if conf >= 0.70 and mom < -0.40 and ret30 < -0.15:
                    if position > 0:
                        sell_units = position * 0.30
                        cash += sell_units * price
                        position -= sell_units
                        last_sell = i
                        trades += 1
                        if price < bottom_20:
                            sells_at_bottom += 1
                elif conf >= 0.50 and mom < -0.40 and ret30 < -0.10:
                    if position > 0:
                        sell_units = position * 0.10
                        cash += sell_units * price
                        position -= sell_units
                        last_sell = i
                        trades += 1
                        if price < bottom_20:
                            sells_at_bottom += 1
    
    final_value = cash + position * prices[-1]
    return (final_value / capital - 1) * 100, trades, sells_at_bottom


def main():
    print("\n" + "="*70)
    print("BACKTEST: v1.4.1 CONSERVATIVE vs v1.6.0 TREND-FOLLOWING")
    print("="*70)
    
    prices = generate_btc_cycle()
    momentum, vol_z, returns_30d = compute_indicators(prices)
    
    bh_return = (prices[-1] / prices[0] - 1) * 100
    
    v14_return, v14_trades, v14_sells_bottom = backtest_v14(prices, momentum, vol_z, returns_30d)
    v16_return, v16_trades, v16_sells_bottom = backtest_v16(prices, momentum, vol_z, returns_30d)
    
    print(f"\nData: {len(prices)} days")
    print(f"BTC: ${prices[0]:,.0f} → ${prices[-1]:,.0f}")
    print(f"\n{'Strategy':<25} {'Return':>12} {'Trades':>10} {'vs B&H':>12} {'Sells@Bot':>12}")
    print("-"*75)
    print(f"{'Buy & Hold':<25} {bh_return:>+11.1f}% {1:>10} {'-':>12} {0:>12}")
    print(f"{'v1.4.1 (conservative)':<25} {v14_return:>+11.1f}% {v14_trades:>10} {v14_return - bh_return:>+11.1f}% {v14_sells_bottom:>12}")
    print(f"{'v1.6.0 (trend-follow)':<25} {v16_return:>+11.1f}% {v16_trades:>10} {v16_return - bh_return:>+11.1f}% {v16_sells_bottom:>12}")
    
    print(f"\n{'='*70}")
    print("v1.6.0 TREND-FOLLOWING PHILOSOPHY:")
    print("-"*70)
    print("  1. BULL: Покупаем агрессивно, НЕ боимся euphoria")
    print("  2. BEAR: Продаём ТОЛЬКО при подтверждении (conf>0.70, mom<-0.4, ret<-15%)")
    print("  3. TRANSITION/RANGE: HOLD (не торгуем в неопределённости)")
    print("  4. Cooldown: быстро покупаем (1d), медленно продаём (7d)")
    print("  5. Panic: только mom<-0.85 + vol>2.5 (очень редко)")
    print(f"{'='*70}")
    
    improvement = v16_return - v14_return
    print(f"\n🎯 v1.6.0 vs v1.4.1: {improvement:+.1f}%")
    print(f"🎯 v1.6.0 vs B&H: {v16_return - bh_return:+.1f}%")
    
    if v16_return > v14_return:
        print("✅ v1.6.0 TREND-FOLLOWING outperforms v1.4.1")
    if v16_return > bh_return * 0.7:  # Если в пределах 30% от B&H
        print("✅ v1.6.0 competitive with B&H")


if __name__ == "__main__":
    main()
