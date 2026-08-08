"""
══════════════════════════════════════════════════════════════════════════════
 COMBINED BACKTEST — Asset Allocation v1.4.1 + LP Policy v2.0.2
══════════════════════════════════════════════════════════════════════════════

Full portfolio simulation:
- Directional book (spot BTC) managed by Asset Allocation v1.4.1
- LP book managed by LP Policy v2.0.2
- Combined portfolio metrics

Compares:
1. Full System (AA + LP)
2. AA Only (no LP)
3. LP Only (no directional)
4. Buy & Hold
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Tuple
import json

np.random.seed(42)


# ══════════════════════════════════════════════════════════════════
# DATA GENERATION
# ══════════════════════════════════════════════════════════════════

def generate_btc_data() -> pd.DataFrame:
    """Generate realistic BTC price data."""
    phases = [
        ("Bull", 120, 40000, 65000, 0.03),
        ("Range 1", 60, 65000, 60000, 0.02),
        ("Bear", 90, 60000, 35000, 0.05),
        ("Capitulation", 30, 35000, 25000, 0.07),
        ("Range Bottom", 90, 25000, 30000, 0.025),
        ("Recovery", 120, 30000, 50000, 0.03),
        ("Chop", 60, 50000, 48000, 0.03),
        ("Bull 2", 90, 48000, 75000, 0.04),
        ("Distribution", 60, 75000, 65000, 0.03),
    ]
    
    all_prices, all_dates = [], []
    current_date = datetime(2022, 1, 1)
    
    for _, days, start_p, end_p, vol in phases:
        prices = [start_p]
        for d in range(1, days):
            progress = d / days
            target = start_p + (end_p - start_p) * progress
            drift = 0.1 * (target - prices[-1]) / prices[-1]
            prices.append(max(prices[-1] * (1 + drift + np.random.normal(0, vol)), 10000))
        
        adj = end_p / prices[-1]
        prices = [p * (1 + (adj - 1) * (i / len(prices))) for i, p in enumerate(prices)]
        
        for i, p in enumerate(prices):
            all_prices.append(p)
            all_dates.append(current_date + timedelta(days=i))
        current_date = all_dates[-1] + timedelta(days=1)
    
    df = pd.DataFrame({'date': all_dates, 'close': all_prices})
    df.set_index('date', inplace=True)
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicators."""
    df = df.copy()
    
    df['returns_1d'] = df['close'].pct_change()
    df['returns_7d'] = df['close'].pct_change(7)
    df['returns_30d'] = df['close'].pct_change(30)
    df['volatility'] = df['returns_1d'].rolling(30).std() * np.sqrt(365)
    df['vol_z'] = (df['volatility'] - df['volatility'].rolling(90).mean()) / \
                  (df['volatility'].rolling(90).std() + 1e-10)
    
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
    
    df['direction'] = np.sign(df['returns_1d'])
    df['persistence'] = df['direction'].rolling(14).apply(
        lambda x: abs(x.sum()) / len(x), raw=True
    )
    
    df['regime'] = 'RANGE'
    df.loc[df['momentum'] > 0.3, 'regime'] = 'BULL'
    df.loc[df['momentum'] < -0.3, 'regime'] = 'BEAR'
    df.loc[(df['momentum'].abs() < 0.2) & (df['vol_z'] > 1), 'regime'] = 'TRANSITION'
    
    df['confidence'] = (0.5 + df['momentum'].abs() * 0.3).clip(0.2, 0.85)
    
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    df['rsi'] = 100 - (100 / (1 + gain / (loss + 1e-10)))
    
    df['tail_risk'] = (df['rsi'] < 25) | (df['rsi'] > 80) | (df['vol_z'] > 2)
    df['tail_polarity'] = 'none'
    df.loc[(df['tail_risk']) & (df['momentum'] < 0), 'tail_polarity'] = 'downside'
    
    return df.dropna()


# ══════════════════════════════════════════════════════════════════
# ASSET ALLOCATION v1.4.1
# ══════════════════════════════════════════════════════════════════

def aa_v141(row, position, last_action, days_since):
    """Asset Allocation v1.4.1 with counter-cyclical logic."""
    regime = row['regime']
    conf = row['confidence']
    mom = row['momentum']
    tail = row['tail_risk']
    tail_pol = row['tail_polarity']
    vol_z = row['vol_z'] if not np.isnan(row['vol_z']) else 0
    ret30 = row['returns_30d'] if not np.isnan(row['returns_30d']) else 0
    
    # Panic detection (v1.4.1 tuned)
    is_panic = (
        (mom < -0.50 and vol_z > 1.5) or
        (mom < -0.60 and vol_z > 1.0) or
        (ret30 < -0.30)
    )
    
    # Counter-cyclical: don't sell panic
    if tail and tail_pol == 'downside':
        if is_panic:
            return "HOLD", 0, "panic_protect"
        return "STRONG_SELL", -0.50, "tail"
    
    # Standard logic
    if conf < 0.40:
        return "HOLD", 0, "conf"
    if last_action == "BUY" and days_since < 3:
        return "HOLD", 0, "cd"
    if last_action == "SELL" and days_since < 7:
        return "HOLD", 0, "cd"
    if "STRONG" in last_action and days_since < 14:
        return "HOLD", 0, "cd"
    
    if regime == "BULL":
        if mom > 0.70:  # Don't buy euphoria
            return "HOLD", 0, "euphoria"
        if conf >= 0.70 and mom > 0.50:
            return "STRONG_BUY", 0.20, "bull"
        elif conf >= 0.50 and mom > 0:
            return "BUY", 0.10, "bull"
    elif regime == "BEAR":
        if is_panic:
            return "HOLD", 0, "panic_hold"
        if conf >= 0.60 and mom < -0.50:
            return "SELL", -0.30, "bear"
        elif conf >= 0.50 and mom < 0:
            return "SELL", -0.15, "bear"
    elif regime == "TRANSITION":
        if mom < -0.30 and conf >= 0.50 and not is_panic:
            return "SELL", -0.10, "trans"
    
    return "HOLD", 0, "hold"


# ══════════════════════════════════════════════════════════════════
# LP POLICY v2.0.2
# ══════════════════════════════════════════════════════════════════

def lp_v202(row):
    """LP Policy v2.0.2 with conservative trend management."""
    regime = row['regime']
    persistence = row['persistence'] if not np.isnan(row['persistence']) else 0.5
    momentum = abs(row['momentum'])
    ret7 = abs(row['returns_7d']) if not np.isnan(row['returns_7d']) else 0
    vol_z = row['vol_z'] if not np.isnan(row['vol_z']) else 0
    
    # Strong trend detection (v2.0.2)
    is_strong_trend = persistence > 0.45 or momentum > 0.5 or ret7 > 0.05
    is_weak_trend = persistence > 0.35 or momentum > 0.3
    
    # v2.0.2 conservative exposure
    if regime == "RANGE" and not is_weak_trend:
        exposure = 0.60
    elif regime == "RANGE" and is_weak_trend:
        exposure = 0.30
    elif is_strong_trend:
        exposure = 0.10  # Minimal in strong trends
    elif is_weak_trend:
        exposure = 0.20
    else:
        exposure = 0.40
    
    # Gap risk adjustment
    if vol_z > 2.0:
        exposure *= 0.5
    
    return {'exposure': min(0.80, max(0.05, exposure))}


def calculate_il(p1, p2):
    if p1 <= 0 or p2 <= 0:
        return 0
    r = p2 / p1
    return 2 * np.sqrt(r) / (1 + r) - 1


def calculate_fees(vol):
    daily_vol = vol / np.sqrt(365) if vol > 0 else 0
    return min(daily_vol * 0.003, 0.01)


# ══════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════

@dataclass
class PortfolioResult:
    name: str
    initial: float
    final: float
    return_pct: float
    max_dd: float
    sharpe: float
    spot_final: float
    lp_final: float
    lp_fees: float
    lp_il: float
    aa_trades: int
    panic_protects: int


def run_full_system(df, initial=100000):
    """Full system: AA v1.4.1 + LP v2.0.2"""
    
    # Portfolio split: 60% directional (spot), 40% LP book
    spot_value = initial * 0.60
    lp_value = initial * 0.40
    
    spot_btc = spot_value / df.iloc[0]['close']
    spot_cash = 0
    
    total_fees = 0
    total_il = 0
    equity = []
    
    last_action = "HOLD"
    last_date = df.index[0]
    aa_trades = 0
    panic_protects = 0
    
    for i in range(60, len(df)):
        row = df.iloc[i]
        price = row['close']
        prev_price = df.iloc[i-1]['close']
        date = row.name
        
        # ═══ DIRECTIONAL BOOK (AA v1.4.1) ═══
        days_since = (date - last_date).days
        spot_total = spot_cash + spot_btc * price
        position = (spot_btc * price) / spot_total if spot_total > 0 else 0
        
        action, size, reason = aa_v141(row, position, last_action, days_since)
        
        if reason == "panic_protect":
            panic_protects += 1
        
        if action != "HOLD" and size != 0:
            if size > 0:  # BUY
                cash_use = spot_cash * min(size * 2, 1.0)
                if cash_use > 100:
                    spot_btc += cash_use / price
                    spot_cash -= cash_use
                    last_action, last_date = action, date
                    aa_trades += 1
            else:  # SELL
                btc_sell = spot_btc * min(abs(size), 1.0)
                if btc_sell * price > 100:
                    spot_cash += btc_sell * price
                    spot_btc -= btc_sell
                    last_action, last_date = action, date
                    aa_trades += 1
        
        # Update spot value
        spot_value = spot_cash + spot_btc * price
        
        # ═══ LP BOOK (LP v2.0.2) ═══
        lp_policy = lp_v202(row)
        target_exp = lp_policy['exposure']
        
        # LP value changes: IL + price component + fees
        if lp_value > 0:
            daily_il = calculate_il(prev_price, price)
            il_amt = lp_value * abs(daily_il)
            total_il += il_amt
            
            price_change = price / prev_price - 1
            lp_value = lp_value * (1 + daily_il + price_change * 0.5)
            
            vol = row['volatility'] if not np.isnan(row['volatility']) else 0.5
            fees = calculate_fees(vol) * lp_value * target_exp
            total_fees += fees
            lp_value += fees
        
        # Track total equity
        total_equity = spot_value + lp_value
        equity.append(total_equity)
    
    final = spot_value + lp_value
    ret = (final / initial - 1) * 100
    
    eq = pd.Series(equity)
    dd = abs(((eq - eq.expanding().max()) / eq.expanding().max()).min()) * 100
    rets = eq.pct_change().dropna()
    sharpe = (rets.mean() * 365) / (rets.std() * np.sqrt(365)) if rets.std() > 0 else 0
    
    return PortfolioResult(
        name="Full System (AA+LP)",
        initial=initial, final=final, return_pct=ret, max_dd=dd, sharpe=sharpe,
        spot_final=spot_value, lp_final=lp_value,
        lp_fees=total_fees, lp_il=total_il,
        aa_trades=aa_trades, panic_protects=panic_protects
    )


def run_aa_only(df, initial=100000):
    """AA only, no LP"""
    spot_btc = (initial * 0.5) / df.iloc[0]['close']
    spot_cash = initial * 0.5
    equity = []
    
    last_action = "HOLD"
    last_date = df.index[0]
    aa_trades = 0
    panic_protects = 0
    
    for i in range(60, len(df)):
        row = df.iloc[i]
        price = row['close']
        date = row.name
        
        days_since = (date - last_date).days
        spot_total = spot_cash + spot_btc * price
        position = (spot_btc * price) / spot_total if spot_total > 0 else 0
        
        action, size, reason = aa_v141(row, position, last_action, days_since)
        
        if reason == "panic_protect":
            panic_protects += 1
        
        if action != "HOLD" and size != 0:
            if size > 0:
                cash_use = spot_cash * min(size * 2, 1.0)
                if cash_use > 100:
                    spot_btc += cash_use / price
                    spot_cash -= cash_use
                    last_action, last_date = action, date
                    aa_trades += 1
            else:
                btc_sell = spot_btc * min(abs(size), 1.0)
                if btc_sell * price > 100:
                    spot_cash += btc_sell * price
                    spot_btc -= btc_sell
                    last_action, last_date = action, date
                    aa_trades += 1
        
        equity.append(spot_cash + spot_btc * price)
    
    final = spot_cash + spot_btc * df.iloc[-1]['close']
    ret = (final / initial - 1) * 100
    
    eq = pd.Series(equity)
    dd = abs(((eq - eq.expanding().max()) / eq.expanding().max()).min()) * 100
    rets = eq.pct_change().dropna()
    sharpe = (rets.mean() * 365) / (rets.std() * np.sqrt(365)) if rets.std() > 0 else 0
    
    return PortfolioResult(
        name="AA Only (no LP)",
        initial=initial, final=final, return_pct=ret, max_dd=dd, sharpe=sharpe,
        spot_final=final, lp_final=0,
        lp_fees=0, lp_il=0,
        aa_trades=aa_trades, panic_protects=panic_protects
    )


def run_lp_only(df, initial=100000):
    """LP only, no directional"""
    lp_value = initial
    total_fees = 0
    total_il = 0
    equity = []
    
    for i in range(60, len(df)):
        row = df.iloc[i]
        price = row['close']
        prev_price = df.iloc[i-1]['close']
        
        lp_policy = lp_v202(row)
        
        if lp_value > 0:
            daily_il = calculate_il(prev_price, price)
            il_amt = lp_value * abs(daily_il)
            total_il += il_amt
            
            price_change = price / prev_price - 1
            lp_value = lp_value * (1 + daily_il + price_change * 0.5)
            
            vol = row['volatility'] if not np.isnan(row['volatility']) else 0.5
            fees = calculate_fees(vol) * lp_value * lp_policy['exposure']
            total_fees += fees
            lp_value += fees
        
        equity.append(lp_value)
    
    ret = (lp_value / initial - 1) * 100
    
    eq = pd.Series(equity)
    dd = abs(((eq - eq.expanding().max()) / eq.expanding().max()).min()) * 100
    rets = eq.pct_change().dropna()
    sharpe = (rets.mean() * 365) / (rets.std() * np.sqrt(365)) if rets.std() > 0 else 0
    
    return PortfolioResult(
        name="LP Only (no AA)",
        initial=initial, final=lp_value, return_pct=ret, max_dd=dd, sharpe=sharpe,
        spot_final=0, lp_final=lp_value,
        lp_fees=total_fees, lp_il=total_il,
        aa_trades=0, panic_protects=0
    )


def run_buy_hold(df, initial=100000):
    """Buy and hold benchmark"""
    start_p = df.iloc[60]['close']
    end_p = df.iloc[-1]['close']
    final = initial * (end_p / start_p)
    ret = (final / initial - 1) * 100
    
    equity = [initial * (df.iloc[i]['close'] / start_p) for i in range(60, len(df))]
    eq = pd.Series(equity)
    dd = abs(((eq - eq.expanding().max()) / eq.expanding().max()).min()) * 100
    rets = eq.pct_change().dropna()
    sharpe = (rets.mean() * 365) / (rets.std() * np.sqrt(365)) if rets.std() > 0 else 0
    
    return PortfolioResult(
        name="Buy & Hold",
        initial=initial, final=final, return_pct=ret, max_dd=dd, sharpe=sharpe,
        spot_final=final, lp_final=0,
        lp_fees=0, lp_il=0,
        aa_trades=0, panic_protects=0
    )


# ══════════════════════════════════════════════════════════════════
# REPORT
# ══════════════════════════════════════════════════════════════════

def print_report(results, df):
    print("=" * 90)
    print("         COMBINED BACKTEST — AA v1.4.1 + LP v2.0.2")
    print("=" * 90)
    print()
    
    print("DATA SUMMARY")
    print("-" * 40)
    print(f"Period: {df.index[60].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}")
    print(f"Days: {len(df) - 60}")
    print(f"Start: ${df.iloc[60]['close']:,.0f} → End: ${df.iloc[-1]['close']:,.0f}")
    print(f"B&H Return: {(df.iloc[-1]['close'] / df.iloc[60]['close'] - 1) * 100:+.1f}%")
    print()
    
    print("=" * 90)
    print("                         PERFORMANCE")
    print("=" * 90)
    print()
    
    print(f"{'Strategy':<22} {'Return':>10} {'MaxDD':>10} {'Sharpe':>8} {'Trades':>8} {'Panics':>8}")
    print("-" * 70)
    for r in results:
        print(f"{r.name:<22} {r.return_pct:>+9.1f}% {r.max_dd:>9.1f}% {r.sharpe:>8.2f} {r.aa_trades:>8} {r.panic_protects:>8}")
    
    print()
    print("=" * 90)
    print("                         LP METRICS")
    print("=" * 90)
    print()
    
    print(f"{'Strategy':<22} {'LP Final':>12} {'Fees':>12} {'IL':>12} {'Fee/IL':>10}")
    print("-" * 70)
    for r in results:
        ratio = r.lp_fees / r.lp_il if r.lp_il > 0 else 0
        print(f"{r.name:<22} ${r.lp_final:>10,.0f} ${r.lp_fees:>10,.0f} ${r.lp_il:>10,.0f} {ratio:>9.2f}x")
    
    print()
    print("=" * 90)
    print("                         ANALYSIS")
    print("=" * 90)
    print()
    
    full = next(r for r in results if "Full" in r.name)
    aa = next(r for r in results if "AA Only" in r.name)
    lp = next(r for r in results if "LP Only" in r.name)
    bh = next(r for r in results if "Hold" in r.name)
    
    print(f"Full System vs Buy & Hold:")
    print(f"  Return:      {full.return_pct:+.1f}% vs {bh.return_pct:+.1f}%  (alpha: {full.return_pct - bh.return_pct:+.1f}%)")
    print(f"  Max DD:      {full.max_dd:.1f}% vs {bh.max_dd:.1f}%  ({full.max_dd - bh.max_dd:+.1f}%)")
    print(f"  Sharpe:      {full.sharpe:.2f} vs {bh.sharpe:.2f}")
    print()
    
    print(f"Component Analysis:")
    print(f"  Spot book:   ${full.spot_final:,.0f} ({(full.spot_final / 60000 - 1) * 100:+.1f}%)")
    print(f"  LP book:     ${full.lp_final:,.0f} ({(full.lp_final / 40000 - 1) * 100:+.1f}%)")
    print(f"  LP Fees:     ${full.lp_fees:,.0f}")
    print(f"  LP IL:       ${full.lp_il:,.0f}")
    print()
    
    print(f"Counter-cyclical Protection:")
    print(f"  Panic holds: {full.panic_protects} times")
    print()
    
    print("=" * 90)
    print("                         RECOMMENDATION")
    print("=" * 90)
    print()
    
    if full.return_pct > bh.return_pct:
        print("  ✅ Full system outperforms Buy & Hold")
    if full.max_dd < bh.max_dd:
        print("  ✅ Full system has lower drawdown")
    if full.panic_protects > 0:
        print(f"  ✅ Counter-cyclical protected {full.panic_protects} times")
    if full.lp_fees > full.lp_il:
        print("  ✅ LP book is profitable (Fees > IL)")
    
    print()
    print("=" * 90)


def main():
    print()
    print("Generating data...")
    df = generate_btc_data()
    df = compute_indicators(df)
    print(f"Data ready: {len(df)} days")
    print()
    
    print("Running backtests...")
    
    results = [
        run_full_system(df),
        run_aa_only(df),
        run_lp_only(df),
        run_buy_hold(df),
    ]
    
    print()
    print_report(results, df)
    
    # Save results
    output = {
        "generated_at": datetime.now().isoformat(),
        "results": [{
            "name": r.name,
            "return_pct": round(r.return_pct, 2),
            "max_dd": round(r.max_dd, 2),
            "sharpe": round(r.sharpe, 2),
            "lp_fees": round(r.lp_fees, 2),
            "lp_il": round(r.lp_il, 2),
            "aa_trades": r.aa_trades,
            "panic_protects": r.panic_protects,
        } for r in results]
    }
    
    with open("backtest_combined_results.json", "w") as f:
        json.dump(output, f, indent=2)
    
    print()
    print("Results saved to backtest_combined_results.json")


if __name__ == "__main__":
    main()
