"""
LP Weekly Digest v2.1
Минималистичный еженедельный отчёт по LP позициям.

Формула реальной эффективности:
Adjusted TVL Change = End TVL - Start TVL + Withdrawals
Real PnL = Fees + Adjusted TVL Change
Real Efficiency = Real PnL / Start TVL * 100%

Weekly withdrawals: $300 ($200 child savings + $100 personal)
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Files
HISTORY_FILE = "state/lp_history.json"
POSITIONS_FILE = "state/lp_positions.json"
DIGEST_FILE = "state/lp_weekly_digest.json"
DIGEST_HISTORY_FILE = "state/lp_digest_history.json"

# Weekly withdrawals (not reinvested)
WEEKLY_WITHDRAWAL = 300  # $200 child savings + $100 personal


def load_digest_history() -> List[dict]:
    """Load accumulated weekly digests"""
    if not os.path.exists(DIGEST_HISTORY_FILE):
        return []
    try:
        with open(DIGEST_HISTORY_FILE, 'r') as f:
            return json.load(f).get("weeks", [])
    except Exception as e:
        logger.warning(f"Error loading digest history: {e}")
        return []


def save_digest_to_history(stats: dict):
    """Append weekly digest to history"""
    if not stats.get("has_data"):
        return
    
    history = load_digest_history()
    
    # Create week record
    period = stats.get("period", {})
    portfolio = stats.get("portfolio", {})
    
    week_record = {
        "week_end": period.get("end"),
        "week_start": period.get("start"),
        "days": period.get("days"),
        "start_tvl": portfolio.get("start_tvl", 0),
        "end_tvl": portfolio.get("end_tvl", 0),
        "tvl_change": portfolio.get("tvl_change", 0),
        "withdrawals": portfolio.get("withdrawals", 0),
        "fees_earned": portfolio.get("fees_earned", 0),
        "real_pnl": portfolio.get("real_pnl", 0),
        "efficiency_pct": portfolio.get("real_efficiency_pct", 0),
        "positions_count": stats.get("positions", {}).get("count", 0),
        "positions_in_range": stats.get("positions", {}).get("in_range", 0),
        "wallets": stats.get("wallets", []),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # Check if this week already exists (by week_end)
    existing_idx = None
    for i, w in enumerate(history):
        if w.get("week_end") == week_record["week_end"]:
            existing_idx = i
            break
    
    if existing_idx is not None:
        # Update existing
        history[existing_idx] = week_record
        logger.info(f"✓ Updated week {week_record['week_end']} in history")
    else:
        # Append new
        history.append(week_record)
        logger.info(f"✓ Added week {week_record['week_end']} to history")
    
    # Sort by date
    history.sort(key=lambda x: x.get("week_end", ""))
    
    # Save
    os.makedirs(os.path.dirname(DIGEST_HISTORY_FILE), exist_ok=True)
    with open(DIGEST_HISTORY_FILE, 'w') as f:
        json.dump({"weeks": history, "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)


def calculate_period_summary(weeks: List[dict], last_n_weeks: int = None) -> dict:
    """
    Calculate summary for a period (last N weeks or all time).
    
    Returns aggregated stats: total fees, total PnL, avg efficiency, etc.
    """
    if not weeks:
        return {"has_data": False}
    
    if last_n_weeks:
        weeks = weeks[-last_n_weeks:]
    
    total_fees = sum(w.get("fees_earned", 0) for w in weeks)
    total_pnl = sum(w.get("real_pnl", 0) for w in weeks)
    total_tvl_change = sum(w.get("tvl_change", 0) for w in weeks)
    total_withdrawals = sum(w.get("withdrawals", 0) for w in weeks)
    
    # Recalculate PnL with withdrawals for older weeks that didn't have them
    if total_withdrawals == 0 and len(weeks) > 0:
        total_withdrawals = WEEKLY_WITHDRAWAL * len(weeks)
        total_pnl = total_fees + total_tvl_change + total_withdrawals
    
    # Average efficiency
    efficiencies = [w.get("efficiency_pct", 0) for w in weeks]
    avg_efficiency = sum(efficiencies) / len(efficiencies) if efficiencies else 0
    
    # Start TVL (first week) and End TVL (last week)
    start_tvl = weeks[0].get("start_tvl", 0) if weeks else 0
    end_tvl = weeks[-1].get("end_tvl", 0) if weeks else 0
    
    # Period dates
    period_start = weeks[0].get("week_start", "") if weeks else ""
    period_end = weeks[-1].get("week_end", "") if weeks else ""
    
    return {
        "has_data": True,
        "weeks_count": len(weeks),
        "period_start": period_start,
        "period_end": period_end,
        "start_tvl": start_tvl,
        "end_tvl": end_tvl,
        "total_tvl_change": total_tvl_change,
        "total_withdrawals": total_withdrawals,
        "total_fees": total_fees,
        "total_pnl": total_pnl,
        "avg_efficiency_pct": avg_efficiency
    }


def load_history() -> List[dict]:
    """Load history snapshots"""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f).get("snapshots", [])
    except Exception as e:
        logger.warning(f"Error loading history: {e}")
        return []


def load_positions() -> List[dict]:
    """Load current positions"""
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, 'r') as f:
            return json.load(f).get("positions", [])
    except Exception as e:
        logger.warning(f"Error loading positions: {e}")
        return []


def get_week_snapshots(snapshots: List[dict], days: int = 7) -> Tuple[Optional[dict], Optional[dict]]:
    """Get start and end snapshots for the week"""
    if len(snapshots) < 2:
        return None, None
    
    today = datetime.now(timezone.utc).date()
    week_ago = today - timedelta(days=days)
    
    # Find closest to week_ago
    start_snapshot = None
    for s in snapshots:
        s_date = datetime.strptime(s.get("date", "2000-01-01"), "%Y-%m-%d").date()
        if s_date <= week_ago:
            start_snapshot = s
        elif start_snapshot is None:
            start_snapshot = s
            break
    
    # Latest as end
    end_snapshot = snapshots[-1]
    
    return start_snapshot, end_snapshot


def calculate_real_efficiency(start_tvl: float, end_tvl: float, fees_earned: float, 
                              withdrawals: float = 0) -> dict:
    """
    Calculate real efficiency adjusted for withdrawals.
    
    Formula:
    - Raw TVL Change = End TVL - Start TVL
    - Adjusted TVL Change = Raw TVL Change + Withdrawals (add back what was taken out)
    - Real PnL = Fees + Adjusted TVL Change
    - Real Efficiency = Real PnL / Start TVL * 100%
    """
    raw_tvl_change = end_tvl - start_tvl
    adjusted_tvl_change = raw_tvl_change + withdrawals
    real_pnl = fees_earned + adjusted_tvl_change
    
    if start_tvl > 0:
        real_efficiency_pct = (real_pnl / start_tvl) * 100
    else:
        real_efficiency_pct = 0
    
    return {
        "start_tvl": start_tvl,
        "end_tvl": end_tvl,
        "tvl_change": raw_tvl_change,
        "withdrawals": withdrawals,
        "adjusted_tvl_change": adjusted_tvl_change,
        "fees_earned": fees_earned,
        "real_pnl": real_pnl,
        "real_efficiency_pct": real_efficiency_pct
    }


def calculate_weekly_stats(snapshots: List[dict]) -> dict:
    """Calculate weekly statistics with real efficiency"""
    
    start_snapshot, end_snapshot = get_week_snapshots(snapshots)
    
    if not start_snapshot or not end_snapshot:
        return {"has_data": False, "reason": "Недостаточно данных"}
    
    # Period
    start_date = start_snapshot.get("date", "")
    end_date = end_snapshot.get("date", "")
    days = (datetime.strptime(end_date, "%Y-%m-%d") - 
            datetime.strptime(start_date, "%Y-%m-%d")).days
    
    # Portfolio totals
    start_tvl = start_snapshot.get("tvl", 0)
    end_tvl = end_snapshot.get("tvl", 0)
    
    # Fees earned (cumulative difference)
    start_cumulative = start_snapshot.get("fees_cumulative", 0)
    end_cumulative = end_snapshot.get("fees_cumulative", 0)
    fees_earned = end_cumulative - start_cumulative
    
    # Withdrawals pro-rated by period length
    withdrawals = WEEKLY_WITHDRAWAL * (days / 7.0)
    
    # Real efficiency for portfolio (adjusted for withdrawals)
    portfolio_efficiency = calculate_real_efficiency(start_tvl, end_tvl, fees_earned, withdrawals)
    
    # Wallet-level analysis
    start_wallets = start_snapshot.get("by_wallet", {})
    end_wallets = end_snapshot.get("by_wallet", {})
    end_fees = end_snapshot.get("by_wallet_fees", {})
    
    # Get all wallets (union of start and end)
    all_wallets = set(start_wallets.keys()) | set(end_wallets.keys())
    
    wallet_stats = []
    for wallet in all_wallets:
        w_start_tvl = start_wallets.get(wallet, 0)
        w_end_tvl = end_wallets.get(wallet, 0)
        w_tvl_change = w_end_tvl - w_start_tvl
        
        # Current uncollected fees for this wallet
        w_uncollected = end_fees.get(wallet, 0)
        
        # For wallet-level, we approximate fees earned as proportion of total
        if start_tvl > 0:
            wallet_share = w_start_tvl / start_tvl
            w_fees_estimated = fees_earned * wallet_share
        else:
            w_fees_estimated = 0
        
        w_real_pnl = w_fees_estimated + w_tvl_change
        w_efficiency = (w_real_pnl / w_start_tvl * 100) if w_start_tvl > 0 else 0
        
        wallet_stats.append({
            "wallet": wallet,
            "start_tvl": w_start_tvl,
            "end_tvl": w_end_tvl,
            "tvl_change": w_tvl_change,
            "fees_estimated": w_fees_estimated,
            "uncollected": w_uncollected,
            "real_pnl": w_real_pnl,
            "efficiency_pct": w_efficiency
        })
    
    # Sort wallets by number (1, 2, 3, 4, 5)
    def wallet_sort_key(w):
        name = w.get("wallet", "")
        try:
            return int(name.split('_')[1])
        except:
            return 999
    
    wallet_stats.sort(key=wallet_sort_key)
    
    # Positions info
    positions_count = end_snapshot.get("positions_count", 0)
    positions_in_range = end_snapshot.get("positions_in_range", 0)
    
    return {
        "has_data": True,
        "period": {
            "start": start_date,
            "end": end_date,
            "days": days
        },
        "portfolio": portfolio_efficiency,
        "wallets": wallet_stats,
        "positions": {
            "count": positions_count,
            "in_range": positions_in_range
        },
        "fees_uncollected": end_snapshot.get("fees", 0)
    }


def format_weekly_digest(stats: dict) -> str:
    """Format minimalist weekly digest"""
    
    lines = []
    
    # Header
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    lines.append(f"LP WEEKLY | {now.strftime('%d.%m.%Y')}")
    lines.append("")
    
    if not stats.get("has_data"):
        lines.append(f"⚠️ {stats.get('reason', 'Нет данных')}")
        return "\n".join(lines)
    
    period = stats.get("period", {})
    portfolio = stats.get("portfolio", {})
    
    # Period
    lines.append(f"{period.get('start')} → {period.get('end')} ({period.get('days')}d)")
    lines.append("")
    
    # Portfolio summary
    start_tvl = portfolio.get("start_tvl", 0)
    end_tvl = portfolio.get("end_tvl", 0)
    tvl_change = portfolio.get("tvl_change", 0)
    fees = portfolio.get("fees_earned", 0)
    real_pnl = portfolio.get("real_pnl", 0)
    efficiency = portfolio.get("real_efficiency_pct", 0)
    
    lines.append("PORTFOLIO")
    lines.append(f"TVL: ${end_tvl:,.0f}")
    
    # TVL change
    tvl_sign = "+" if tvl_change >= 0 else ""
    lines.append(f"Δ TVL: {tvl_sign}${tvl_change:,.0f}")
    
    # Withdrawals
    withdrawals = portfolio.get("withdrawals", 0)
    if withdrawals > 0:
        lines.append(f"Выведено: -${withdrawals:,.0f} (ребёнок $200 + личные $100)")
    
    # Fees
    lines.append(f"Fees: +${fees:,.2f}")
    
    # Real PnL (adjusted for withdrawals)
    pnl_emoji = "✅" if real_pnl >= 0 else "❌"
    pnl_sign = "+" if real_pnl >= 0 else ""
    lines.append(f"{pnl_emoji} Real PnL: {pnl_sign}${real_pnl:,.2f} (с учётом выводов)")
    
    # Efficiency
    eff_sign = "+" if efficiency >= 0 else ""
    lines.append(f"Efficiency: {eff_sign}{efficiency:.2f}%")
    
    lines.append("")
    
    # Wallets (sorted 1-5)
    lines.append("BY WALLET")
    
    wallets = stats.get("wallets", [])
    for w in wallets:
        name = w.get("wallet", "")
        w_tvl = w.get("end_tvl", 0)
        w_pnl = w.get("real_pnl", 0)
        w_eff = w.get("efficiency_pct", 0)
        
        # Format wallet number
        num = name.split('_')[1] if '_' in name else name
        
        pnl_sign = "+" if w_pnl >= 0 else ""
        eff_sign = "+" if w_eff >= 0 else ""
        
        emoji = "🟢" if w_pnl >= 0 else "🔴"
        
        lines.append(f"{num}. ${w_tvl:,.0f} | {pnl_sign}${w_pnl:,.0f} ({eff_sign}{w_eff:.1f}%) {emoji}")
    
    lines.append("")
    
    # Positions
    pos = stats.get("positions", {})
    count = pos.get("count", 0)
    in_range = pos.get("in_range", 0)
    range_pct = (in_range / count * 100) if count > 0 else 0
    
    lines.append(f"Positions: {in_range}/{count} in range ({range_pct:.0f}%)")
    
    # Uncollected fees
    uncollected = stats.get("fees_uncollected", 0)
    if uncollected > 0:
        lines.append(f"Uncollected: ${uncollected:,.2f}")
    
    # Historical summary (if available)
    history = stats.get("history_summary")
    if history and history.get("has_data") and history.get("weeks_count", 0) > 1:
        lines.append("")
        lines.append("─" * 20)
        
        weeks_count = history.get("weeks_count", 0)
        total_pnl = history.get("total_pnl", 0)
        total_fees = history.get("total_fees", 0)
        avg_eff = history.get("avg_efficiency_pct", 0)
        
        pnl_sign = "+" if total_pnl >= 0 else ""
        eff_sign = "+" if avg_eff >= 0 else ""
        
        lines.append(f"ALL TIME ({weeks_count}w)")
        lines.append(f"Total Fees: +${total_fees:,.2f}")
        
        total_withdrawals = history.get("total_withdrawals", 0)
        if total_withdrawals > 0:
            lines.append(f"Total Выведено: ${total_withdrawals:,.0f}")
        
        lines.append(f"Total PnL: {pnl_sign}${total_pnl:,.2f}")
        lines.append(f"Avg Eff: {eff_sign}{avg_eff:.2f}%/week")
    
    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    """Send message to Telegram"""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        logger.warning("Telegram credentials not set")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        response = requests.post(url, data={
            "chat_id": chat_id, 
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        
        if response.status_code == 200:
            logger.info("✓ Telegram sent")
            return True
        else:
            logger.error(f"Telegram error: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Telegram exception: {e}")
        return False


def save_digest(stats: dict):
    """Save digest to file"""
    os.makedirs(os.path.dirname(DIGEST_FILE), exist_ok=True)
    
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stats": stats
    }
    
    with open(DIGEST_FILE, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    
    logger.info(f"✓ Saved to {DIGEST_FILE}")


def main():
    """Main entry point"""
    logger.info("=" * 50)
    logger.info("LP WEEKLY DIGEST v2.1")
    logger.info("=" * 50)
    
    # Load data
    snapshots = load_history()
    logger.info(f"Loaded {len(snapshots)} snapshots")
    
    # Calculate weekly stats
    stats = calculate_weekly_stats(snapshots)
    
    # Save current week to history
    save_digest_to_history(stats)
    
    # Load full history and calculate summary
    history = load_digest_history()
    logger.info(f"Digest history: {len(history)} weeks")
    
    if history:
        history_summary = calculate_period_summary(history)
        stats["history_summary"] = history_summary
    
    # Save current digest
    save_digest(stats)
    
    # Format
    report = format_weekly_digest(stats)
    
    print("\n" + report + "\n")
    
    # Send
    send_telegram(report)
    
    logger.info("Done!")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
