"""
LP Intelligence System - Unified Runner with History
Version: 2.0.0

Объединяет:
1. LP Monitor - мониторинг позиций
2. LP Opportunities - поиск лучших пулов  
3. LP Advisor - AI рекомендации
4. History - хранение и аналитика TVL

Расписание: 7:00 и 19:00 MSK (04:00 и 16:00 UTC)
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

import requests

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

HISTORY_FILE = "state/lp_history.json"
MAX_HISTORY_DAYS = 90  # Keep 90 days of history

# ═══════════════════════════════════════════════════════════════════════════════
# HISTORY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DailySnapshot:
    """Daily portfolio snapshot"""
    date: str  # YYYY-MM-DD
    timestamp: str  # ISO format
    tvl: float
    fees: float  # Current uncollected fees
    fees_cumulative: float  # All fees earned ever (doesn't reset on harvest)
    positions_count: int
    positions_in_range: int
    by_wallet: Dict[str, float]  # wallet_name -> tvl
    by_wallet_fees: Dict[str, float]  # wallet_name -> fees


def load_history() -> List[dict]:
    """Load history from file"""
    if not os.path.exists(HISTORY_FILE):
        return []
    
    try:
        with open(HISTORY_FILE, 'r') as f:
            data = json.load(f)
            return data.get("snapshots", [])
    except Exception as e:
        logger.warning(f"Error loading history: {e}")
        return []


def save_history(snapshots: List[dict]):
    """Save history to file"""
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    
    # Keep only last MAX_HISTORY_DAYS
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=MAX_HISTORY_DAYS)).strftime("%Y-%m-%d")
    snapshots = [s for s in snapshots if s.get("date", "") >= cutoff_date]
    
    with open(HISTORY_FILE, 'w') as f:
        json.dump({"snapshots": snapshots, "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    
    logger.info(f"History saved: {len(snapshots)} snapshots")


def add_snapshot(tvl: float, fees: float, positions_count: int, in_range: int, 
                 by_wallet: Dict[str, float], by_wallet_fees: Dict[str, float]):
    """Add today's snapshot to history with cumulative fees tracking"""
    snapshots = load_history()
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).isoformat()
    
    # Calculate cumulative fees
    # Logic: if current fees < previous fees, user did harvest
    # We add the positive delta to cumulative, never subtract
    fees_cumulative = fees  # default for first snapshot
    
    if snapshots:
        # Find the most recent snapshot (any date)
        prev_snapshot = snapshots[-1]
        prev_fees = prev_snapshot.get("fees", 0)
        prev_cumulative = prev_snapshot.get("fees_cumulative", prev_fees)
        prev_tvl = prev_snapshot.get("tvl", tvl)
        
        if fees >= prev_fees:
            # Fees grew normally - add the delta
            delta = fees - prev_fees
        else:
            # Fees dropped = harvest happened
            # Add current fees as new accumulation since harvest
            delta = fees
            logger.info(f"Detected harvest: fees dropped from ${prev_fees:.2f} to ${fees:.2f}")
        
        # SANITY CHECK: daily fee delta cannot exceed 10% of TVL
        # Catches data glitches, double-counting, and position resets
        max_daily_delta = max(prev_tvl, tvl) * 0.10
        if delta > max_daily_delta:
            logger.warning(f"⚠️ Fee delta ${delta:.2f} exceeds 10% of TVL (${max_daily_delta:.0f}). Capping.")
            delta = max_daily_delta
        
        fees_cumulative = prev_cumulative + delta
    
    # Check if today's snapshot exists
    existing_idx = None
    for i, s in enumerate(snapshots):
        if s.get("date") == today:
            existing_idx = i
            # Keep the higher cumulative (in case of multiple runs per day)
            prev_today_cumulative = s.get("fees_cumulative", 0)
            fees_cumulative = max(fees_cumulative, prev_today_cumulative)
            break
    
    snapshot = {
        "date": today,
        "timestamp": now,
        "tvl": tvl,
        "fees": fees,
        "fees_cumulative": fees_cumulative,
        "positions_count": positions_count,
        "positions_in_range": in_range,
        "by_wallet": by_wallet,
        "by_wallet_fees": by_wallet_fees,
    }
    
    if existing_idx is not None:
        # Update existing
        snapshots[existing_idx] = snapshot
    else:
        # Add new
        snapshots.append(snapshot)
    
    # Sort by date
    snapshots.sort(key=lambda x: x.get("date", ""))
    
    save_history(snapshots)
    
    logger.info(f"Snapshot saved: TVL=${tvl:.0f}, fees=${fees:.2f}, cumulative=${fees_cumulative:.2f}")
    return snapshot


def get_tvl_change(snapshots: List[dict], current_tvl: float, days: int) -> Tuple[Optional[float], Optional[float]]:
    """Get TVL change over N days. Returns (absolute_change, percent_change)"""
    if len(snapshots) < 2:
        return None, None
    
    target_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    
    # Find closest snapshot to target date
    past_snapshot = None
    for s in snapshots:
        if s.get("date", "") <= target_date:
            past_snapshot = s
    
    if not past_snapshot:
        return None, None
    
    past_tvl = past_snapshot.get("tvl", 0)
    if past_tvl == 0:
        return None, None
    
    abs_change = current_tvl - past_tvl
    pct_change = (abs_change / past_tvl) * 100
    
    return abs_change, pct_change


def calculate_portfolio_apy(snapshots: List[dict], current_tvl: float) -> Optional[float]:
    """Calculate portfolio APY based on cumulative fees earned"""
    if len(snapshots) < 2:
        return None
    
    # Get current snapshot (last one)
    current = snapshots[-1]
    current_cumulative = current.get("fees_cumulative", 0)
    
    # Try to find snapshot from ~7 days ago, then 3 days, then 1 day
    for days in [7, 3, 1]:
        target_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        
        past_snapshot = None
        for s in snapshots:
            if s.get("date", "") <= target_date:
                past_snapshot = s
        
        if past_snapshot and past_snapshot.get("date") != current.get("date"):
            past_cumulative = past_snapshot.get("fees_cumulative", 0)
            past_tvl = past_snapshot.get("tvl", 0)
            
            # Calculate fees earned in this period
            fees_earned = current_cumulative - past_cumulative
            
            if fees_earned > 0 and past_tvl > 0:
                # Average TVL over period
                avg_tvl = (current_tvl + past_tvl) / 2
                
                # Calculate actual days between snapshots
                from datetime import datetime as dt
                current_date = dt.strptime(current.get("date"), "%Y-%m-%d")
                past_date = dt.strptime(past_snapshot.get("date"), "%Y-%m-%d")
                actual_days = (current_date - past_date).days
                
                if actual_days > 0:
                    # Annualize
                    apy = (fees_earned / avg_tvl) * (365 / actual_days) * 100
                    logger.info(f"APY calc: ${fees_earned:.2f} earned over {actual_days}d, avg TVL ${avg_tvl:.0f} = {apy:.1f}%")
                    return apy
    
    return None


def format_change(abs_change: Optional[float], pct_change: Optional[float]) -> str:
    """Format change for display"""
    if abs_change is None or pct_change is None:
        return "нет данных"
    
    # Don't show +$0 changes (means no historical data)
    if abs_change == 0 and pct_change == 0:
        return "нет данных"
    
    sign = "+" if abs_change >= 0 else ""
    return f"{sign}${abs_change:,.0f} ({sign}{pct_change:.1f}%)"


# ═══════════════════════════════════════════════════════════════════════════════
# UNIFIED RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_monitor() -> Optional[dict]:
    """Run LP Monitor and return summary"""
    try:
        from lp_monitor import LPMonitor
        
        monitor = LPMonitor()
        
        if not monitor.web3_clients:
            logger.warning("No chains connected")
            return None
        
        positions = monitor.scan_all_positions()
        
        if not positions:
            logger.warning("No positions found")
            return None
        
        summary = monitor.get_summary()
        monitor.save_state()
        
        return {
            "positions": [asdict(p) for p in monitor.positions],
            "summary": asdict(summary),
            "tvl": summary.total_balance_usd,
            "fees": summary.total_uncollected_fees_usd,
            "count": summary.total_positions,
            "in_range": summary.positions_in_range,
            "by_wallet": summary.by_wallet,
            "failed_wallets": getattr(monitor, 'failed_wallets', []),
        }
        
    except Exception as e:
        logger.error(f"Monitor error: {e}")
        return None


def run_opportunities() -> Optional[dict]:
    """Run LP Opportunities Scanner and return top pools"""
    try:
        from lp_opportunities import LPOpportunitiesScanner
        from lp_config import REGIME_IL_PENALTY
        
        scanner = LPOpportunitiesScanner()
        opportunities = scanner.scan()
        
        if not opportunities:
            logger.warning("No opportunities found")
            return None
        
        scanner.save_state()
        rankings = scanner.get_rankings()
        
        # LP recommendation based on regime (Russian)
        regime = scanner.regime
        regime_penalty = REGIME_IL_PENALTY.get(regime, 0.4)
        
        lp_recommendations_ru = {
            "HARVEST": "Идеальные условия для LP. Используйте узкие диапазоны.",
            "RANGE": "Хорошие условия. Стандартные диапазоны работают.",
            "MEAN_REVERT": "Умеренные условия. Следите за границами.",
            "VOLATILE_CHOP": "Волатильность. Используйте широкие диапазоны.",
            "TRANSITION": "Переходный период. Осторожность.",
            "BULL": "Тренд вверх. Риск IL на short позициях.",
            "BEAR": "Тренд вниз. Высокий риск IL. Предпочитайте stable пары.",
            "TRENDING": "Сильный тренд. Минимизируйте LP экспозицию.",
            "BREAKOUT": "Пробой. Возможен сильный IL.",
            "CHURN": "Хаос. Лучше выйти из рисковых позиций.",
            "AVOID": "Избегайте LP. Высокий риск.",
        }
        
        return {
            "regime": regime,
            "regime_stale": scanner.regime_state.get("stale", False),
            "regime_penalty": regime_penalty,
            "lp_recommendation": lp_recommendations_ru.get(regime, "Неизвестный режим."),
            "top_pools": [
                {
                    "symbol": o.symbol,
                    "chain": o.chain,
                    "apy": o.apy_total,
                    "risk_adj_apy": o.risk_adjusted_apy,
                    "tvl": o.tvl_usd,
                    "il_risk": o.il_risk_label,
                }
                for o in rankings["by_risk_adjusted"][:10]  # Top 10
            ]
        }
        
    except Exception as e:
        logger.error(f"Opportunities error: {e}")
        return None


def run_advisor(monitor_data: dict, opportunities_data: Optional[dict], history: List[dict]) -> Optional[str]:
    """Run LP Advisor with proper APY and regime analysis"""
    
    # Check for OpenAI key first
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        logger.warning("OPENAI_API_KEY not set - skipping AI summary")
        return None
    
    try:
        # === BUILD ANALYSIS CONTEXT ===
        
        tvl = monitor_data.get("tvl", 0)
        fees = monitor_data.get("fees", 0)
        positions = monitor_data.get("positions", [])
        count = len(positions)
        in_range = sum(1 for p in positions if p.get("in_range", False))
        out_range = count - in_range
        
        # Regime info
        regime = opportunities_data.get("regime", "UNKNOWN") if opportunities_data else "UNKNOWN"
        
        # Portfolio APY (calculated from history)
        portfolio_apy = opportunities_data.get("portfolio_apy") if opportunities_data else None
        
        # Benchmark - average of top 5 pools
        benchmark_apy = None
        top_pools = []
        if opportunities_data and opportunities_data.get("top_pools"):
            top_pools = opportunities_data["top_pools"][:5]
            if top_pools:
                benchmark_apy = sum(p.get("risk_adj_apy", 0) for p in top_pools) / len(top_pools)
        
        # === DETERMINE PORTFOLIO HEALTH ===
        
        all_in_range = (in_range == count)
        has_apy_data = portfolio_apy is not None
        
        # Token type classification
        def get_token_type(symbol: str) -> str:
            s = symbol.upper()
            stables = {"USDC", "USDT", "DAI", "BUSD", "FRAX", "FDUSD"}
            majors = {"WETH", "ETH", "WBTC", "BTC", "BTCB", "WBNB", "BNB"}
            if s in stables:
                return "stable"
            if s in majors:
                return "major"
            return "alt"
        
        # Count position types
        stable_stable = 0
        stable_major = 0
        major_major = 0
        with_alt = 0
        
        for p in positions:
            t0 = get_token_type(p.get("token0_symbol", ""))
            t1 = get_token_type(p.get("token1_symbol", ""))
            
            if t0 == "stable" and t1 == "stable":
                stable_stable += 1
            elif (t0 == "stable" and t1 == "major") or (t0 == "major" and t1 == "stable"):
                stable_major += 1
            elif t0 == "major" and t1 == "major":
                major_major += 1
            else:
                with_alt += 1
        
        # === BUILD AI PROMPT ===
        
        # Portfolio status
        if all_in_range and fees > 0:
            status_line = f"Все {count} позиций в диапазоне, накоплено ${fees:.0f} fees. Портфель работает."
        elif out_range > 0:
            status_line = f"ВНИМАНИЕ: {out_range} из {count} позиций ВНЕ диапазона! Требуется действие."
        else:
            status_line = f"{in_range}/{count} позиций активны."
        
        # APY comparison
        if has_apy_data and benchmark_apy:
            diff = portfolio_apy - benchmark_apy
            if diff >= -5:
                apy_line = f"APY портфеля: {portfolio_apy:.1f}% (бенчмарк: {benchmark_apy:.1f}%). На уровне рынка или лучше."
            else:
                apy_line = f"APY портфеля: {portfolio_apy:.1f}% (бенчмарк: {benchmark_apy:.1f}%). Есть потенциал для улучшения."
        else:
            apy_line = "APY: недостаточно данных для расчёта (нужно минимум 2 дня истории)."
        
        # Regime description
        regime_descriptions = {
            "BULL": "рост, тренд вверх",
            "BEAR": "падение, тренд вниз",
            "RANGE": "боковик, консолидация",
            "TRENDING": "сильный тренд",
            "VOLATILE_CHOP": "высокая волатильность",
            "TRANSITION": "переходный период",
            "HARVEST": "идеально для LP",
            "CHURN": "хаотичное движение",
        }
        regime_desc = regime_descriptions.get(regime, regime)
        
        # Pair composition
        composition = f"stable/stable: {stable_stable}, stable/major: {stable_major}, major/major: {major_major}, с alt: {with_alt}"
        
        prompt = f"""Ты LP-эксперт. Дай КРАТКУЮ оценку портфеля (3-4 предложения).

СТАТУС: {status_line}

ДОХОДНОСТЬ: {apy_line}

ФАЗА РЫНКА: {regime} ({regime_desc})

СОСТАВ ПАР: {composition}

ПРАВИЛА ОТВЕТА:
1. Если ВСЕ позиции в диапазоне и fees растут — НЕ рекомендуй менять позиции
2. Если APY неизвестен — НЕ говори "отстаёт", просто отметь что данных пока нет
3. Рекомендуй действия ТОЛЬКО если есть реальная проблема:
   - Позиции вне диапазона
   - APY известен И сильно ниже бенчмарка (>10%)
4. При BEAR режиме отметь что пары с alt токенами несут повышенный риск IL, но НЕ требуй срочной смены если они в диапазоне
5. Будь кратким и конкретным

Ответ на русском, 2-4 предложения."""

        # === CALL OPENAI ===
        
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "messages": [
                {
                    "role": "system",
                    "content": "Ты спокойный и практичный DeFi LP эксперт. Не паникуешь, не даёшь лишних рекомендаций. Если портфель работает нормально — так и говоришь."
                },
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 250,
            "temperature": 0.7
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            ai_text = data["choices"][0]["message"]["content"]
            logger.info(f"AI response: {ai_text[:100]}...")
            return ai_text
        else:
            logger.error(f"OpenAI error: {response.status_code} - {response.text[:200]}")
            return None
            
    except Exception as e:
        logger.error(f"Advisor error: {e}")
        import traceback
        traceback.print_exc()
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM REPORT
# ═══════════════════════════════════════════════════════════════════════════════

STABLECOINS = {"USDT", "USDC", "USD₮0", "BUSD", "DAI", "FRAX", "TUSD", "USDP"}
MAJORS = {"WETH", "ETH", "WBNB", "BNB", "WBTC", "BTC"}


def calculate_asset_allocation(positions: List[dict]) -> List[dict]:
    """
    Calculate % allocation per asset across all LP positions.
    
    Hierarchy: stablecoins > majors (ETH/BNB/BTC) > alts
    Risk exposure is assigned to the riskiest token in the pair:
    - USDT-ETH → 100% ETH
    - BNB-ASTER → 100% ASTER
    - ETH-ASTER → 100% ASTER
    - USDT-USDC → skip
    - ETH-BNB → 50/50
    - ASTER-ZEC → 50/50
    """
    exposure = {}  # token -> total USD
    
    def token_tier(t: str) -> int:
        """0=stable, 1=major, 2=alt"""
        if t in STABLECOINS:
            return 0
        if t in MAJORS:
            return 1
        return 2
    
    for p in positions:
        balance = p.get("balance_usd", 0)
        t0 = p.get("token0_symbol", "")
        t1 = p.get("token1_symbol", "")
        
        tier0 = token_tier(t0)
        tier1 = token_tier(t1)
        
        if tier0 == tier1:
            if tier0 == 0:
                continue  # Both stablecoins — no risk exposure
            # Same tier — split 50/50
            exposure[t0] = exposure.get(t0, 0) + balance / 2
            exposure[t1] = exposure.get(t1, 0) + balance / 2
        elif tier0 > tier1:
            # t0 is riskier → 100% t0
            exposure[t0] = exposure.get(t0, 0) + balance
        else:
            # t1 is riskier → 100% t1
            exposure[t1] = exposure.get(t1, 0) + balance
    
    total = sum(exposure.values())
    if total == 0:
        return []
    
    result = [
        {"token": token, "usd": usd, "pct": usd / total * 100}
        for token, usd in sorted(exposure.items(), key=lambda x: -x[1])
    ]
    return result


def check_defi_hacks_ai(chains: List[str], tokens: List[str]) -> str:
    """Use OpenAI with web search to check for recent DeFi hacks/exploits."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return ""
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    chains_str = ", ".join(chains)
    tokens_str = ", ".join(tokens)
    
    prompt = f"""Search for DeFi hacks, exploits, or security incidents in the last 48 hours (today is {today}).

I have LP positions on these chains: {chains_str}
My tokens: {tokens_str}
Protocols: PancakeSwap, Uniswap V3

Check:
1. Any DeFi hacks/exploits in the last 48 hours on ANY chain
2. Are any of my chains or tokens affected?

Reply ONLY in this compact format (for Telegram):

If hacks found:
🚨 DeFi Hacks (48h):
[protocol] on [chain] — $Xm lost — [brief description]
→ Затронуты мои позиции: ДА/НЕТ

If no hacks:
✅ DeFi: no hacks in 48h

Maximum 3-4 lines total. No extra text."""

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini-search-preview",
                "messages": [
                    {"role": "system", "content": "DeFi security monitor. Search web for recent hacks. Be factual, concise."},
                    {"role": "user", "content": prompt}
                ],
                "web_search_options": {"search_context_size": "medium"}
            },
            timeout=60
        )
        
        if response.status_code != 200:
            logger.error(f"Hack check API error: {response.status_code}")
            return ""
        
        result = response.json()["choices"][0]["message"]["content"]
        logger.info(f"Hack check done ({len(result)} chars)")
        return result.strip()
    except Exception as e:
        logger.error(f"Hack check exception: {e}")
        return ""

def format_unified_report(
    monitor_data: dict,
    opportunities_data: Optional[dict],
    ai_summary: Optional[str],
    history: List[dict],
    hedge_report: Optional[str] = None,
    hack_report: Optional[str] = None
) -> str:
    """Format compact daily Telegram report — only actionable info."""
    
    now = datetime.now(timezone.utc)
    msk_time = now + timedelta(hours=3)
    
    lines = [f"LP | {msk_time.strftime('%d.%m')}"]
    
    # Line 1: TVL + 24h change + APY
    tvl = monitor_data.get("tvl", 0)
    count = monitor_data.get("count", 0)
    in_range = monitor_data.get("in_range", 0)
    
    summary_parts = [f"${tvl:,.0f}"]
    
    # 24h change
    if len(history) >= 2:
        abs_1d, pct_1d = get_tvl_change(history, tvl, 1)
        if abs_1d is not None:
            sign = "+" if abs_1d >= 0 else ""
            summary_parts.append(f"{sign}${abs_1d:,.0f} (24h)")
    
    # APY
    portfolio_apy = opportunities_data.get("portfolio_apy") if opportunities_data else None
    if portfolio_apy and portfolio_apy < 1000:
        summary_parts.append(f"APY {portfolio_apy:.0f}%")
    
    lines.append(" | ".join(summary_parts))
    
    # Warning for failed wallets (RPC errors)
    failed_wallets = monitor_data.get("failed_wallets", [])
    if failed_wallets:
        lines.append(f"НЕ ЗАГРУЖЕНЫ: {', '.join(failed_wallets)} — TVL неполный!")
    
    # In-range status
    if in_range == count:
        lines.append(f"{in_range}/{count} in range")
    else:
        # Show only out-of-range positions, compact
        lines.append(f"{in_range}/{count} in range")
        
        positions = monitor_data.get("positions", [])
        for p in positions:
            if not p.get("in_range", False):
                symbol = f"{p.get('token0_symbol', '')}-{p.get('token1_symbol', '')}"
                balance = p.get("balance_usd", 0)
                wallet = p.get("wallet_name", "")
                
                if p.get("current_tick", 0) < p.get("tick_lower", 0):
                    pct = abs(p.get('distance_to_lower_pct', 0))
                    lines.append(f"  {wallet}: {symbol} ${balance:,.0f} — {pct:.1f}% below")
                else:
                    pct = abs(p.get('distance_to_upper_pct', 0))
                    lines.append(f"  {wallet}: {symbol} ${balance:,.0f} — {pct:.1f}% above")
    
    # Asset allocation
    positions = monitor_data.get("positions", [])
    allocation = calculate_asset_allocation(positions)
    if allocation:
        alloc_parts = [f"{a['token']} {a['pct']:.0f}%" for a in allocation]
        lines.append(" | ".join(alloc_parts))
    
    # DeFi hack check — only if hacks found
    if hack_report and "no hacks" not in hack_report.lower():
        lines.append("")
        lines.append(hack_report)
    
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
        response = requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=10)
        
        if response.status_code == 200:
            logger.info("Telegram sent")
            return True
        else:
            logger.error(f"Telegram error: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Telegram exception: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """Main entry point"""
    logger.info("=" * 60)
    logger.info("LP INTELLIGENCE SYSTEM v2.0.0")
    logger.info("=" * 60)
    
    # Load history
    history = load_history()
    logger.info(f"Loaded {len(history)} historical snapshots")
    
    # Stage 1: Monitor
    logger.info("\n--- STAGE 1: MONITOR ---")
    monitor_data = run_monitor()
    
    if not monitor_data:
        logger.error("Monitor failed - cannot continue")
        return 1
    
    logger.info(f"TVL: ${monitor_data['tvl']:,.0f}")
    logger.info(f"Positions: {monitor_data['count']}")
    
    # Save snapshot to history
    by_wallet_tvl = {k: v.get("balance_usd", 0) for k, v in monitor_data.get("by_wallet", {}).items()}
    by_wallet_fees = {k: v.get("fees_usd", 0) for k, v in monitor_data.get("by_wallet", {}).items()}
    add_snapshot(
        tvl=monitor_data["tvl"],
        fees=monitor_data["fees"],
        positions_count=monitor_data["count"],
        in_range=monitor_data["in_range"],
        by_wallet=by_wallet_tvl,
        by_wallet_fees=by_wallet_fees,
    )
    
    # Reload history after adding snapshot
    history = load_history()
    
    # Calculate portfolio APY (uses cumulative fees from history)
    portfolio_apy = calculate_portfolio_apy(history, monitor_data["tvl"])
    if portfolio_apy:
        logger.info(f"Portfolio APY: {portfolio_apy:.1f}%")
    
    # Stage 2: Opportunities
    logger.info("\n--- STAGE 2: OPPORTUNITIES ---")
    opportunities_data = run_opportunities()
    
    if opportunities_data:
        logger.info(f"Regime: {opportunities_data.get('regime')}")
        logger.info(f"Top pools: {len(opportunities_data.get('top_pools', []))}")
        # Add portfolio APY to opportunities data for comparison
        opportunities_data["portfolio_apy"] = portfolio_apy
    else:
        logger.warning("Opportunities scan failed")
    
    # Stage 3: AI Advisor — skipped in daily (shown in weekly only)
    logger.info("\n--- STAGE 3: ADVISOR (skipped — daily compact mode) ---")
    ai_summary = None
    
    # Stage 4: Hedge — skipped in daily (shown in weekly only)
    logger.info("\n--- STAGE 4: HEDGE (skipped — daily compact mode) ---")
    hedge_report = None
    
    # DeFi hack check
    logger.info("\n--- STAGE 5: DEFI HACK CHECK ---")
    hack_report = None
    try:
        # Get tokens and chains from positions
        all_positions = monitor_data.get("positions", [])
        chains = list(set(p.get("chain", "") for p in all_positions if p.get("chain")))
        tokens = list(set(
            t for p in all_positions 
            for t in (p.get("token0_symbol", ""), p.get("token1_symbol", ""))
            if t and t not in STABLECOINS
        ))
        
        if chains and tokens:
            hack_report = check_defi_hacks_ai(chains, tokens)
    except Exception as e:
        logger.warning(f"Hack check failed: {e}")
    
    # Generate unified report
    logger.info("\n--- GENERATING REPORT ---")
    report = format_unified_report(monitor_data, opportunities_data, ai_summary, history, hedge_report, hack_report)
    
    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)
    
    # Send to Telegram
    send_telegram(report)
    
    logger.info("\nDone!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
