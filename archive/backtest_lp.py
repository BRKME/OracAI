"""
══════════════════════════════════════════════════════════════════════════════
 LP POLICY BACKTEST — CFO Analysis
══════════════════════════════════════════════════════════════════════════════

Tests LP Intelligence System v2.0.1 vs naive approaches.

Simulates:
- Impermanent Loss (IL) based on price movement
- Fee income based on volatility * range component
- LP P&L = Fees - IL

Compares:
- v2.0.1 Adaptive (regime-aware exposure)
- Static 50% (constant LP exposure)
- No LP (spot only benchmark)

Key metrics:
- Total LP P&L
- IL suffered
- Fees earned
- Fee/IL ratio
- Quadrant accuracy
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Tuple
import json

np.random.seed(42)


# ══════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════

# Fee tier (Uniswap v3 0.3% tier)
FEE_TIER = 0.003

# IL formula: IL = 2 * sqrt(price_ratio) / (1 + price_ratio) - 1
# Approximation for small moves: IL ≈ price_change^2 / 8


# ══════════════════════════════════════════════════════════════════
# DATA GENERATION
# ══════════════════════════════════════════════════════════════════

def generate_btc_data() -> pd.DataFrame:
    """Generate realistic BTC price data with regime phases."""
    
    phases = [
        # (name, days, start, end, vol, regime_type)
        ("Bull Trend", 90, 45000, 65000, 0.03, "TRENDING"),
        ("Range 1", 60, 65000, 62000, 0.02, "RANGE"),
        ("Bear Trend", 60, 62000, 45000, 0.04, "TRENDING"),
        ("Capitulation", 30, 45000, 35000, 0.06, "CRASH"),
        ("Range Bottom", 90, 35000, 38000, 0.025, "RANGE"),
        ("Recovery", 90, 38000, 55000, 0.03, "TRENDING"),
        ("Chop", 60, 55000, 52000, 0.035, "CHOPPY"),
        ("Breakout", 30, 52000, 70000, 0.04, "TRENDING"),
        ("Distribution", 60, 70000, 65000, 0.03, "RANGE"),
    ]
    
    all_prices = []
    all_dates = []
    all_regimes = []
    current_date = datetime(2023, 1, 1)
    
    for name, days, start_p, end_p, vol, regime in phases:
        prices = [start_p]
        for d in range(1, days):
            progress = d / days
            target = start_p + (end_p - start_p) * progress
            drift = 0.1 * (target - prices[-1]) / prices[-1]
            shock = np.random.normal(0, vol)
            new_price = prices[-1] * (1 + drift + shock)
            prices.append(max(new_price, 10000))
        
        # Adjust to hit target
        adj = end_p / prices[-1]
        prices = [p * (1 + (adj - 1) * (i / len(prices))) for i, p in enumerate(prices)]
        
        for i, p in enumerate(prices):
            all_prices.append(p)
            all_dates.append(current_date + timedelta(days=i))
            all_regimes.append(regime)
        
        current_date = all_dates[-1] + timedelta(days=1)
    
    df = pd.DataFrame({
        'date': all_dates,
        'close': all_prices,
        'regime_true': all_regimes
    })
    df.set_index('date', inplace=True)
    
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute indicators for LP policy."""
    df = df.copy()
    
    # Returns
    df['returns_1d'] = df['close'].pct_change()
    df['returns_7d'] = df['close'].pct_change(7)
    df['returns_30d'] = df['close'].pct_change(30)
    
    # Volatility
    df['volatility'] = df['returns_1d'].rolling(30).std() * np.sqrt(365)
    df['vol_z'] = (df['volatility'] - df['volatility'].rolling(90).mean()) / \
                  (df['volatility'].rolling(90).std() + 1e-10)
    
    # Momentum
    df['ema_20'] = df['close'].ewm(span=20).mean()
    df['ema_50'] = df['close'].ewm(span=50).mean()
    
    df['momentum'] = 0.0
    df.loc[df['close'] > df['ema_20'], 'momentum'] += 0.25
    df.loc[df['ema_20'] > df['ema_50'], 'momentum'] += 0.25
    df.loc[df['close'] < df['ema_20'], 'momentum'] -= 0.25
    df.loc[df['ema_20'] < df['ema_50'], 'momentum'] -= 0.25
    df.loc[df['returns_7d'] > 0.03, 'momentum'] += 0.25
    df.loc[df['returns_7d'] < -0.03, 'momentum'] -= 0.25
    df['momentum'] = df['momentum'].clip(-1, 1)
    
    # Trend persistence (how consistent is direction)
    df['direction'] = np.sign(df['returns_1d'])
    df['persistence'] = df['direction'].rolling(14).apply(
        lambda x: abs(x.sum()) / len(x), raw=True
    )
    
    # Regime detection
    df['regime'] = 'RANGE'
    df.loc[df['momentum'] > 0.3, 'regime'] = 'BULL'
    df.loc[df['momentum'] < -0.3, 'regime'] = 'BEAR'
    df.loc[(df['momentum'].abs() < 0.2) & (df['vol_z'] > 1), 'regime'] = 'TRANSITION'
    
    # Confidence
    df['confidence'] = (0.5 + df['momentum'].abs() * 0.3).clip(0.2, 0.85)
    
    return df.dropna()


# ══════════════════════════════════════════════════════════════════
# IL & FEE CALCULATIONS
# ══════════════════════════════════════════════════════════════════

def calculate_il(price_start: float, price_end: float) -> float:
    """
    Calculate Impermanent Loss.
    IL = 2 * sqrt(price_ratio) / (1 + price_ratio) - 1
    """
    if price_start <= 0 or price_end <= 0:
        return 0
    
    price_ratio = price_end / price_start
    il = 2 * np.sqrt(price_ratio) / (1 + price_ratio) - 1
    return il


def calculate_daily_fees(volatility: float, volume_proxy: float = 1.0) -> float:
    """
    Estimate daily fees from LP.
    
    Simplified: fees ∝ volatility * volume * fee_tier
    Higher vol = more trading = more fees
    """
    # Daily vol from annual
    daily_vol = volatility / np.sqrt(365)
    
    # Fee estimate: vol * turnover proxy
    # In high vol, more trading happens through the pool
    daily_fees = daily_vol * volume_proxy * FEE_TIER
    
    return min(daily_fees, 0.01)  # Cap at 1% daily


# ══════════════════════════════════════════════════════════════════
# LP POLICY MODELS
# ══════════════════════════════════════════════════════════════════

def lp_policy_v201(row, position_value: float) -> Dict:
    """
    LP Policy v2.0.1 — Adaptive regime-aware exposure.
    
    Key insight: 
    - RANGE = high exposure (harvest fees)
    - TRENDING = low exposure (avoid IL)
    - TRANSITION = medium exposure (uncertainty = opportunity)
    """
    regime = row['regime']
    momentum = row['momentum']
    persistence = row['persistence'] if not np.isnan(row['persistence']) else 0.5
    vol_z = row['vol_z'] if not np.isnan(row['vol_z']) else 0
    confidence = row['confidence']
    
    # Vol structure classification
    if persistence > 0.6:
        vol_structure = "TREND_DOMINANT"
    elif persistence < 0.3:
        vol_structure = "RANGE_DOMINANT"
    else:
        vol_structure = "MIXED"
    
    # Base exposure by regime
    if regime == "BULL":
        if persistence > 0.6:
            base_exposure = 0.30  # Trending up = IL risk
        else:
            base_exposure = 0.60  # Choppy bull = good
    elif regime == "BEAR":
        if persistence > 0.6:
            base_exposure = 0.20  # Strong bear = avoid
        else:
            base_exposure = 0.40  # Bear chop = okay
    elif regime == "RANGE":
        base_exposure = 0.80  # RANGE = harvest!
    else:  # TRANSITION
        if confidence < 0.4:
            base_exposure = 0.50  # Low confidence = opportunity
        else:
            base_exposure = 0.35  # High confidence = trend forming
    
    # Adjust for vol structure
    if vol_structure == "RANGE_DOMINANT":
        base_exposure *= 1.2  # Range vol = good
    elif vol_structure == "TREND_DOMINANT":
        base_exposure *= 0.7  # Trend vol = bad
    
    # Adjust for extreme vol
    if vol_z > 2.0:
        base_exposure *= 0.6  # Gap risk
    elif vol_z < -1.0:
        base_exposure *= 1.1  # Low vol = harvest
    
    # Clamp
    exposure = min(0.90, max(0.10, base_exposure))
    
    # Risk quadrant
    risk_dir = -row['momentum']  # Simplified: negative momentum = risk-off
    risk_lp = 0.5 - persistence + (0.5 if regime == "RANGE" else 0)
    
    if risk_dir >= 0 and risk_lp >= 0:
        quadrant = "Q1"
    elif risk_dir < 0 and risk_lp >= 0:
        quadrant = "Q2"
    elif risk_dir >= 0 and risk_lp < 0:
        quadrant = "Q3"
    else:
        quadrant = "Q4"
    
    return {
        'exposure': exposure,
        'vol_structure': vol_structure,
        'quadrant': quadrant,
        'hedge': persistence > 0.5,
    }


def lp_policy_static(row, position_value: float) -> Dict:
    """Static 50% LP exposure - baseline."""
    return {
        'exposure': 0.50,
        'vol_structure': 'UNKNOWN',
        'quadrant': 'N/A',
        'hedge': False,
    }


def lp_policy_aggressive(row, position_value: float) -> Dict:
    """Aggressive 80% LP exposure."""
    return {
        'exposure': 0.80,
        'vol_structure': 'UNKNOWN',
        'quadrant': 'N/A',
        'hedge': False,
    }


# ══════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════

@dataclass
class LPBacktestResult:
    name: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    total_fees_earned: float
    total_il_suffered: float
    fee_il_ratio: float
    avg_exposure: float
    max_drawdown_pct: float
    days_in_q2: int  # Key metric: Q2 = LP opportunity


def run_lp_backtest(
    df: pd.DataFrame,
    policy_func,
    policy_name: str,
    initial_capital: float = 100000
) -> LPBacktestResult:
    """
    Run LP backtest.
    
    Portfolio split:
    - LP portion: earns fees, suffers IL
    - Spot portion: tracks price
    """
    capital = initial_capital
    lp_value = initial_capital * 0.5  # Start 50% in LP
    spot_value = initial_capital * 0.5  # Start 50% in spot
    
    total_fees = 0
    total_il = 0
    equity_curve = []
    q2_days = 0
    
    entry_price = df.iloc[0]['close']
    
    for i in range(30, len(df)):
        row = df.iloc[i]
        price = row['close']
        prev_price = df.iloc[i-1]['close']
        
        # Get policy
        policy = policy_func(row, capital)
        target_exposure = policy['exposure']
        
        if policy.get('quadrant') == 'Q2':
            q2_days += 1
        
        # Current exposure
        total_value = lp_value + spot_value
        current_exposure = lp_value / total_value if total_value > 0 else 0
        
        # Rebalance if needed (>10% deviation)
        if abs(current_exposure - target_exposure) > 0.10:
            lp_value = total_value * target_exposure
            spot_value = total_value * (1 - target_exposure)
        
        # Calculate daily IL on LP position
        if lp_value > 0:
            daily_il = calculate_il(prev_price, price)
            il_amount = lp_value * abs(daily_il)
            total_il += il_amount
            
            # LP value changes: -IL + price move component
            price_change = (price / prev_price - 1)
            # LP gets half the price change (balanced position)
            lp_value = lp_value * (1 + daily_il + price_change * 0.5)
        
        # Spot value tracks price
        spot_value = spot_value * (price / prev_price)
        
        # Calculate and add fees
        if lp_value > 0:
            vol = row['volatility'] if not np.isnan(row['volatility']) else 0.5
            daily_fees = calculate_daily_fees(vol) * lp_value
            total_fees += daily_fees
            lp_value += daily_fees
        
        # Track equity
        capital = lp_value + spot_value
        equity_curve.append(capital)
    
    # Final calculations
    final_capital = lp_value + spot_value
    total_return = (final_capital / initial_capital - 1) * 100
    
    # Max drawdown
    equity = pd.Series(equity_curve)
    rolling_max = equity.expanding().max()
    drawdowns = (equity - rolling_max) / rolling_max
    max_dd = abs(drawdowns.min()) * 100
    
    # Fee/IL ratio
    fee_il_ratio = total_fees / total_il if total_il > 0 else 10.0
    
    # Average exposure
    avg_exposure = 0.5  # Simplified
    
    return LPBacktestResult(
        name=policy_name,
        initial_capital=initial_capital,
        final_capital=final_capital,
        total_return_pct=total_return,
        total_fees_earned=total_fees,
        total_il_suffered=total_il,
        fee_il_ratio=fee_il_ratio,
        avg_exposure=avg_exposure,
        max_drawdown_pct=max_dd,
        days_in_q2=q2_days,
    )


def run_spot_only(df: pd.DataFrame, initial_capital: float = 100000) -> LPBacktestResult:
    """Spot only benchmark (no LP)."""
    start_price = df.iloc[30]['close']
    end_price = df.iloc[-1]['close']
    
    final = initial_capital * (end_price / start_price)
    total_return = (final / initial_capital - 1) * 100
    
    equity = [initial_capital * (df.iloc[i]['close'] / start_price) 
              for i in range(30, len(df))]
    equity = pd.Series(equity)
    rolling_max = equity.expanding().max()
    drawdowns = (equity - rolling_max) / rolling_max
    max_dd = abs(drawdowns.min()) * 100
    
    return LPBacktestResult(
        name="Spot Only (No LP)",
        initial_capital=initial_capital,
        final_capital=final,
        total_return_pct=total_return,
        total_fees_earned=0,
        total_il_suffered=0,
        fee_il_ratio=0,
        avg_exposure=0,
        max_drawdown_pct=max_dd,
        days_in_q2=0,
    )


# ══════════════════════════════════════════════════════════════════
# REPORT
# ══════════════════════════════════════════════════════════════════

def print_lp_report(results: List[LPBacktestResult], df: pd.DataFrame):
    """Print CFO report for LP backtest."""
    
    print("=" * 80)
    print("         LP POLICY BACKTEST — CFO ANALYSIS")
    print("=" * 80)
    print()
    
    print("DATA SUMMARY")
    print("-" * 40)
    print(f"Period: {df.index[30].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}")
    print(f"Days: {len(df) - 30}")
    print(f"Start Price: ${df.iloc[30]['close']:,.0f}")
    print(f"End Price: ${df.iloc[-1]['close']:,.0f}")
    print(f"Price Change: {(df.iloc[-1]['close'] / df.iloc[30]['close'] - 1) * 100:+.1f}%")
    print()
    
    print("=" * 80)
    print("                    PERFORMANCE COMPARISON")
    print("=" * 80)
    print()
    
    header = f"{'Metric':<25} "
    for r in results:
        header += f"{r.name:>18} "
    print(header)
    print("-" * 85)
    
    metrics = [
        ("Initial Capital", lambda r: f"${r.initial_capital:,.0f}"),
        ("Final Capital", lambda r: f"${r.final_capital:,.0f}"),
        ("Total Return", lambda r: f"{r.total_return_pct:+.1f}%"),
        ("Max Drawdown", lambda r: f"-{r.max_drawdown_pct:.1f}%"),
        ("Fees Earned", lambda r: f"${r.total_fees_earned:,.0f}"),
        ("IL Suffered", lambda r: f"${r.total_il_suffered:,.0f}"),
        ("Fee/IL Ratio", lambda r: f"{r.fee_il_ratio:.2f}x"),
        ("Days in Q2", lambda r: f"{r.days_in_q2}"),
    ]
    
    for name, func in metrics:
        row = f"{name:<25} "
        for r in results:
            row += f"{func(r):>18} "
        print(row)
    
    print()
    print("=" * 80)
    print("                    LP ANALYSIS")
    print("=" * 80)
    print()
    
    v201 = next((r for r in results if "v2.0.1" in r.name), None)
    static = next((r for r in results if "Static" in r.name), None)
    spot = next((r for r in results if "Spot" in r.name), None)
    
    if v201 and static:
        print("v2.0.1 vs Static 50%:")
        print(f"  Return:      {v201.total_return_pct:+.1f}% vs {static.total_return_pct:+.1f}%  ({v201.total_return_pct - static.total_return_pct:+.1f}%)")
        print(f"  Fee/IL:      {v201.fee_il_ratio:.2f}x vs {static.fee_il_ratio:.2f}x")
        print(f"  Max DD:      {v201.max_drawdown_pct:.1f}% vs {static.max_drawdown_pct:.1f}%")
        print()
    
    if v201 and spot:
        print("v2.0.1 vs Spot Only:")
        print(f"  Return:      {v201.total_return_pct:+.1f}% vs {spot.total_return_pct:+.1f}%  ({v201.total_return_pct - spot.total_return_pct:+.1f}%)")
        print(f"  Fees earned: ${v201.total_fees_earned:,.0f} (LP premium)")
        print()
    
    print("=" * 80)
    print("                    RECOMMENDATION")
    print("=" * 80)
    print()
    
    if v201:
        if v201.fee_il_ratio > 1.0:
            print("  ✅ LP Intelligence v2.0.1 is profitable (Fee/IL > 1)")
        else:
            print("  ⚠️ LP was unprofitable in this period (Fee/IL < 1)")
        
        if v201 and static and v201.total_return_pct > static.total_return_pct:
            print("  ✅ Adaptive exposure outperforms static")
        
        if v201.days_in_q2 > 0:
            print(f"  ✅ Identified {v201.days_in_q2} days in Q2 (LP opportunity)")
    
    print()
    print("=" * 80)


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    print()
    print("Generating BTC price data...")
    df = generate_btc_data()
    print(f"Generated {len(df)} days")
    
    print("Computing indicators...")
    df = compute_indicators(df)
    print(f"Data ready: {len(df)} days")
    print()
    
    print("Running LP backtests...")
    print()
    
    results = []
    
    # v2.0.1 Adaptive
    r1 = run_lp_backtest(df, lp_policy_v201, "LP v2.0.1 Adaptive")
    results.append(r1)
    print(f"  v2.0.1:    {r1.total_return_pct:+.1f}%, Fee/IL: {r1.fee_il_ratio:.2f}x")
    
    # Static 50%
    r2 = run_lp_backtest(df, lp_policy_static, "Static 50% LP")
    results.append(r2)
    print(f"  Static:    {r2.total_return_pct:+.1f}%, Fee/IL: {r2.fee_il_ratio:.2f}x")
    
    # Aggressive 80%
    r3 = run_lp_backtest(df, lp_policy_aggressive, "Aggressive 80% LP")
    results.append(r3)
    print(f"  Aggr 80%:  {r3.total_return_pct:+.1f}%, Fee/IL: {r3.fee_il_ratio:.2f}x")
    
    # Spot only
    r4 = run_spot_only(df)
    results.append(r4)
    print(f"  Spot Only: {r4.total_return_pct:+.1f}%")
    
    print()
    print_lp_report(results, df)
    
    # Save results
    output = {
        "generated_at": datetime.now().isoformat(),
        "results": [{
            "name": r.name,
            "total_return_pct": round(r.total_return_pct, 2),
            "fees_earned": round(r.total_fees_earned, 2),
            "il_suffered": round(r.total_il_suffered, 2),
            "fee_il_ratio": round(r.fee_il_ratio, 2),
            "max_drawdown_pct": round(r.max_drawdown_pct, 2),
        } for r in results]
    }
    
    with open("backtest_lp_results.json", "w") as f:
        json.dump(output, f, indent=2)
    
    print()
    print("Results saved to backtest_lp_results.json")


if __name__ == "__main__":
    main()
