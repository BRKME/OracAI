"""
LP Hedge Engine v1.0
Расчёт рекомендаций по хеджированию LP позиций через опционы.

Интегрируется с:
- Regime Engine (last_output.json) — Dir, TailRisk, Vol_z
- LP Monitor (lp_positions.json) — позиции и экспозиция
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Файлы состояния
REGIME_STATE_FILE = "state/last_output.json"
LP_POSITIONS_FILE = "state/lp_positions.json"
HEDGE_STATE_FILE = "state/lp_hedge_state.json"

# Классификация токенов
STABLES = {'USDC', 'USDT', 'DAI', 'BUSD', 'FDUSD', 'FRAX'}
HEDGEABLE_MAJORS = {'ETH', 'WETH', 'BTC', 'WBTC', 'BTCB'}  # Есть опционы на DEX
BNB_TOKENS = {'BNB', 'WBNB'}  # Отдельно — проверим наличие опционов

# Пороги
MIN_HEDGEABLE_EXPOSURE = 5000  # Минимум $5K для хеджа
HEDGE_SCORE_THRESHOLD = 0.4    # Ниже — не хеджируем
MAX_HEDGE_RATIO = 0.75         # Максимум 75%
PREMIUM_BUDGET_PCT = 0.005     # 0.5% от notional

# Опционные параметры
DEFAULT_STRIKE_DISTANCE = 0.10  # -10% от текущей цены
DEFAULT_EXPIRY_DAYS = 14


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PositionClassification:
    """Классификация LP позиции для хеджирования"""
    token0: str
    token1: str
    balance_usd: float
    hedgeable: bool  # True, False, or 'partial'
    hedge_type: str  # 'full', 'partial', 'none'
    underlying: Optional[str]  # ETH, BTC, BNB или None
    exposure_eth: float
    exposure_btc: float
    exposure_bnb: float
    note: str


@dataclass
class HedgeRecommendation:
    """Рекомендация по хеджированию"""
    underlying: str  # ETH, BTC
    action: str  # PUT
    strike_pct: float  # -10% = 0.10
    expiry_days: int
    notional_usd: float
    max_premium_usd: float
    platform: str  # Aevo
    
    # Live pricing fields (from Aevo API)
    instrument_name: Optional[str] = None
    strike_price: Optional[float] = None
    mark_price: Optional[float] = None
    iv: Optional[float] = None
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None


@dataclass
class HedgeDecision:
    """Итоговое решение по хеджированию"""
    timestamp: str
    action: str  # HEDGE, NO_HEDGE, WAIT
    reason: str
    
    # Метрики из Regime Engine
    dir_value: float
    tail_risk_active: bool
    tail_polarity: str
    confidence: float
    vol_z: float
    
    # Расчётные метрики
    hedge_score: float
    hedge_ratio: float
    
    # Экспозиция
    total_tvl: float
    hedgeable_exposure: Dict[str, float]
    non_hedgeable_exposure: float
    
    # Рекомендации
    recommendations: List[dict]


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def load_regime_state() -> Optional[dict]:
    """Load regime state from last_output.json"""
    if not os.path.exists(REGIME_STATE_FILE):
        logger.warning(f"Regime state not found: {REGIME_STATE_FILE}")
        return None
    
    try:
        with open(REGIME_STATE_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading regime state: {e}")
        return None


def load_lp_positions() -> Optional[dict]:
    """Load LP positions from lp_positions.json"""
    if not os.path.exists(LP_POSITIONS_FILE):
        logger.warning(f"LP positions not found: {LP_POSITIONS_FILE}")
        return None
    
    try:
        with open(LP_POSITIONS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading LP positions: {e}")
        return None


def normalize_token(token: str) -> str:
    """Normalize token symbol"""
    t = token.upper().strip()
    # Map wrapped to base
    if t == 'WETH':
        return 'ETH'
    if t in ('WBTC', 'BTCB'):
        return 'BTC'
    if t == 'WBNB':
        return 'BNB'
    return t


def get_token_type(token: str) -> str:
    """Classify token type"""
    t = normalize_token(token)
    
    if t in STABLES or token.upper() in STABLES:
        return 'stable'
    if t in ('ETH', 'BTC') or token.upper() in HEDGEABLE_MAJORS:
        return 'major'
    if t == 'BNB' or token.upper() in BNB_TOKENS:
        return 'bnb'
    return 'alt'


# ═══════════════════════════════════════════════════════════════════════════════
# POSITION CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def classify_position(token0: str, token1: str, balance_usd: float) -> PositionClassification:
    """
    Классифицируем позицию для хеджирования.
    
    Типы:
    - Volatile/Stable (ETH-USDC): полный хедж через PUT
    - Volatile/Volatile (ETH-BTC): частичный хедж
    - Alt/любой: не хеджируем (нет инструментов)
    """
    
    t0_type = get_token_type(token0)
    t1_type = get_token_type(token1)
    t0_norm = normalize_token(token0)
    t1_norm = normalize_token(token1)
    
    # Инициализация
    exposure = {'ETH': 0, 'BTC': 0, 'BNB': 0}
    half_balance = balance_usd / 2
    
    # Volatile/Stable — идеальный случай
    if t0_type == 'stable' and t1_type == 'major':
        exposure[t1_norm] = half_balance
        return PositionClassification(
            token0=token0, token1=token1, balance_usd=balance_usd,
            hedgeable=True, hedge_type='full', underlying=t1_norm,
            exposure_eth=exposure.get('ETH', 0),
            exposure_btc=exposure.get('BTC', 0),
            exposure_bnb=exposure.get('BNB', 0),
            note=f"PUT {t1_norm} хеджирует IL"
        )
    
    if t1_type == 'stable' and t0_type == 'major':
        exposure[t0_norm] = half_balance
        return PositionClassification(
            token0=token0, token1=token1, balance_usd=balance_usd,
            hedgeable=True, hedge_type='full', underlying=t0_norm,
            exposure_eth=exposure.get('ETH', 0),
            exposure_btc=exposure.get('BTC', 0),
            exposure_bnb=exposure.get('BNB', 0),
            note=f"PUT {t0_norm} хеджирует IL"
        )
    
    # Stable/BNB
    if t0_type == 'stable' and t1_type == 'bnb':
        exposure['BNB'] = half_balance
        return PositionClassification(
            token0=token0, token1=token1, balance_usd=balance_usd,
            hedgeable=True, hedge_type='full', underlying='BNB',
            exposure_eth=0, exposure_btc=0, exposure_bnb=half_balance,
            note="PUT BNB (если доступен)"
        )
    
    if t1_type == 'stable' and t0_type == 'bnb':
        exposure['BNB'] = half_balance
        return PositionClassification(
            token0=token0, token1=token1, balance_usd=balance_usd,
            hedgeable=True, hedge_type='full', underlying='BNB',
            exposure_eth=0, exposure_btc=0, exposure_bnb=half_balance,
            note="PUT BNB (если доступен)"
        )
    
    # Major/Major (ETH-BTC) — частичный хедж
    if t0_type == 'major' and t1_type == 'major':
        # Оба актива движутся — хедж одного не компенсирует IL полностью
        exposure['ETH'] = half_balance * 0.5  # 25% от позиции
        exposure['BTC'] = half_balance * 0.5  # 25% от позиции
        return PositionClassification(
            token0=token0, token1=token1, balance_usd=balance_usd,
            hedgeable=True, hedge_type='partial', underlying='both',
            exposure_eth=exposure['ETH'],
            exposure_btc=exposure['BTC'],
            exposure_bnb=0,
            note="Частичный хедж — PUT на один актив не компенсирует IL полностью"
        )
    
    # Alt/Major или Alt/Stable — не хеджируем
    if t0_type == 'alt' or t1_type == 'alt':
        return PositionClassification(
            token0=token0, token1=token1, balance_usd=balance_usd,
            hedgeable=False, hedge_type='none', underlying=None,
            exposure_eth=0, exposure_btc=0, exposure_bnb=0,
            note="Нет ликвидных опционов на alt токены"
        )
    
    # BNB/Major — сложный случай, не хеджируем
    if (t0_type == 'bnb' and t1_type == 'major') or (t1_type == 'bnb' and t0_type == 'major'):
        return PositionClassification(
            token0=token0, token1=token1, balance_usd=balance_usd,
            hedgeable=False, hedge_type='none', underlying=None,
            exposure_eth=0, exposure_btc=0, exposure_bnb=0,
            note="Cross-chain пара, сложно хеджировать"
        )
    
    # Default — не хеджируем
    return PositionClassification(
        token0=token0, token1=token1, balance_usd=balance_usd,
        hedgeable=False, hedge_type='none', underlying=None,
        exposure_eth=0, exposure_btc=0, exposure_bnb=0,
        note="Неизвестный тип пары"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HEDGE CALCULATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_hedge_score(dir_value: float, tail_risk_active: bool, tail_polarity: str) -> float:
    """
    Рассчитываем Hedge Score.
    
    Используем ТОЛЬКО Dir как главный сигнал.
    TailRisk — бинарный override для экстремальных ситуаций.
    """
    
    # Базовый score из Dir (только downside)
    base_score = max(0, -dir_value)  # [0, 1]
    
    # TailRisk override — минимум 0.7 при активном downside tail
    if tail_risk_active and tail_polarity == 'downside':
        hedge_score = max(0.7, base_score)
    else:
        hedge_score = base_score
    
    return min(1.0, hedge_score)


def calculate_hedge_ratio(
    hedge_score: float, 
    confidence: float, 
    tail_risk_active: bool, 
    vol_z: float
) -> float:
    """
    Рассчитываем Hedge Ratio.
    
    При TailRisk НЕ снижаем из-за низкой confidence.
    """
    
    base_ratio = hedge_score
    
    # Confidence adjustment
    if tail_risk_active:
        confidence_adj = 1.0  # Не снижаем при TailRisk
    else:
        confidence_adj = 0.7 + 0.3 * confidence  # [0.7, 1.0]
    
    # Vol adjustment (прокси IV)
    if vol_z > 1.5:
        vol_adj = 0.7  # Дорогие премии
    elif vol_z > 1.0:
        vol_adj = 0.85
    else:
        vol_adj = 1.0
    
    hedge_ratio = base_ratio * confidence_adj * vol_adj
    
    return min(MAX_HEDGE_RATIO, max(0.0, hedge_ratio))


def generate_recommendations(
    exposure: Dict[str, float],
    hedge_ratio: float,
    eth_price: float,
    btc_price: float,
    use_live_pricing: bool = True
) -> List[HedgeRecommendation]:
    """Генерируем конкретные рекомендации по опционам"""
    
    recommendations = []
    live_quotes = None
    
    # Try to get live pricing from Aevo
    if use_live_pricing:
        try:
            from aevo_api import get_hedge_quotes
            
            eth_notional = exposure.get('ETH', 0) * hedge_ratio
            btc_notional = exposure.get('BTC', 0) * hedge_ratio
            
            if eth_notional > 500 or btc_notional > 500:
                live_quotes = get_hedge_quotes(
                    eth_notional=eth_notional if eth_notional > 500 else 0,
                    btc_notional=btc_notional if btc_notional > 500 else 0,
                    strike_pct=1 - DEFAULT_STRIKE_DISTANCE,  # 0.90 for -10%
                    expiry_days=DEFAULT_EXPIRY_DAYS
                )
                logger.info("Got live quotes from Aevo")
        except Exception as e:
            logger.warning(f"Aevo API failed, using estimates: {e}")
            live_quotes = None
    
    for asset, exp in exposure.items():
        if exp <= 0:
            continue
        
        # BNB пока пропускаем — нет надёжных опционов
        if asset == 'BNB':
            continue
        
        notional = exp * hedge_ratio
        
        if notional < 500:  # Минимум $500 на опцион
            continue
        
        # Check for live quote
        live_quote = None
        if live_quotes:
            if asset == 'ETH' and live_quotes.get('eth'):
                live_quote = live_quotes['eth']
            elif asset == 'BTC' and live_quotes.get('btc'):
                live_quote = live_quotes['btc']
        
        if live_quote:
            # Use real pricing from Aevo
            option = live_quote.get('option', {})
            recommendations.append(HedgeRecommendation(
                underlying=asset,
                action='PUT',
                strike_pct=DEFAULT_STRIKE_DISTANCE,
                expiry_days=option.get('days_to_expiry', DEFAULT_EXPIRY_DAYS),
                notional_usd=notional,
                max_premium_usd=live_quote.get('total_premium_usd', notional * PREMIUM_BUDGET_PCT),
                platform='Aevo',
                # Extended fields from live data
                instrument_name=option.get('instrument_name'),
                strike_price=option.get('strike'),
                mark_price=option.get('mark_price'),
                iv=option.get('iv'),
                bid_price=option.get('bid_price'),
                ask_price=option.get('ask_price')
            ))
        else:
            # Fallback to estimates
            if asset == 'ETH':
                strike_price = eth_price * (1 - DEFAULT_STRIKE_DISTANCE)
            elif asset == 'BTC':
                strike_price = btc_price * (1 - DEFAULT_STRIKE_DISTANCE)
            else:
                continue
            
            max_premium = notional * PREMIUM_BUDGET_PCT
            
            recommendations.append(HedgeRecommendation(
                underlying=asset,
                action='PUT',
                strike_pct=DEFAULT_STRIKE_DISTANCE,
                expiry_days=DEFAULT_EXPIRY_DAYS,
                notional_usd=notional,
                max_premium_usd=max_premium,
                platform='Aevo'
            ))
    
    return recommendations


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class LPHedgeEngine:
    """Main hedge engine class"""
    
    def __init__(self):
        self.regime_state = None
        self.positions = []
        self.classifications = []
        self.decision = None
    
    def load_data(self) -> bool:
        """Load all required data"""
        
        # Load regime state
        self.regime_state = load_regime_state()
        if not self.regime_state:
            logger.error("Failed to load regime state")
            return False
        
        # Load LP positions
        lp_data = load_lp_positions()
        if not lp_data:
            logger.error("Failed to load LP positions")
            return False
        
        self.positions = lp_data.get('positions', [])
        
        if not self.positions:
            logger.warning("No LP positions found")
            return False
        
        logger.info(f"Loaded {len(self.positions)} positions")
        return True
    
    def classify_positions(self) -> Dict[str, float]:
        """
        Classify all positions and calculate exposure.
        
        NEW APPROACH (per analyst feedback):
        - Use total TVL as base
        - Apply fixed proportions: BTC 40%, ETH 60%
        - This ensures hedge covers directional risk regardless of actual LP composition
        """
        
        self.classifications = []
        actual_exposure = {'ETH': 0, 'BTC': 0, 'BNB': 0}
        non_hedgeable = 0
        total_tvl = 0
        
        # First pass: classify and sum TVL
        for pos in self.positions:
            token0 = pos.get('token0_symbol', '')
            token1 = pos.get('token1_symbol', '')
            balance = pos.get('balance_usd', 0)
            total_tvl += balance
            
            classification = classify_position(token0, token1, balance)
            self.classifications.append(classification)
            
            # Track actual exposure for logging
            if classification.hedgeable:
                actual_exposure['ETH'] += classification.exposure_eth
                actual_exposure['BTC'] += classification.exposure_btc
                actual_exposure['BNB'] += classification.exposure_bnb
            else:
                non_hedgeable += balance
        
        # NEW: Calculate hedge exposure based on TVL with fixed proportions
        # Rationale: LP positions have crypto exposure regardless of pair composition
        # We hedge the directional risk of the portfolio, not individual positions
        
        # Proportions (configurable)
        ETH_PROPORTION = 0.60  # 60% of hedgeable TVL attributed to ETH
        BTC_PROPORTION = 0.40  # 40% of hedgeable TVL attributed to BTC
        
        # Hedgeable TVL = TVL minus stablecoin-only positions
        hedgeable_tvl = total_tvl - non_hedgeable
        
        # If most positions are non-hedgeable, use TVL * 0.5 as proxy
        if hedgeable_tvl < total_tvl * 0.3:
            hedgeable_tvl = total_tvl * 0.5
            logger.info(f"Low hedgeable exposure, using TVL proxy: ${hedgeable_tvl:.0f}")
        
        exposure = {
            'ETH': hedgeable_tvl * ETH_PROPORTION,
            'BTC': hedgeable_tvl * BTC_PROPORTION,
            'BNB': 0  # BNB hedging not reliable yet
        }
        
        logger.info(f"TVL: ${total_tvl:.0f}, Hedgeable: ${hedgeable_tvl:.0f}")
        logger.info(f"Exposure (TVL-based): ETH=${exposure['ETH']:.0f} (60%), BTC=${exposure['BTC']:.0f} (40%)")
        logger.info(f"Actual exposure: ETH=${actual_exposure['ETH']:.0f}, BTC=${actual_exposure['BTC']:.0f}")
        logger.info(f"Non-hedgeable (stables, alts): ${non_hedgeable:.0f}")
        
        return exposure, non_hedgeable
    
    def calculate_decision(self) -> HedgeDecision:
        """Calculate hedge decision"""
        
        # Extract regime metrics
        risk = self.regime_state.get('risk', {})
        meta = self.regime_state.get('asset_allocation', {}).get('meta', {})
        conf = self.regime_state.get('confidence', {})
        metadata = self.regime_state.get('metadata', {})
        
        dir_value = risk.get('risk_level', 0)
        tail_risk_active = meta.get('tail_risk_active', False)
        tail_polarity = meta.get('tail_polarity', '')
        confidence = conf.get('quality_adjusted', 0.5)
        vol_z = metadata.get('vol_z', 0)
        eth_price = metadata.get('eth_price', 2000)
        btc_price = metadata.get('btc_price', 80000)
        
        # Classify positions
        exposure, non_hedgeable = self.classify_positions()
        total_hedgeable = sum(exposure.values())
        total_tvl = total_hedgeable + non_hedgeable
        
        # Calculate hedge score
        hedge_score = calculate_hedge_score(dir_value, tail_risk_active, tail_polarity)
        
        # Check minimum exposure
        if total_hedgeable < MIN_HEDGEABLE_EXPOSURE:
            self.decision = HedgeDecision(
                timestamp=datetime.now(timezone.utc).isoformat(),
                action='NO_HEDGE',
                reason=f'Hedgeable exposure < ${MIN_HEDGEABLE_EXPOSURE:,} (${total_hedgeable:,.0f})',
                dir_value=dir_value,
                tail_risk_active=tail_risk_active,
                tail_polarity=tail_polarity,
                confidence=confidence,
                vol_z=vol_z,
                hedge_score=hedge_score,
                hedge_ratio=0,
                total_tvl=total_tvl,
                hedgeable_exposure=exposure,
                non_hedgeable_exposure=non_hedgeable,
                recommendations=[]
            )
            return self.decision
        
        # Check hedge score threshold
        if hedge_score < HEDGE_SCORE_THRESHOLD:
            self.decision = HedgeDecision(
                timestamp=datetime.now(timezone.utc).isoformat(),
                action='NO_HEDGE',
                reason=f'Hedge Score низкий ({hedge_score:.2f} < {HEDGE_SCORE_THRESHOLD})',
                dir_value=dir_value,
                tail_risk_active=tail_risk_active,
                tail_polarity=tail_polarity,
                confidence=confidence,
                vol_z=vol_z,
                hedge_score=hedge_score,
                hedge_ratio=0,
                total_tvl=total_tvl,
                hedgeable_exposure=exposure,
                non_hedgeable_exposure=non_hedgeable,
                recommendations=[]
            )
            return self.decision
        
        # Calculate hedge ratio
        hedge_ratio = calculate_hedge_ratio(hedge_score, confidence, tail_risk_active, vol_z)
        
        # Check IV (vol_z as proxy)
        if vol_z > 1.5 and hedge_score < 0.6:
            self.decision = HedgeDecision(
                timestamp=datetime.now(timezone.utc).isoformat(),
                action='WAIT',
                reason=f'IV высокая (vol_z={vol_z:.2f}), hedge_score недостаточен ({hedge_score:.2f})',
                dir_value=dir_value,
                tail_risk_active=tail_risk_active,
                tail_polarity=tail_polarity,
                confidence=confidence,
                vol_z=vol_z,
                hedge_score=hedge_score,
                hedge_ratio=hedge_ratio,
                total_tvl=total_tvl,
                hedgeable_exposure=exposure,
                non_hedgeable_exposure=non_hedgeable,
                recommendations=[]
            )
            return self.decision
        
        # Generate recommendations
        recommendations = generate_recommendations(exposure, hedge_ratio, eth_price, btc_price)
        
        self.decision = HedgeDecision(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action='HEDGE',
            reason=f'Dir={dir_value:.2f}, TailRisk={"Active" if tail_risk_active else "No"}',
            dir_value=dir_value,
            tail_risk_active=tail_risk_active,
            tail_polarity=tail_polarity,
            confidence=confidence,
            vol_z=vol_z,
            hedge_score=hedge_score,
            hedge_ratio=hedge_ratio,
            total_tvl=total_tvl,
            hedgeable_exposure=exposure,
            non_hedgeable_exposure=non_hedgeable,
            recommendations=[asdict(r) for r in recommendations]
        )
        
        return self.decision
    
    def format_report(self) -> str:
        """Format hedge report for Telegram - with economic analysis"""
        
        if not self.decision:
            return "🛡️ Hedge: No data"
        
        d = self.decision
        lines = []
        
        # Header with status
        if d.action == 'NO_HEDGE':
            lines.append("🛡️ Hedge: Not required")
            lines.append(f"  {d.reason}")
        elif d.action == 'WAIT':
            lines.append("🛡️ Hedge: Wait")
            lines.append(f"  {d.reason}")
        else:
            lines.append("🛡️ Hedge: Recommended")
            lines.append(f"  Score: {d.hedge_score:.0%} → Ratio: {d.hedge_ratio:.0%}")
        
        # Risk indicators - compact
        tail_str = "⚠️" if d.tail_risk_active else ""
        lines.append(f"  Dir: {d.dir_value:+.2f} {tail_str}")
        
        # Exposure breakdown
        if d.action == 'HEDGE' and d.recommendations:
            lines.append("")
            lines.append("Exposure (TVL-based):")
            
            total_hedgeable = sum(d.hedgeable_exposure.values())
            for asset, exp in d.hedgeable_exposure.items():
                if exp > 0:
                    hedge_amt = exp * d.hedge_ratio
                    pct = (exp / d.total_tvl * 100) if d.total_tvl > 0 else 0
                    lines.append(f"  {asset}: ${exp:,.0f} ({pct:.0f}%) → hedge ${hedge_amt:,.0f}")
            
            if d.non_hedgeable_exposure > 0:
                pct = (d.non_hedgeable_exposure / d.total_tvl * 100) if d.total_tvl > 0 else 0
                lines.append(f"  Other: ${d.non_hedgeable_exposure:,.0f} ({pct:.0f}%) - no hedge available")
            
            # Recommendations with economics
            lines.append("")
            
            total_premium = 0
            for i, rec in enumerate(d.recommendations, 1):
                underlying = rec['underlying']
                notional = rec['notional_usd']
                
                # Calculate premium
                if rec.get('mark_price') and rec.get('strike_price'):
                    # Real premium from Aevo
                    contracts = notional / rec['strike_price']
                    premium = contracts * rec['mark_price']
                    total_premium += premium
                    
                    lines.append(f"PUT {underlying}:")
                    if rec.get('instrument_name'):
                        lines.append(f"  {rec['instrument_name']}")
                    lines.append(f"  Notional: ${notional:,.0f} | Premium: ${premium:,.0f}")
                    
                    # Break-even calculation
                    strike = rec['strike_price']
                    premium_pct = (premium / notional) * 100
                    break_even_drop = ((rec.get('strike_price', 0) / (notional / contracts)) - 1) * 100 if contracts > 0 else -10
                    
                    lines.append(f"  Strike: ${strike:,.0f} | Cost: {premium_pct:.1f}%")
                else:
                    # Estimate
                    premium = rec['max_premium_usd']
                    total_premium += premium
                    lines.append(f"PUT {underlying}: ${notional:,.0f} (est. ${premium:,.0f})")
            
            # Economic summary
            if total_premium > 0:
                lines.append("")
                premium_pct = (total_premium / d.total_tvl) * 100
                lines.append(f"📊 Total cost: ${total_premium:,.0f} ({premium_pct:.2f}% of TVL)")
                
                # Break-even scenarios
                lines.append("Break-even: hedge pays off if drop >~13%")
                
                # Is it worth it?
                if d.hedge_score >= 0.7:
                    lines.append("✓ High risk - hedge justified")
                elif d.hedge_score >= 0.5:
                    lines.append("~ Moderate risk - consider partial hedge")
                else:
                    lines.append("? Low risk - hedge may be expensive")
        
        return "\n".join(lines)
    
    def save_state(self):
        """Save hedge state"""
        if not self.decision:
            return
        
        os.makedirs(os.path.dirname(HEDGE_STATE_FILE), exist_ok=True)
        
        state = {
            'decision': asdict(self.decision),
            'classifications': [asdict(c) for c in self.classifications],
            'updated': datetime.now(timezone.utc).isoformat()
        }
        
        with open(HEDGE_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        
        logger.info(f"Hedge state saved to {HEDGE_STATE_FILE}")
    
    def run(self) -> Optional[str]:
        """Run full hedge analysis"""
        
        logger.info("=" * 60)
        logger.info("LP HEDGE ENGINE v1.0")
        logger.info("=" * 60)
        
        if not self.load_data():
            return None
        
        self.calculate_decision()
        self.save_state()
        
        report = self.format_report()
        
        logger.info("\n" + report)
        
        return report


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

def run_hedge_analysis() -> Optional[HedgeDecision]:
    """Run hedge analysis and return decision"""
    engine = LPHedgeEngine()
    engine.run()
    return engine.decision


if __name__ == "__main__":
    engine = LPHedgeEngine()
    report = engine.run()
    
    if report:
        print("\n" + "=" * 60)
        print(report)
        print("=" * 60)
