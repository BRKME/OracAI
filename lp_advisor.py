"""
LP Advisor - AI-powered LP Recommendations
Version: 1.0.0

Функционал:
- Сравнение текущих позиций vs лучших возможностей
- Интеграция с OpenAI GPT для анализа
- Персонализированные рекомендации
- Action items: держать, ребалансировать, закрыть, открыть
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

import requests

from lp_config import (
    STABLECOINS, MAJOR_TOKENS,
    REGIME_IL_PENALTY,
    LP_STATE_FILE, LP_OPPORTUNITIES_FILE,
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# OPENAI CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PositionAnalysis:
    """Analysis of a single position"""
    wallet_name: str
    chain: str
    symbol: str
    balance_usd: float
    fees_usd: float
    in_range: bool
    status: str  # HEALTHY, WARNING, CRITICAL
    recommendation: str  # HOLD, REBALANCE, CLOSE, NARROW, WIDEN
    reason: str
    better_alternative: Optional[str] = None
    potential_improvement: Optional[float] = None  # APY difference


@dataclass
class AdvisorReport:
    """Full advisor report"""
    timestamp: str
    regime: str
    regime_recommendation: str
    total_tvl: float
    total_fees: float
    positions_analyzed: int
    positions_healthy: int
    positions_warning: int
    positions_critical: int
    position_analyses: List[dict]
    top_opportunities: List[dict]
    action_items: List[str]
    ai_summary: str


# ═══════════════════════════════════════════════════════════════════════════════
# STATE LOADERS
# ═══════════════════════════════════════════════════════════════════════════════

def load_positions_state() -> dict:
    """Load current LP positions"""
    if not os.path.exists(LP_STATE_FILE):
        logger.warning(f"Positions file not found: {LP_STATE_FILE}")
        return {}
    
    try:
        with open(LP_STATE_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading positions: {e}")
        return {}


def load_opportunities_state() -> dict:
    """Load LP opportunities"""
    if not os.path.exists(LP_OPPORTUNITIES_FILE):
        logger.warning(f"Opportunities file not found: {LP_OPPORTUNITIES_FILE}")
        return {}
    
    try:
        with open(LP_OPPORTUNITIES_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading opportunities: {e}")
        return {}


def load_regime_state() -> dict:
    """Load market regime"""
    state_files = ["state/engine_state.json", "state/last_output.json"]
    
    for filepath in state_files:
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                regime = data.get("current_regime") or data.get("regime")
                lp_score = data.get("lp_score") or data.get("lp_env_score")
                if regime:
                    return {"regime": regime, "lp_score": lp_score}
            except:
                pass
    
    return {"regime": "UNKNOWN", "lp_score": None}


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

def get_token_type(symbol: str) -> str:
    """Classify token"""
    s = symbol.upper().strip()
    if s in STABLECOINS or s.startswith("USD"):
        return "stable"
    if s in MAJOR_TOKENS or s in {"WETH", "ETH", "WBTC", "BTC", "WBNB", "BNB"}:
        return "major"
    return "alt"


def analyze_position(position: dict, opportunities: List[dict], regime: str) -> PositionAnalysis:
    """Analyze a single position and generate recommendation"""
    
    wallet_name = position.get("wallet_name", "Unknown")
    chain = position.get("chain", "")
    token0 = position.get("token0_symbol", "")
    token1 = position.get("token1_symbol", "")
    symbol = f"{token0}-{token1}"
    balance_usd = position.get("balance_usd", 0)
    fees_usd = position.get("uncollected_fees_usd", 0)
    in_range = position.get("in_range", False)
    distance_lower = position.get("distance_to_lower_pct", 0)
    distance_upper = position.get("distance_to_upper_pct", 0)
    range_width = position.get("range_width_pct", 0)
    
    # Determine status
    if not in_range:
        if abs(distance_lower) > 10 or abs(distance_upper) > 10:
            status = "CRITICAL"
        else:
            status = "WARNING"
    else:
        # Check if close to edge
        if min(abs(distance_lower), abs(distance_upper)) < 2:
            status = "WARNING"
        else:
            status = "HEALTHY"
    
    # Determine recommendation
    recommendation = "HOLD"
    reason = ""
    better_alternative = None
    potential_improvement = None
    
    # Out of range logic
    if not in_range:
        if balance_usd > 100:  # Only care about non-dust positions
            recommendation = "REBALANCE"
            reason = f"Position out of range. Not earning fees."
        else:
            recommendation = "CLOSE"
            reason = "Small position out of range. Consider closing."
    
    # Regime-based logic
    regime_penalty = REGIME_IL_PENALTY.get(regime, 0.4)
    token0_type = get_token_type(token0)
    token1_type = get_token_type(token1)
    
    # High IL risk in trending market
    if regime in ["BEAR", "TRENDING", "BREAKOUT", "CHURN"]:
        if token0_type == "alt" or token1_type == "alt":
            if status == "HEALTHY":
                status = "WARNING"
            recommendation = "CLOSE" if not in_range else "NARROW"
            reason = f"High IL risk pair in {regime} regime. Consider safer pairs."
    
    # Range too wide in good LP environment
    if regime in ["RANGE", "HARVEST"] and in_range:
        if range_width > 50:
            recommendation = "NARROW"
            reason = "Range too wide for current regime. Narrowing could increase APY."
    
    # Range too narrow in volatile environment
    if regime in ["VOLATILE_CHOP", "TRANSITION"]:
        if range_width < 10 and in_range:
            recommendation = "WIDEN"
            reason = "Range too narrow for volatile market. Risk of going out of range."
    
    # Find better alternative
    if opportunities:
        same_chain_opps = [o for o in opportunities if o.get("chain", "").lower() == chain.lower()]
        if same_chain_opps:
            # Find similar risk profile but higher APY
            for opp in same_chain_opps[:10]:
                opp_t0 = get_token_type(opp.get("token0", ""))
                opp_t1 = get_token_type(opp.get("token1", ""))
                
                # Same or lower risk
                current_risk = 1 if "alt" in [token0_type, token1_type] else 0
                opp_risk = 1 if "alt" in [opp_t0, opp_t1] else 0
                
                if opp_risk <= current_risk:
                    opp_apy = opp.get("risk_adjusted_apy", 0)
                    if opp_apy > 0:
                        better_alternative = opp.get("symbol", "")
                        potential_improvement = opp_apy
                        break
    
    return PositionAnalysis(
        wallet_name=wallet_name,
        chain=chain,
        symbol=symbol,
        balance_usd=balance_usd,
        fees_usd=fees_usd,
        in_range=in_range,
        status=status,
        recommendation=recommendation,
        reason=reason if reason else "Position is healthy.",
        better_alternative=better_alternative,
        potential_improvement=potential_improvement,
    )


def generate_action_items(analyses: List[PositionAnalysis], regime: str) -> List[str]:
    """Generate prioritized action items"""
    items = []
    
    # Critical items first
    critical = [a for a in analyses if a.status == "CRITICAL"]
    for a in critical:
        items.append(f"! {a.wallet_name} | {a.symbol}: {a.recommendation} - {a.reason}")
    
    # Warning items
    warnings = [a for a in analyses if a.status == "WARNING"]
    for a in warnings:
        items.append(f"* {a.wallet_name} | {a.symbol}: {a.recommendation} - {a.reason}")
    
    # Regime-specific advice
    if regime in ["BEAR", "TRENDING"]:
        items.append(f"Regime {regime}: Reduce exposure to volatile pairs. Prefer stable/major.")
    elif regime in ["RANGE", "HARVEST"]:
        items.append(f"Regime {regime}: Good for LP. Consider tightening ranges for higher APY.")
    
    # Better alternatives
    with_alternatives = [a for a in analyses if a.better_alternative and a.potential_improvement]
    if with_alternatives:
        best = max(with_alternatives, key=lambda x: x.potential_improvement or 0)
        items.append(f"Consider: {best.better_alternative} (Risk-Adj APY: {best.potential_improvement:.1f}%)")
    
    return items[:10]  # Max 10 items


# ═══════════════════════════════════════════════════════════════════════════════
# OPENAI INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════

def call_openai(prompt: str) -> str:
    """Call OpenAI API for analysis"""
    if not OPENAI_API_KEY:
        logger.warning("OpenAI API key not set")
        return ""
    
    try:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": OPENAI_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": """You are an expert DeFi LP advisor. Analyze LP positions and market conditions.
Be concise and actionable. Use Russian language. Focus on:
1. Risk assessment (IL, out-of-range)
2. Regime-appropriate strategy
3. Specific action items
Keep response under 500 characters."""
                },
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 300,
            "temperature": 0.7
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        else:
            logger.error(f"OpenAI error: {response.status_code} - {response.text}")
            return ""
            
    except Exception as e:
        logger.error(f"OpenAI exception: {e}")
        return ""


def generate_ai_summary(
    positions: List[dict],
    opportunities: List[dict],
    regime: str,
    analyses: List[PositionAnalysis]
) -> str:
    """Generate AI summary of the situation"""
    
    # Build context
    total_tvl = sum(p.get("balance_usd", 0) for p in positions)
    total_fees = sum(p.get("uncollected_fees_usd", 0) for p in positions)
    in_range_count = sum(1 for p in positions if p.get("in_range", False))
    out_range_count = len(positions) - in_range_count
    
    critical_count = sum(1 for a in analyses if a.status == "CRITICAL")
    warning_count = sum(1 for a in analyses if a.status == "WARNING")
    
    # Top opportunities
    top_opps = opportunities[:3] if opportunities else []
    top_opps_str = ", ".join([f"{o.get('symbol', '')} ({o.get('risk_adjusted_apy', 0):.1f}%)" for o in top_opps])
    
    prompt = f"""LP Portfolio Analysis:

Market Regime: {regime}
Total TVL: ${total_tvl:,.0f}
Uncollected Fees: ${total_fees:,.2f}
Positions: {len(positions)} total, {in_range_count} in-range, {out_range_count} out-of-range
Critical: {critical_count}, Warnings: {warning_count}

Top opportunities on market: {top_opps_str}

Дай краткую оценку портфеля и 2-3 конкретных действия."""

    return call_openai(prompt)


# ═══════════════════════════════════════════════════════════════════════════════
# ADVISOR CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class LPAdvisor:
    """Main advisor class"""
    
    def __init__(self):
        self.positions_data = load_positions_state()
        self.opportunities_data = load_opportunities_state()
        self.regime_data = load_regime_state()
        
        self.regime = self.regime_data.get("regime", "UNKNOWN")
        self.lp_score = self.regime_data.get("lp_score")
        
        self.positions = self.positions_data.get("positions", [])
        
        # Get opportunities (try different keys)
        self.opportunities = (
            self.opportunities_data.get("all_opportunities", []) or
            self.opportunities_data.get("summary", {}).get("top_by_risk_adjusted", [])
        )
        
        self.analyses: List[PositionAnalysis] = []
        
        logger.info(f"Loaded {len(self.positions)} positions")
        logger.info(f"Loaded {len(self.opportunities)} opportunities")
        logger.info(f"Market Regime: {self.regime}")
    
    def analyze(self) -> List[PositionAnalysis]:
        """Analyze all positions"""
        self.analyses = []
        
        for pos in self.positions:
            try:
                analysis = analyze_position(pos, self.opportunities, self.regime)
                self.analyses.append(analysis)
            except Exception as e:
                logger.warning(f"Error analyzing position: {e}")
        
        logger.info(f"Analyzed {len(self.analyses)} positions")
        return self.analyses
    
    def get_regime_recommendation(self) -> str:
        """Get regime-based recommendation"""
        recommendations = {
            "HARVEST": "Ideal conditions for LP. Can use tight ranges.",
            "RANGE": "Good conditions. Standard ranges work.",
            "MEAN_REVERT": "Moderate. Watch range boundaries.",
            "VOLATILE_CHOP": "Volatility. Use wide ranges.",
            "TRANSITION": "Transition period. Caution.",
            "BULL": "Uptrend. IL risk on short positions.",
            "BEAR": "Downtrend. IL risk. Prefer stable pairs.",
            "TRENDING": "Strong trend. High IL risk. Minimize exposure.",
            "BREAKOUT": "Breakout. Possible strong IL. Caution.",
            "CHURN": "Chaos. Better to exit risky positions.",
            "AVOID": "Avoid LP. High risk.",
        }
        return recommendations.get(self.regime, "Unknown regime. Act cautiously.")
    
    def generate_report(self) -> AdvisorReport:
        """Generate full report"""
        if not self.analyses:
            self.analyze()
        
        # Stats
        total_tvl = sum(a.balance_usd for a in self.analyses)
        total_fees = sum(a.fees_usd for a in self.analyses)
        healthy = sum(1 for a in self.analyses if a.status == "HEALTHY")
        warning = sum(1 for a in self.analyses if a.status == "WARNING")
        critical = sum(1 for a in self.analyses if a.status == "CRITICAL")
        
        # Action items
        action_items = generate_action_items(self.analyses, self.regime)
        
        # AI summary
        ai_summary = generate_ai_summary(
            self.positions,
            self.opportunities,
            self.regime,
            self.analyses
        )
        
        # Top opportunities for report
        top_opps = []
        if self.opportunities:
            for opp in self.opportunities[:5]:
                top_opps.append({
                    "symbol": opp.get("symbol", ""),
                    "chain": opp.get("chain", ""),
                    "apy": opp.get("apy_total", 0),
                    "risk_adjusted_apy": opp.get("risk_adjusted_apy", 0),
                    "tvl": opp.get("tvl_usd", 0),
                    "il_risk": opp.get("il_risk_label", ""),
                })
        
        return AdvisorReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            regime=self.regime,
            regime_recommendation=self.get_regime_recommendation(),
            total_tvl=total_tvl,
            total_fees=total_fees,
            positions_analyzed=len(self.analyses),
            positions_healthy=healthy,
            positions_warning=warning,
            positions_critical=critical,
            position_analyses=[asdict(a) for a in self.analyses],
            top_opportunities=top_opps,
            action_items=action_items,
            ai_summary=ai_summary,
        )
    
    def format_telegram_report(self) -> str:
        """Format report for Telegram"""
        report = self.generate_report()
        
        now = datetime.now(timezone.utc)
        
        lines = [
            "#LP #Advisor",
            f"📊 LP Advisor | {now.strftime('%d.%m %H:%M')} UTC",
            "",
        ]
        
        # Regime
        lines.append(f"Regime: {report.regime}")
        lines.append(report.regime_recommendation)
        lines.append("")
        
        # Portfolio summary
        lines.append("Portfolio:")
        lines.append(f"  TVL: ${report.total_tvl:,.0f}")
        lines.append(f"  Fees: ${report.total_fees:,.2f}")
        lines.append(f"  Healthy: {report.positions_healthy} | Warning: {report.positions_warning} | Critical: {report.positions_critical}")
        lines.append("")
        
        # Position details - only show wallets with issues
        if self.analyses:
            from collections import defaultdict
            by_wallet = defaultdict(list)
            for a in self.analyses:
                by_wallet[a.wallet_name].append(a)
            
            all_healthy = all(a.in_range for a in self.analyses)
            
            if all_healthy:
                wallet_parts = []
                for wallet_name in sorted(by_wallet.keys()):
                    w_total = sum(a.balance_usd for a in by_wallet[wallet_name])
                    wallet_parts.append(f"{wallet_name}: ${w_total:,.0f}")
                lines.append(" | ".join(wallet_parts))
            else:
                for wallet_name in sorted(by_wallet.keys()):
                    wallet_analyses = sorted(by_wallet[wallet_name], key=lambda x: x.balance_usd, reverse=True)
                    has_problems = any(not a.in_range for a in wallet_analyses)
                    
                    if not has_problems:
                        w_total = sum(a.balance_usd for a in wallet_analyses)
                        lines.append(f"{wallet_name}: ${w_total:,.0f} ✓")
                    else:
                        lines.append(f"{wallet_name}:")
                        for a in wallet_analyses:
                            status = "🟢" if a.in_range else "🔴"
                            lines.append(f"  {status} {a.symbol} ${a.balance_usd:,.0f} -> {a.recommendation}")
            lines.append("")
        
        # Action items
        if report.action_items:
            lines.append("Actions:")
            for item in report.action_items[:5]:
                lines.append(f"  {item}")
            lines.append("")
        
        # Top opportunities
        if report.top_opportunities:
            lines.append("Top pools:")
            for opp in report.top_opportunities[:3]:
                lines.append(f"  {opp['symbol']}: {opp['risk_adjusted_apy']:.1f}% (adj)")
            lines.append("")
        
        # AI Summary
        if report.ai_summary:
            lines.append("AI:")
            lines.append(report.ai_summary)
        
        return "\n".join(lines)
    
    def save_report(self, filepath: str = "state/lp_advisor_report.json"):
        """Save report to JSON"""
        report = self.generate_report()
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(asdict(report), f, indent=2, ensure_ascii=False)
        
        logger.info(f"✓ Report saved to {filepath}")


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
    logger.info("LP ADVISOR v1.0.0")
    logger.info("=" * 60)
    
    advisor = LPAdvisor()
    
    # Check data
    if not advisor.positions:
        logger.warning("No positions loaded! Run lp_monitor.py first.")
    
    if not advisor.opportunities:
        logger.warning("No opportunities loaded! Run lp_opportunities.py first.")
    
    # Analyze
    advisor.analyze()
    
    # Generate report
    report = advisor.generate_report()
    
    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Regime: {report.regime}")
    logger.info(f"TVL: ${report.total_tvl:,.0f}")
    logger.info(f"Positions: {report.positions_analyzed}")
    logger.info(f"  Healthy: {report.positions_healthy}")
    logger.info(f"  Warning: {report.positions_warning}")
    logger.info(f"  Critical: {report.positions_critical}")
    
    if report.action_items:
        logger.info("\nAction Items:")
        for item in report.action_items:
            logger.info(f"  {item}")
    
    # Save report
    advisor.save_report()
    
    # Telegram
    tg_report = advisor.format_telegram_report()
    logger.info("\n" + "=" * 60)
    logger.info("TELEGRAM REPORT")
    logger.info("=" * 60)
    print(tg_report)
    
    send_telegram_message(tg_report)
    
    return report


if __name__ == "__main__":
    main()
