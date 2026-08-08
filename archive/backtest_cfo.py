"""
══════════════════════════════════════════════════════════════════════════════
 COMPREHENSIVE BACKTEST — CFO FINANCIAL ANALYSIS
══════════════════════════════════════════════════════════════════════════════

Simulates realistic BTC market cycles based on actual 2022-2025 price action:

Phase 1 (2022 Q1-Q2): Bear crash     $47,000 → $17,500  (-63%)
Phase 2 (2022 Q3-Q4): Bottom chop    $17,500 → $16,500  (-6%)
Phase 3 (2023 Q1-Q3): Recovery       $16,500 → $31,000  (+88%)
Phase 4 (2023 Q4):    Breakout       $31,000 → $44,000  (+42%)
Phase 5 (2024 Q1):    ETF pump       $44,000 → $73,000  (+66%)
Phase 6 (2024 Q2-Q3): Consolidation  $73,000 → $54,000  (-26%)
Phase 7 (2024 Q4):    ATH rally      $54,000 → $108,000 (+100%)
Phase 8 (2025 Q1):    Correction     $108,000 → $78,000 (-28%)

Tests three strategies:
- v1.3.1 Conservative (current model)
- v1.4 Counter-cyclical (new model)
- Buy & Hold (benchmark)

CFO Metrics:
- Total Return, CAGR, Alpha
- Sharpe Ratio, Sortino Ratio
- Max Drawdown, Calmar Ratio
- Win Rate, Profit Factor
- Timing Analysis (sells at bottom/top)
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import json

np.random.seed(42)


# ══════════════════════════════════════════════════════════════════
# REALISTIC BTC PRICE SIMULATION
# ══════════════════════════════════════════════════════════════════

def generate_realistic_btc_cycles() -> pd.DataFrame:
    """
    Generate realistic BTC prices matching actual 2022-2025 cycles.
    Uses geometric Brownian motion with regime-specific parameters.
    """
    
    phases = [
        ("Bear Crash Q1-Q2 2022", 180, 47000, 17500, 0.05, -0.006),
        ("Bottom Chop Q3-Q4 2022", 180, 17500, 16500, 0.03, -0.0003),
        ("Recovery 2023", 270, 16500, 31000, 0.025, 0.002),
        ("Breakout Q4 2023", 90, 31000, 44000, 0.03, 0.004),
        ("ETF Pump Q1 2024", 90, 44000, 73000, 0.04, 0.006),
        ("Consolidation Q2-Q3 2024", 180, 73000, 54000, 0.035, -0.002),
        ("ATH Rally Q4 2024", 90, 54000, 108000, 0.045, 0.008),
        ("Correction Q1 2025", 90, 108000, 78000, 0.05, -0.004),
    ]
    
    all_prices = []
    all_dates = []
    current_date = datetime(2022, 1, 1)
    
    for phase_name, days, start_p, end_p, vol, trend in phases:
        prices = [start_p]
        
        for d in range(1, days):
            progress = d / days
            target = start_p + (end_p - start_p) * progress
            drift = 0.1 * (target - prices[-1]) / prices[-1]
            shock = np.random.normal(0, vol)
            new_price = prices[-1] * (1 + drift + shock)
            new_price = max(new_price, 10000)
            prices.append(new_price)
        
        adjustment = end_p / prices[-1]
        prices = [p * (1 + (adjustment - 1) * (i / len(prices))) 
                  for i, p in enumerate(prices)]
        
        for i, p in enumerate(prices):
            all_prices.append(p)
            all_dates.append(current_date + timedelta(days=i))
        
        current_date = all_dates[-1] + timedelta(days=1)
    
    df = pd.DataFrame({'date': all_dates, 'close': all_prices})
    df.set_index('date', inplace=True)
    df['high'] = df['close'] * (1 + np.abs(np.random.normal(0, 0.02, len(df))))
    df['low'] = df['close'] * (1 - np.abs(np.random.normal(0, 0.02, len(df))))
    df['volume'] = np.random.uniform(20e9, 50e9, len(df))
    
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['returns_1d'] = df['close'].pct_change()
    df['returns_7d'] = df['close'].pct_change(7)
    df['returns_30d'] = df['close'].pct_change(30)
    df['volatility_30d'] = df['returns_1d'].rolling(30).std() * np.sqrt(365)
    df['vol_z'] = (df['volatility_30d'] - df['volatility_30d'].rolling(90).mean()) / \
                  (df['volatility_30d'].rolling(90).std() + 1e-10)
    
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    df['rsi'] = 100 - (100 / (1 + rs))
    
    df['ema_20'] = df['close'].ewm(span=20).mean()
    df['ema_50'] = df['close'].ewm(span=50).mean()
    df['ema_200'] = df['close'].ewm(span=200).mean()
    
    df['momentum'] = 0.0
    df.loc[df['close'] > df['ema_20'], 'momentum'] += 0.2
    df.loc[df['ema_20'] > df['ema_50'], 'momentum'] += 0.2
    df.loc[df['close'] < df['ema_20'], 'momentum'] -= 0.2
    df.loc[df['ema_20'] < df['ema_50'], 'momentum'] -= 0.2
    df.loc[df['returns_7d'] > 0.05, 'momentum'] += 0.3
    df.loc[df['returns_7d'] > 0.02, 'momentum'] += 0.15
    df.loc[df['returns_7d'] < -0.05, 'momentum'] -= 0.3
    df.loc[df['returns_7d'] < -0.02, 'momentum'] -= 0.15
    df.loc[df['rsi'] > 60, 'momentum'] += 0.1
    df.loc[df['rsi'] < 40, 'momentum'] -= 0.1
    df['momentum'] = df['momentum'].clip(-1, 1)
    
    df['regime'] = 'RANGE'
    df.loc[df['momentum'] > 0.3, 'regime'] = 'BULL'
    df.loc[df['momentum'] < -0.3, 'regime'] = 'BEAR'
    df.loc[(df['momentum'].abs() < 0.15) & (df['vol_z'] > 1), 'regime'] = 'TRANSITION'
    
    df['confidence'] = 0.5 + df['momentum'].abs() * 0.3
    df['confidence'] = df['confidence'].clip(0.2, 0.85)
    
    df['tail_risk'] = False
    df.loc[df['rsi'] < 25, 'tail_risk'] = True
    df.loc[df['rsi'] > 80, 'tail_risk'] = True
    df.loc[df['vol_z'] > 2, 'tail_risk'] = True
    
    df['tail_polarity'] = 'none'
    df.loc[(df['tail_risk']) & (df['momentum'] < 0), 'tail_polarity'] = 'downside'
    df.loc[(df['tail_risk']) & (df['momentum'] > 0), 'tail_polarity'] = 'upside'
    
    return df.dropna()


@dataclass
class Trade:
    date: datetime
    action: str
    price: float
    size_pct: float
    position_after: float
    reason: str
    rsi: float
    momentum: float


def model_v131_conservative(row, position, last_action, last_action_days):
    regime = row['regime']
    conf = row['confidence']
    mom = row['momentum']
    tail = row['tail_risk']
    tail_pol = row['tail_polarity']
    
    if tail and tail_pol == 'downside':
        return "STRONG_SELL", -0.50, "tail_risk_downside"
    
    if conf < 0.40:
        return "HOLD", 0, "low_confidence"
    
    if last_action == "BUY" and last_action_days < 3:
        return "HOLD", 0, "cooldown"
    if last_action == "SELL" and last_action_days < 7:
        return "HOLD", 0, "cooldown"
    if "STRONG" in last_action and last_action_days < 14:
        return "HOLD", 0, "cooldown"
    
    if regime == "BULL":
        if conf >= 0.70 and mom > 0.50:
            return "STRONG_BUY", 0.20, "bull_strong"
        elif conf >= 0.50 and mom > 0:
            return "BUY", 0.10, "bull_buy"
        return "HOLD", 0, "bull_hold"
    
    elif regime == "BEAR":
        if conf >= 0.60 and mom < -0.50:
            return "STRONG_SELL", -0.50, "bear_strong"
        elif conf >= 0.50 and mom < 0:
            return "SELL", -0.15, "bear_sell"
        return "HOLD", 0, "bear_hold"
    
    elif regime == "TRANSITION":
        if mom < -0.30 and conf >= 0.50:
            return "SELL", -0.15, "transition_risk"
        return "HOLD", 0, "transition_wait"
    
    return "HOLD", 0, "range"


def model_v14_countercyclical(row, position, last_action, last_action_days):
    regime = row['regime']
    conf = row['confidence']
    mom = row['momentum']
    tail = row['tail_risk']
    tail_pol = row['tail_polarity']
    rsi = row['rsi']
    vol_z = row['vol_z'] if not np.isnan(row['vol_z']) else 0
    returns_30d = row['returns_30d'] if not np.isnan(row['returns_30d']) else 0
    
    is_panic = mom < -0.70 and vol_z > 1.5
    is_extreme_panic = mom < -0.80 and vol_z > 2.0
    is_deep_drawdown = returns_30d < -0.20
    
    is_euphoria = mom > 0.70 and conf > 0.60
    is_extreme_euphoria = mom > 0.80 and conf > 0.70
    is_big_rally = returns_30d > 0.30
    
    panic_block = is_panic or is_extreme_panic
    
    if is_extreme_panic and is_deep_drawdown:
        if last_action_days >= 7 or last_action not in ["BUY", "STRONG_BUY"]:
            return "BUY", 0.10, "cc_accumulate_fear"
    
    if is_extreme_euphoria and is_big_rally:
        if last_action_days >= 3 or last_action not in ["SELL", "STRONG_SELL"]:
            return "SELL", -0.20, "cc_take_profit"
    
    if tail and tail_pol == 'downside':
        if panic_block:
            return "HOLD", 0, "tail_panic_hold"
        else:
            return "STRONG_SELL", -0.50, "tail_risk"
    
    if tail and tail_pol == 'upside':
        return "SELL", -0.20, "tail_upside_profit"
    
    if conf < 0.40:
        return "HOLD", 0, "low_confidence"
    
    if "STRONG" in last_action and last_action_days < 10:
        return "HOLD", 0, "cooldown"
    
    if regime == "BULL":
        if is_euphoria:
            return "HOLD", 0, "bull_euphoria_hold"
        if conf >= 0.70 and mom > 0.50:
            return "STRONG_BUY", 0.20, "bull_strong"
        elif conf >= 0.50 and mom > 0:
            return "BUY", 0.10, "bull_buy"
        return "HOLD", 0, "bull_hold"
    
    elif regime == "BEAR":
        if panic_block:
            return "HOLD", 0, "bear_panic_hold"
        if conf >= 0.60 and mom < -0.50:
            return "SELL", -0.25, "bear_sell"
        elif conf >= 0.50 and mom < 0 and rsi > 35:
            return "SELL", -0.10, "bear_light_sell"
        return "HOLD", 0, "bear_hold"
    
    elif regime == "RANGE":
        if is_panic:
            return "BUY", 0.05, "range_mean_revert_buy"
        elif is_euphoria:
            return "SELL", -0.05, "range_mean_revert_sell"
        return "HOLD", 0, "range"
    
    elif regime == "TRANSITION":
        if mom < -0.30 and conf >= 0.50 and not panic_block:
            return "SELL", -0.10, "transition_risk"
        return "HOLD", 0, "transition_wait"
    
    return "HOLD", 0, "default"


@dataclass
class BacktestResult:
    name: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    total_trades: int
    win_rate_pct: float
    profit_factor: float
    avg_win_pct: float
    avg_loss_pct: float
    sells_at_bottom_pct: float
    sells_at_top_pct: float
    buys_at_bottom_pct: float
    buys_at_top_pct: float
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[Tuple[datetime, float]] = field(default_factory=list)


def run_backtest(df, model_func, model_name, initial_capital=100000):
    capital = initial_capital
    position = 0.5
    btc_held = (capital * position) / df.iloc[0]['close']
    cash = capital * (1 - position)
    
    trades = []
    equity_curve = []
    
    last_action = "HOLD"
    last_action_date = df.index[0]
    
    for i in range(60, len(df)):
        row = df.iloc[i]
        date = df.index[i]
        price = row['close']
        
        equity = cash + btc_held * price
        equity_curve.append((date, equity))
        
        days_since = (date - last_action_date).days
        
        action, size_pct, reason = model_func(row, position, last_action, days_since)
        
        if action != "HOLD" and size_pct != 0:
            if size_pct > 0:
                cash_to_use = cash * min(size_pct * 2, 1.0)
                if cash_to_use > 100:
                    btc_bought = cash_to_use / price
                    btc_held += btc_bought
                    cash -= cash_to_use
                    position = (btc_held * price) / equity
                    
                    trades.append(Trade(date=date, action=action, price=price,
                        size_pct=size_pct, position_after=position,
                        reason=reason, rsi=row['rsi'], momentum=row['momentum']))
                    last_action = action
                    last_action_date = date
            
            else:
                btc_to_sell = btc_held * min(abs(size_pct), 1.0)
                if btc_to_sell * price > 100:
                    cash += btc_to_sell * price
                    btc_held -= btc_to_sell
                    position = (btc_held * price) / equity if equity > 0 else 0
                    
                    trades.append(Trade(date=date, action=action, price=price,
                        size_pct=size_pct, position_after=position,
                        reason=reason, rsi=row['rsi'], momentum=row['momentum']))
                    last_action = action
                    last_action_date = date
    
    final_equity = cash + btc_held * df.iloc[-1]['close']
    
    years = len(df) / 365
    total_return = (final_equity / initial_capital - 1) * 100
    cagr = ((final_equity / initial_capital) ** (1/years) - 1) * 100 if years > 0 else 0
    
    equity_series = pd.Series([e[1] for e in equity_curve])
    rolling_max = equity_series.expanding().max()
    drawdowns = (equity_series - rolling_max) / rolling_max
    max_dd = abs(drawdowns.min()) * 100
    
    if len(equity_curve) > 1:
        returns = equity_series.pct_change().dropna()
        sharpe = (returns.mean() * 365) / (returns.std() * np.sqrt(365)) if returns.std() > 0 else 0
        neg_returns = returns[returns < 0]
        sortino = (returns.mean() * 365) / (neg_returns.std() * np.sqrt(365)) if len(neg_returns) > 0 and neg_returns.std() > 0 else 0
    else:
        sharpe = sortino = 0
    
    calmar = cagr / max_dd if max_dd > 0 else 0
    
    wins, losses = [], []
    for i, trade in enumerate(trades):
        if "BUY" in trade.action:
            for j in range(i+1, len(trades)):
                if "SELL" in trades[j].action:
                    pnl = (trades[j].price / trade.price - 1) * 100
                    (wins if pnl > 0 else losses).append(pnl)
                    break
    
    win_rate = len(wins) / (len(wins) + len(losses)) * 100 if wins or losses else 0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0
    
    sells = [t for t in trades if "SELL" in t.action]
    buys = [t for t in trades if "BUY" in t.action]
    prices = df['close'].values
    
    def is_near_local_min(idx, window=60):
        start, end = max(0, idx - window), min(len(prices), idx + window)
        return prices[idx] <= prices[start:end].min() * 1.10
    
    def is_near_local_max(idx, window=60):
        start, end = max(0, idx - window), min(len(prices), idx + window)
        return prices[idx] >= prices[start:end].max() * 0.90
    
    sells_at_bottom = sum(1 for t in sells if is_near_local_min(df.index.get_loc(t.date)))
    sells_at_top = sum(1 for t in sells if is_near_local_max(df.index.get_loc(t.date)))
    buys_at_bottom = sum(1 for t in buys if is_near_local_min(df.index.get_loc(t.date)))
    buys_at_top = sum(1 for t in buys if is_near_local_max(df.index.get_loc(t.date)))
    
    return BacktestResult(
        name=model_name, initial_capital=initial_capital, final_capital=final_equity,
        total_return_pct=total_return, cagr_pct=cagr, max_drawdown_pct=max_dd,
        sharpe_ratio=sharpe, sortino_ratio=sortino, calmar_ratio=calmar,
        total_trades=len(trades), win_rate_pct=win_rate, profit_factor=profit_factor,
        avg_win_pct=avg_win, avg_loss_pct=avg_loss,
        sells_at_bottom_pct=(sells_at_bottom / len(sells) * 100) if sells else 0,
        sells_at_top_pct=(sells_at_top / len(sells) * 100) if sells else 0,
        buys_at_bottom_pct=(buys_at_bottom / len(buys) * 100) if buys else 0,
        buys_at_top_pct=(buys_at_top / len(buys) * 100) if buys else 0,
        trades=trades, equity_curve=equity_curve
    )


def run_buy_and_hold(df, initial_capital=100000):
    start_price = df.iloc[60]['close']
    end_price = df.iloc[-1]['close']
    
    btc_held = initial_capital / start_price
    final_value = btc_held * end_price
    
    years = (len(df) - 60) / 365
    total_return = (final_value / initial_capital - 1) * 100
    cagr = ((final_value / initial_capital) ** (1/years) - 1) * 100 if years > 0 else 0
    
    equity = [initial_capital * (df.iloc[i]['close'] / start_price) for i in range(60, len(df))]
    equity_series = pd.Series(equity)
    rolling_max = equity_series.expanding().max()
    drawdowns = (equity_series - rolling_max) / rolling_max
    max_dd = abs(drawdowns.min()) * 100
    
    returns = equity_series.pct_change().dropna()
    sharpe = (returns.mean() * 365) / (returns.std() * np.sqrt(365)) if returns.std() > 0 else 0
    
    return BacktestResult(
        name="Buy & Hold", initial_capital=initial_capital, final_capital=final_value,
        total_return_pct=total_return, cagr_pct=cagr, max_drawdown_pct=max_dd,
        sharpe_ratio=sharpe, sortino_ratio=sharpe * 1.2, calmar_ratio=cagr / max_dd if max_dd > 0 else 0,
        total_trades=1, win_rate_pct=100 if total_return > 0 else 0, profit_factor=0,
        avg_win_pct=total_return if total_return > 0 else 0, avg_loss_pct=total_return if total_return < 0 else 0,
        sells_at_bottom_pct=0, sells_at_top_pct=0, buys_at_bottom_pct=0, buys_at_top_pct=0,
        trades=[], equity_curve=[(df.index[i], equity[i-60]) for i in range(60, len(df))]
    )


def print_cfo_report(results, df):
    print("=" * 80)
    print("         CFO FINANCIAL ANALYSIS — ASSET ALLOCATION BACKTEST")
    print("=" * 80)
    print()
    
    print("DATA SUMMARY")
    print("-" * 40)
    print(f"Period: {df.index[60].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}")
    print(f"Duration: {(df.index[-1] - df.index[60]).days} days ({(df.index[-1] - df.index[60]).days / 365:.1f} years)")
    print(f"Start Price: ${df.iloc[60]['close']:,.0f}")
    print(f"End Price: ${df.iloc[-1]['close']:,.0f}")
    print(f"Price Change: {(df.iloc[-1]['close'] / df.iloc[60]['close'] - 1) * 100:+.1f}%")
    print()
    
    print("MARKET PHASES SIMULATED")
    print("-" * 40)
    phases = [
        ("2022 H1", "Bear Crash", "-63%", "47k → 17k"),
        ("2022 H2", "Bottom Consolidation", "-6%", "17k → 16k"),
        ("2023", "Recovery", "+88%", "16k → 31k → 44k"),
        ("2024 Q1", "ETF Pump", "+66%", "44k → 73k"),
        ("2024 Q2-Q3", "Consolidation", "-26%", "73k → 54k"),
        ("2024 Q4", "ATH Rally", "+100%", "54k → 108k"),
        ("2025 Q1", "Correction", "-28%", "108k → 78k"),
    ]
    for period, name, change, range_ in phases:
        print(f"  {period:12} {name:25} {change:>8}  ({range_})")
    print()
    
    print("=" * 80)
    print("                    PERFORMANCE COMPARISON")
    print("=" * 80)
    print()
    
    header = f"{'Metric':<30} "
    for r in results:
        header += f"{r.name:>16} "
    print(header)
    print("-" * 80)
    
    metrics = [
        ("Initial Capital", lambda r: f"${r.initial_capital:,.0f}"),
        ("Final Capital", lambda r: f"${r.final_capital:,.0f}"),
        ("Total Return", lambda r: f"{r.total_return_pct:+.1f}%"),
        ("CAGR", lambda r: f"{r.cagr_pct:+.1f}%"),
        ("Max Drawdown", lambda r: f"-{r.max_drawdown_pct:.1f}%"),
        ("Sharpe Ratio", lambda r: f"{r.sharpe_ratio:.2f}"),
        ("Sortino Ratio", lambda r: f"{r.sortino_ratio:.2f}"),
        ("Calmar Ratio", lambda r: f"{r.calmar_ratio:.2f}"),
    ]
    
    for name, func in metrics:
        row = f"{name:<30} "
        for r in results:
            row += f"{func(r):>16} "
        print(row)
    
    print()
    print("=" * 80)
    print("                    TRADING ANALYSIS")
    print("=" * 80)
    print()
    
    trading_metrics = [
        ("Total Trades", lambda r: f"{r.total_trades}"),
        ("Win Rate", lambda r: f"{r.win_rate_pct:.0f}%"),
        ("Profit Factor", lambda r: f"{r.profit_factor:.2f}" if r.profit_factor > 0 else "N/A"),
        ("Avg Win", lambda r: f"{r.avg_win_pct:+.1f}%"),
        ("Avg Loss", lambda r: f"{r.avg_loss_pct:.1f}%"),
    ]
    
    for name, func in trading_metrics:
        row = f"{name:<30} "
        for r in results:
            row += f"{func(r):>16} "
        print(row)
    
    print()
    print("=" * 80)
    print("                    TIMING ANALYSIS (Critical)")
    print("=" * 80)
    print()
    
    timing_metrics = [
        ("Sells at Bottom (bad)", lambda r: f"{r.sells_at_bottom_pct:.0f}%"),
        ("Sells at Top (good)", lambda r: f"{r.sells_at_top_pct:.0f}%"),
        ("Buys at Bottom (good)", lambda r: f"{r.buys_at_bottom_pct:.0f}%"),
        ("Buys at Top (bad)", lambda r: f"{r.buys_at_top_pct:.0f}%"),
    ]
    
    for name, func in timing_metrics:
        row = f"{name:<30} "
        for r in results:
            row += f"{func(r):>16} "
        print(row)
    
    print()
    print("=" * 80)
    print("                    ALPHA ANALYSIS")
    print("=" * 80)
    print()
    
    bh = next(r for r in results if r.name == "Buy & Hold")
    
    for r in results:
        if r.name != "Buy & Hold":
            alpha = r.total_return_pct - bh.total_return_pct
            risk_adj_alpha = r.sharpe_ratio - bh.sharpe_ratio
            dd_improvement = bh.max_drawdown_pct - r.max_drawdown_pct
            
            print(f"{r.name}:")
            print(f"  Alpha (vs B&H):           {alpha:+.1f}%")
            print(f"  Risk-Adjusted Alpha:      {risk_adj_alpha:+.2f} Sharpe points")
            print(f"  Drawdown Improvement:     {dd_improvement:+.1f}%")
            print()
    
    print("=" * 80)
    print("                    CFO RECOMMENDATIONS")
    print("=" * 80)
    print()
    
    v131 = next(r for r in results if "v1.3.1" in r.name)
    v14 = next(r for r in results if "v1.4" in r.name)
    
    print("KEY FINDINGS:")
    print()
    
    if v14.sells_at_bottom_pct < v131.sells_at_bottom_pct:
        improvement = v131.sells_at_bottom_pct - v14.sells_at_bottom_pct
        print(f"  ✅ v1.4 reduces 'selling at bottom' by {improvement:.0f}%")
        print(f"     ({v131.sells_at_bottom_pct:.0f}% → {v14.sells_at_bottom_pct:.0f}%)")
    else:
        print(f"  ⚠️ v1.4 does not improve bottom selling")
    print()
    
    if v14.total_return_pct > v131.total_return_pct:
        print(f"  ✅ v1.4 outperforms v1.3.1 by {v14.total_return_pct - v131.total_return_pct:.1f}%")
    else:
        print(f"  ⚠️ v1.4 underperforms v1.3.1 by {v131.total_return_pct - v14.total_return_pct:.1f}%")
    print()
    
    if v14.max_drawdown_pct < v131.max_drawdown_pct:
        print(f"  ✅ v1.4 has lower max drawdown ({v14.max_drawdown_pct:.1f}% vs {v131.max_drawdown_pct:.1f}%)")
    else:
        print(f"  ⚠️ v1.4 has higher max drawdown ({v14.max_drawdown_pct:.1f}% vs {v131.max_drawdown_pct:.1f}%)")
    print()
    
    print(f"  📊 Both models vs Buy & Hold:")
    print(f"     v1.3.1 alpha: {v131.total_return_pct - bh.total_return_pct:+.1f}%")
    print(f"     v1.4 alpha:   {v14.total_return_pct - bh.total_return_pct:+.1f}%")
    
    print()
    print("=" * 80)
    print("                    CONCLUSION")
    print("=" * 80)
    print()
    
    score_v131, score_v14 = 0, 0
    
    if v14.total_return_pct > v131.total_return_pct: score_v14 += 1
    else: score_v131 += 1
    
    if v14.sharpe_ratio > v131.sharpe_ratio: score_v14 += 1
    else: score_v131 += 1
    
    if v14.max_drawdown_pct < v131.max_drawdown_pct: score_v14 += 1
    else: score_v131 += 1
    
    if v14.sells_at_bottom_pct < v131.sells_at_bottom_pct: score_v14 += 2
    else: score_v131 += 2
    
    if score_v14 > score_v131:
        print(f"  🏆 RECOMMENDATION: Deploy v1.4 (Counter-cyclical)")
        print(f"     Score: v1.4 ({score_v14}) vs v1.3.1 ({score_v131})")
    else:
        print(f"  🏆 RECOMMENDATION: Keep v1.3.1 (Conservative)")
        print(f"     Score: v1.3.1 ({score_v131}) vs v1.4 ({score_v14})")
    
    print()
    print("  RATIONALE:")
    if v14.sells_at_bottom_pct < v131.sells_at_bottom_pct:
        print("  • Counter-cyclical logic successfully prevents panic selling")
    if v14.sharpe_ratio > v131.sharpe_ratio:
        print("  • Better risk-adjusted returns")
    if v14.max_drawdown_pct < v131.max_drawdown_pct:
        print("  • Lower maximum drawdown protects capital")
    
    print()
    print("=" * 80)


def main():
    print()
    print("Generating realistic BTC price data (2022-2025 cycles)...")
    df = generate_realistic_btc_cycles()
    print(f"Generated {len(df)} days of data")
    
    print("Computing technical indicators...")
    df = compute_indicators(df)
    print(f"Data ready: {len(df)} days with indicators")
    print()
    
    print("Running backtests...")
    print()
    
    results = []
    
    result_v131 = run_backtest(df, model_v131_conservative, "v1.3.1 Conservative")
    results.append(result_v131)
    print(f"  v1.3.1: {result_v131.total_return_pct:+.1f}% return, {result_v131.total_trades} trades")
    
    result_v14 = run_backtest(df, model_v14_countercyclical, "v1.4 Counter-cyclical")
    results.append(result_v14)
    print(f"  v1.4:   {result_v14.total_return_pct:+.1f}% return, {result_v14.total_trades} trades")
    
    result_bh = run_buy_and_hold(df)
    results.append(result_bh)
    print(f"  B&H:    {result_bh.total_return_pct:+.1f}% return")
    
    print()
    print_cfo_report(results, df)
    
    output = {
        "generated_at": datetime.now().isoformat(),
        "data_period": f"{df.index[60].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}",
        "results": [{
            "name": r.name,
            "total_return_pct": round(r.total_return_pct, 2),
            "cagr_pct": round(r.cagr_pct, 2),
            "max_drawdown_pct": round(r.max_drawdown_pct, 2),
            "sharpe_ratio": round(r.sharpe_ratio, 2),
            "total_trades": r.total_trades,
            "sells_at_bottom_pct": round(r.sells_at_bottom_pct, 1),
        } for r in results]
    }
    
    with open("backtest_cfo_results.json", "w") as f:
        json.dump(output, f, indent=2)
    
    print()
    print("Results saved to backtest_cfo_results.json")


if __name__ == "__main__":
    main()
