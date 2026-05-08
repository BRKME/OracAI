"""
LP Opportunities Scanner - Find best LP opportunities on Uniswap V3
Version: 1.0.0

Функционал:
- Сканирование пулов через DeFiLlama API
- Фильтрация по TVL, Volume, токенам
- Расчёт IL Risk и Risk-Adjusted APY
- Интеграция с Market Regime Engine
- Telegram отчёт
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field

import requests

from lp_config import (
    DEFILLAMA_POOLS_URL,
    SCAN_CHAINS, SCAN_PROTOCOLS,
    MIN_TVL_USD, MIN_VOLUME_24H_USD, MIN_APY,
    STABLECOINS, MAJOR_TOKENS, TOKEN_WHITELIST,
    IL_RISK_MATRIX, REGIME_IL_PENALTY,
    LP_OPPORTUNITIES_FILE,
    EXCLUDED_FROM_TOP_POOLS,
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PoolOpportunity:
    """Single LP Pool Opportunity"""
    chain: str
    protocol: str
    pool_address: str
    symbol: str              # e.g., "WETH-USDC"
    token0: str
    token1: str
    fee_tier: Optional[float]  # 0.05, 0.3, 1.0 (if available)
    tvl_usd: float
    volume_24h_usd: float
    volume_7d_usd: float
    apy_base: float          # Fee APY
    apy_reward: float        # Incentives
    apy_total: float
    il_risk: float           # 0-1
    il_risk_label: str       # Low, Medium, High, Very High
    risk_adjusted_apy: float
    efficiency: float        # volume/tvl ratio
    token0_type: str         # stable, major, alt
    token1_type: str
    

@dataclass
class OpportunitiesSummary:
    """Summary of scanned opportunities"""
    timestamp: str
    regime: str
    regime_penalty: float
    lp_score: Optional[float]
    total_pools_scanned: int
    pools_after_filter: int
    chains_scanned: List[str]
    top_by_risk_adjusted: List[dict] = field(default_factory=list)
    top_by_raw_apy: List[dict] = field(default_factory=list)
    top_by_tvl: List[dict] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_token_symbol(symbol: str) -> str:
    """Normalize token symbol for matching"""
    if not symbol:
        return ""
    
    # Uppercase
    s = symbol.upper().strip()
    
    # Remove common suffixes/prefixes
    s = re.sub(r'\.E$', '', s)  # .e suffix (bridged)
    s = re.sub(r'-.*$', '', s)  # -XXXXX suffix
    s = re.sub(r'^W', '', s) if s not in {'WSTETH', 'WEETH'} else s  # W prefix (wrapped)
    
    # Map variants
    mapping = {
        'WETH': 'ETH',
        'WBTC': 'BTC',
        'WBNB': 'BNB',
        'BTCB': 'BTC',
        'USDCE': 'USDC',
        'USDTE': 'USDT',
    }
    
    return mapping.get(s, s)


def get_token_type(symbol: str) -> str:
    """Classify token as stable, major, or alt"""
    normalized = normalize_token_symbol(symbol)
    original = symbol.upper().strip()
    
    # Check stablecoins first
    if normalized in STABLECOINS or original in STABLECOINS:
        return "stable"
    
    # Check majors
    if normalized in MAJOR_TOKENS or original in MAJOR_TOKENS:
        return "major"
    
    return "alt"


def get_il_risk(token0_type: str, token1_type: str) -> float:
    """Get IL risk score from matrix"""
    key = (token0_type, token1_type)
    return IL_RISK_MATRIX.get(key, 0.80)


def get_il_risk_label(il_risk: float) -> str:
    """Convert IL risk score to label"""
    if il_risk <= 0.15:
        return "Very Low"
    elif il_risk <= 0.35:
        return "Low"
    elif il_risk <= 0.55:
        return "Medium"
    elif il_risk <= 0.75:
        return "High"
    else:
        return "Very High"


def parse_symbol(symbol: str) -> Tuple[str, str]:
    """Parse pool symbol into token0 and token1"""
    if not symbol:
        return "", ""
    
    # Common separators
    for sep in ['-', '/', '_', ' ']:
        if sep in symbol:
            parts = symbol.split(sep)
            if len(parts) >= 2:
                return parts[0].strip(), parts[1].strip()
    
    return symbol, ""


def is_whitelisted_pool(token0: str, token1: str) -> bool:
    """Check if both tokens are in whitelist"""
    t0_norm = normalize_token_symbol(token0)
    t1_norm = normalize_token_symbol(token1)
    t0_orig = token0.upper().strip()
    t1_orig = token1.upper().strip()
    
    # Check if at least one representation is in whitelist
    t0_ok = t0_norm in TOKEN_WHITELIST or t0_orig in TOKEN_WHITELIST
    t1_ok = t1_norm in TOKEN_WHITELIST or t1_orig in TOKEN_WHITELIST
    
    return t0_ok and t1_ok


# ═══════════════════════════════════════════════════════════════════════════════
# REGIME INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════

def load_regime_state() -> dict:
    """Load current market regime from engine state.
    Warns if state is stale (>12h old)."""
    state_files = [
        "state/engine_state.json",
        "state/last_output.json",
    ]
    
    for filepath in state_files:
        if os.path.exists(filepath):
            try:
                # Check freshness
                import time as _time
                file_age_hours = (_time.time() - os.path.getmtime(filepath)) / 3600
                if file_age_hours > 12:
                    logger.warning(f"⚠️ Regime state is {file_age_hours:.0f}h old ({filepath}) — may be stale")
                
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    
                # Try to extract regime (different key names)
                regime = (
                    data.get("current_regime") or 
                    data.get("regime") or 
                    data.get("lp_regime") or 
                    data.get("market_regime")
                )
                lp_score = data.get("lp_score") or data.get("lp_env_score")
                
                if regime:
                    return {
                        "regime": regime,
                        "lp_score": lp_score,
                        "source": filepath,
                        "stale": file_age_hours > 12
                    }
            except Exception as e:
                logger.warning(f"Error reading {filepath}: {e}")
    
    return {"regime": "UNKNOWN", "lp_score": None, "source": None, "stale": True}


# ═══════════════════════════════════════════════════════════════════════════════
# DEFILLAMA API
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_defillama_pools() -> List[dict]:
    """Fetch all pools from DeFiLlama"""
    try:
        logger.info(f"Fetching pools from DeFiLlama...")
        response = requests.get(DEFILLAMA_POOLS_URL, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"DeFiLlama API error: {response.status_code}")
            return []
        
        data = response.json()
        pools = data.get("data", [])
        logger.info(f"✓ Fetched {len(pools)} total pools")
        return pools
        
    except Exception as e:
        logger.error(f"Error fetching DeFiLlama: {e}")
        return []


def filter_pools(pools: List[dict]) -> List[dict]:
    """Filter pools by chain, protocol, TVL, volume, tokens"""
    filtered = []
    
    # Normalize chain names for comparison
    chain_map = {
        "arbitrum": "Arbitrum",
        "bsc": "BSC",
        "binance": "BSC",
    }
    
    target_chains = set(c.lower() for c in SCAN_CHAINS)
    target_protocols = set(p.lower() for p in SCAN_PROTOCOLS)
    
    for pool in pools:
        chain = pool.get("chain", "").strip()
        project = pool.get("project", "").strip()
        symbol = pool.get("symbol", "")
        tvl = pool.get("tvlUsd", 0) or 0
        apy = pool.get("apy", 0) or 0
        
        # Chain filter
        if chain.lower() not in target_chains:
            continue
        
        # Protocol filter
        if project.lower() not in target_protocols:
            continue
        
        # TVL filter
        if tvl < MIN_TVL_USD:
            continue
        
        # APY filter
        if apy < MIN_APY:
            continue
        
        # Parse tokens
        token0, token1 = parse_symbol(symbol)
        if not token0 or not token1:
            continue
        
        # Exclude certain tokens from recommendations (e.g., ARB)
        if token0.upper() in EXCLUDED_FROM_TOP_POOLS or token1.upper() in EXCLUDED_FROM_TOP_POOLS:
            continue
        
        # Whitelist filter
        if not is_whitelisted_pool(token0, token1):
            continue
        
        # Volume filter (if available)
        volume_1d = pool.get("volumeUsd1d", 0) or 0
        volume_7d = pool.get("volumeUsd7d", 0) or 0
        
        # Use 7d volume / 7 as fallback for daily
        if volume_1d == 0 and volume_7d > 0:
            volume_1d = volume_7d / 7
        
        if volume_1d < MIN_VOLUME_24H_USD:
            continue
        
        filtered.append(pool)
    
    logger.info(f"✓ Filtered to {len(filtered)} pools")
    return filtered


# ═══════════════════════════════════════════════════════════════════════════════
# OPPORTUNITY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_pool(pool: dict, regime_penalty: float) -> PoolOpportunity:
    """Analyze a single pool and compute metrics"""
    
    symbol = pool.get("symbol", "")
    token0, token1 = parse_symbol(symbol)
    
    # Get token types
    token0_type = get_token_type(token0)
    token1_type = get_token_type(token1)
    
    # Calculate IL risk
    il_risk = get_il_risk(token0_type, token1_type)
    il_risk_label = get_il_risk_label(il_risk)
    
    # Get APY components
    apy_base = pool.get("apyBase", 0) or 0
    apy_reward = pool.get("apyReward", 0) or 0
    apy_total = pool.get("apy", 0) or apy_base + apy_reward
    
    # Calculate risk-adjusted APY
    # risk_adjusted = apy * (1 - il_risk * regime_penalty)
    risk_adjusted_apy = apy_total * (1 - il_risk * regime_penalty)
    
    # Get volume
    volume_1d = pool.get("volumeUsd1d", 0) or 0
    volume_7d = pool.get("volumeUsd7d", 0) or 0
    if volume_1d == 0 and volume_7d > 0:
        volume_1d = volume_7d / 7
    
    # TVL
    tvl = pool.get("tvlUsd", 0) or 0
    
    # Efficiency (volume/tvl ratio - higher = more capital efficient)
    efficiency = volume_1d / tvl if tvl > 0 else 0
    
    # Try to extract fee tier from symbol or pool data
    fee_tier = None
    pool_meta = pool.get("poolMeta", "")
    if pool_meta:
        # Try to parse fee from poolMeta like "0.05%" or "0.3%"
        fee_match = re.search(r'(\d+\.?\d*)%', str(pool_meta))
        if fee_match:
            fee_tier = float(fee_match.group(1))
    
    return PoolOpportunity(
        chain=pool.get("chain", ""),
        protocol=pool.get("project", ""),
        pool_address=pool.get("pool", ""),
        symbol=symbol,
        token0=token0,
        token1=token1,
        fee_tier=fee_tier,
        tvl_usd=tvl,
        volume_24h_usd=volume_1d,
        volume_7d_usd=volume_7d,
        apy_base=apy_base,
        apy_reward=apy_reward,
        apy_total=apy_total,
        il_risk=il_risk,
        il_risk_label=il_risk_label,
        risk_adjusted_apy=risk_adjusted_apy,
        efficiency=efficiency,
        token0_type=token0_type,
        token1_type=token1_type,
    )


def rank_opportunities(opportunities: List[PoolOpportunity]) -> dict:
    """Rank opportunities by different criteria"""
    
    # By Risk-Adjusted APY (best risk/reward)
    by_risk_adjusted = sorted(
        opportunities, 
        key=lambda x: x.risk_adjusted_apy, 
        reverse=True
    )[:20]
    
    # By Raw APY (highest yield)
    by_raw_apy = sorted(
        opportunities,
        key=lambda x: x.apy_total,
        reverse=True
    )[:20]
    
    # By TVL (safest/most liquid)
    by_tvl = sorted(
        opportunities,
        key=lambda x: x.tvl_usd,
        reverse=True
    )[:20]
    
    return {
        "by_risk_adjusted": by_risk_adjusted,
        "by_raw_apy": by_raw_apy,
        "by_tvl": by_tvl,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SCANNER CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class LPOpportunitiesScanner:
    """Main scanner class"""
    
    def __init__(self):
        self.opportunities: List[PoolOpportunity] = []
        self.regime_state = load_regime_state()
        self.regime = self.regime_state.get("regime", "UNKNOWN")
        self.regime_penalty = REGIME_IL_PENALTY.get(self.regime, 0.40)
        self.lp_score = self.regime_state.get("lp_score")
        
        logger.info(f"Market Regime: {self.regime}")
        logger.info(f"Regime IL Penalty: {self.regime_penalty:.0%}")
        if self.lp_score:
            logger.info(f"LP Score: {self.lp_score:.2f}")
    
    def scan(self) -> List[PoolOpportunity]:
        """Scan and analyze all opportunities"""
        
        # Fetch from DeFiLlama
        all_pools = fetch_defillama_pools()
        if not all_pools:
            logger.error("No pools fetched!")
            return []
        
        self.total_scanned = len(all_pools)
        
        # Filter
        filtered_pools = filter_pools(all_pools)
        if not filtered_pools:
            logger.warning("No pools after filtering!")
            return []
        
        # Analyze each pool
        self.opportunities = []
        for pool in filtered_pools:
            try:
                opp = analyze_pool(pool, self.regime_penalty)
                self.opportunities.append(opp)
            except Exception as e:
                logger.warning(f"Error analyzing pool: {e}")
        
        logger.info(f"✓ Analyzed {len(self.opportunities)} opportunities")
        return self.opportunities
    
    def get_rankings(self) -> dict:
        """Get ranked opportunities"""
        return rank_opportunities(self.opportunities)
    
    def get_summary(self) -> OpportunitiesSummary:
        """Generate summary"""
        rankings = self.get_rankings()
        
        return OpportunitiesSummary(
            timestamp=datetime.now(timezone.utc).isoformat(),
            regime=self.regime,
            regime_penalty=self.regime_penalty,
            lp_score=self.lp_score,
            total_pools_scanned=getattr(self, 'total_scanned', 0),
            pools_after_filter=len(self.opportunities),
            chains_scanned=SCAN_CHAINS,
            top_by_risk_adjusted=[asdict(o) for o in rankings["by_risk_adjusted"][:10]],
            top_by_raw_apy=[asdict(o) for o in rankings["by_raw_apy"][:10]],
            top_by_tvl=[asdict(o) for o in rankings["by_tvl"][:10]],
        )
    
    def save_state(self, filepath: str = LP_OPPORTUNITIES_FILE):
        """Save results to JSON"""
        summary = self.get_summary()
        
        state = {
            "timestamp": summary.timestamp,
            "summary": asdict(summary),
            "all_opportunities": [asdict(o) for o in self.opportunities]
        }
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        
        logger.info(f"✓ State saved to {filepath}")
        return state
    
    def format_telegram_report(self) -> str:
        """Format report for Telegram"""
        rankings = self.get_rankings()
        summary = self.get_summary()
        
        now = datetime.now(timezone.utc)
        
        lines = [
            "#LP #Opportunities",
            f"📊 LP Scanner | {now.strftime('%d.%m %H:%M')} UTC",
            "",
        ]
        
        # Regime info
        lines.append(f"Regime: {self.regime}")
        if self.lp_score:
            lines.append(f"LP Score: {self.lp_score:+.2f}")
        lines.append(f"IL Penalty: {self.regime_penalty:.0%}")
        lines.append("")
        
        # Stats
        lines.append(f"Scanned: {summary.total_pools_scanned:,} pools")
        lines.append(f"Filtered: {summary.pools_after_filter} opportunities")
        lines.append("")
        
        # Top 5 by Risk-Adjusted APY
        lines.append("TOP 5 Risk-Adjusted:")
        
        for i, opp in enumerate(rankings["by_risk_adjusted"][:5], 1):
            fee_str = f" {opp.fee_tier}%" if opp.fee_tier else ""
            
            lines.append(f"{i}. {opp.symbol}{fee_str}")
            lines.append(f"   APY: {opp.apy_total:.1f}% -> Adj: {opp.risk_adjusted_apy:.1f}%")
            lines.append(f"   TVL: ${opp.tvl_usd/1e6:.1f}M | Vol: ${opp.volume_24h_usd/1e6:.1f}M/d")
            lines.append(f"   IL: {opp.il_risk_label}")
        
        lines.append("")
        
        # Recommendations
        lines.append("Recommendations:")
        
        if self.regime in ["HARVEST", "RANGE"]:
            lines.append("- Regime favorable for LP")
            lines.append("- Consider tight ranges on stable pairs")
        elif self.regime in ["TRENDING", "BREAKOUT", "BEAR"]:
            lines.append("- High IL risk in current regime")
            lines.append("- Prefer stable/stable or wide ranges")
        else:
            lines.append("- Moderate caution advised")
            lines.append("- Balance APY vs IL risk")
        
        # Best pick
        if rankings["by_risk_adjusted"]:
            best = rankings["by_risk_adjusted"][0]
            lines.append(f"- Best risk/reward: {best.symbol}")
        
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

def send_telegram_message(message: str) -> bool:
    """Send message to Telegram"""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        logger.warning("Telegram credentials not set")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=payload, timeout=10)
        
        if response.status_code == 200:
            logger.info("✓ Telegram message sent")
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
    logger.info("LP OPPORTUNITIES SCANNER v1.0.0")
    logger.info("=" * 60)
    
    scanner = LPOpportunitiesScanner()
    
    # Scan
    opportunities = scanner.scan()
    
    if not opportunities:
        logger.warning("No opportunities found!")
        return None
    
    # Get rankings
    rankings = scanner.get_rankings()
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("TOP 10 BY RISK-ADJUSTED APY")
    logger.info("=" * 60)
    
    for i, opp in enumerate(rankings["by_risk_adjusted"][:10], 1):
        logger.info(f"{i}. {opp.chain} | {opp.symbol}")
        logger.info(f"   APY: {opp.apy_total:.1f}% | Risk-Adj: {opp.risk_adjusted_apy:.1f}%")
        logger.info(f"   TVL: ${opp.tvl_usd:,.0f} | IL: {opp.il_risk_label}")
    
    # Save state
    scanner.save_state()
    
    # Telegram report
    report = scanner.format_telegram_report()
    logger.info("\n" + "=" * 60)
    logger.info("TELEGRAM REPORT")
    logger.info("=" * 60)
    print(report)
    
    # Send to Telegram
    send_telegram_message(report)
    
    return scanner.get_summary()


if __name__ == "__main__":
    main()
