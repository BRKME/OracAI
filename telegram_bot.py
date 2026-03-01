"""
Telegram Bot — Action-First UI
One screen → one decision.
"""

import os
import logging
import requests

import settings as cfg

# Cycle Position Engine
try:
    from cycle_position_engine import (
        CyclePositionEngine, CycleMetrics, CyclePosition,
        CyclePhase, BottomTopSignal, ActionSignal, create_cycle_policy
    )
    from cycle_metrics_collector import build_cycle_metrics, get_cycle_position
    CYCLE_ENGINE_AVAILABLE = True
except ImportError:
    CYCLE_ENGINE_AVAILABLE = False

logger = logging.getLogger(__name__)


# ============================================================
# FORMAT OUTPUT
# ============================================================

def format_output(output: dict, lp_policy=None, allocation=None) -> str:
    """
    Structured risk-focused format.
    Metric names: English
    Comments: Russian
    """
    meta = output.get("metadata", {})
    risk = output.get("risk", {})
    conf = output.get("confidence", {})
    buckets = output.get("buckets", {})
    regime = output.get("regime", "?")
    probs = output.get("probabilities", {})
    flags = output.get("risk_flags", [])
    norm = output.get("normalization", {})
    
    btc_price = meta.get("btc_price", 0)
    eth_price = meta.get("eth_price", 0)
    risk_level = risk.get("risk_level", 0)
    conf_adj = conf.get("quality_adjusted", 0)
    days = meta.get("days_in_regime", 0)
    vol_z = meta.get("vol_z", 0)
    struct_break = norm.get("break_active", False)
    mom = buckets.get("Momentum", 0)
    
    # RSI data
    rsi_data = meta.get("rsi", {})
    rsi_1d = rsi_data.get("rsi_1d")
    rsi_2h = rsi_data.get("rsi_2h")
    rsi_1d_7 = rsi_data.get("rsi_1d_7")
    rsi_source = rsi_data.get("source", "none")
    
    # Tail risk
    tail_active = False
    tail_polarity = None
    if allocation:
        tail_active = allocation.get("meta", {}).get("tail_risk_active", False)
        tail_polarity = allocation.get("meta", {}).get("tail_polarity", "downside")
    
    conf_pct = int(conf_adj * 100)
    
    lines = []
    
    # ══════════════════════════════════════════════════════
    # 1. MARKET PHASE - Visual scale
    # ══════════════════════════════════════════════════════
    
    # Position marker based on regime
    phase_positions = {
        "BULL": 0,
        "RANGE": 1, 
        "TRANSITION": 2,
        "BEAR": 3
    }
    current_pos = phase_positions.get(regime, 2)
    
    # Build scale line
    scale_labels = "BULL ─── RANGE ─── TRANSITION ─── BEAR"
    # Marker positions (approximate character positions)
    marker_positions = [2, 13, 26, 43]
    marker_line = " " * marker_positions[current_pos] + "▲"
    
    lines.append(scale_labels)
    lines.append(marker_line)
    lines.append("")
    
    # Regime emoji and info
    regime_emoji = {"BULL": "🟢", "BEAR": "🔴", "RANGE": "🟡", "TRANSITION": "⚪"}.get(regime, "⚪")
    
    # Visual confidence bar
    filled = int(conf_adj * 10)
    empty = 10 - filled
    conf_bar = '█' * filled + '░' * empty
    
    lines.append(f"{regime_emoji} {regime} ({days}d)")
    lines.append(f"[{conf_bar}] {conf_pct}%")
    
    # Directional pressure
    if risk_level < 0:
        lines.append(f"↓ Downside pressure. Dir: ↓ {abs(risk_level):.2f}")
    else:
        lines.append(f"↑ Upside pressure. Dir: ↑ {abs(risk_level):.2f}")
    
    lines.append("")
    
    # Regime probabilities with visual bars
    prob_bull = probs.get("BULL", 0)
    prob_bear = probs.get("BEAR", 0)
    prob_range = probs.get("RANGE", 0)
    prob_trans = probs.get("TRANSITION", 0)
    
    def make_bar(value, width=12):
        filled = int(value * width)
        return "█" * filled + "░" * (width - filled)
    
    lines.append("Regime probabilities:")
    lines.append(f"BULL       {make_bar(prob_bull)} {int(prob_bull*100)}%")
    lines.append(f"BEAR       {make_bar(prob_bear)} {int(prob_bear*100)}%")
    lines.append(f"RANGE      {make_bar(prob_range)} {int(prob_range*100)}%")
    lines.append(f"TRANSITION {make_bar(prob_trans)} {int(prob_trans*100)}%")
    
    lines.append("")
    
    # AI Comment - analytical, no emotions
    ai_comment = _generate_analytical_comment(
        regime=regime,
        prob_bear=prob_bear,
        prob_trans=prob_trans,
        prob_bull=prob_bull,
        conf_pct=conf_pct,
        dir_value=risk_level,
        tail_active=tail_active,
        struct_break=struct_break,
        vol_z=vol_z
    )
    lines.append(f"→ {ai_comment}")
    
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 2. RISK SCALE
    # ══════════════════════════════════════════════════════
    
    # Determine risk state
    if tail_active:
        risk_state = "TAIL"
        risk_pos = 2
    elif vol_z > 1.5 or struct_break:
        risk_state = "ELEVATED"
        risk_pos = 1
    elif vol_z > 2.5:
        risk_state = "CRISIS"
        risk_pos = 3
    else:
        risk_state = "NORMAL"
        risk_pos = 0
    
    lines.append("⚠️ RISK SCALE")
    risk_scale = "NORMAL ─── ELEVATED ─── TAIL ─── CRISIS"
    risk_marker_positions = [3, 18, 32, 42]
    risk_marker_line = " " * risk_marker_positions[risk_pos] + "▲"
    lines.append(risk_scale)
    lines.append(risk_marker_line)
    lines.append("")
    
    # Risk components with Russian comments
    # Volatility - SYNCHRONIZED with tail_active
    # Если tail_active, но vol_z низкий - это structural risk, не volatility
    if vol_z > 2.0:
        vol_regime = "TAIL (p95+)"
        vol_comment = "Волатильность выше 95-го перцентиля; повышена вероятность резких импульсов."
    elif vol_z > 1.5 or tail_active:
        vol_regime = "ELEVATED"
        if tail_active and vol_z <= 1.5:
            vol_comment = "Структурный риск повышен; волатильность может резко вырасти."
        else:
            vol_comment = "Волатильность повышена; рекомендуется снижение размера позиций."
    elif vol_z > 1.0:
        vol_regime = "MODERATE"
        vol_comment = "Волатильность умеренно повышена."
    else:
        vol_regime = "NORMAL"
        vol_comment = "Волатильность в пределах нормы."
    
    lines.append(f"Volatility: {vol_regime}")
    lines.append(f"  → {vol_comment}")
    
    # Structure
    if struct_break:
        lines.append("Structure: BREAK")
        lines.append("  → Нарушена рыночная структура; фаза перераспределения.")
    else:
        lines.append("Structure: INTACT")
        lines.append("  → Структура сохранена.")
    
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 3. SPOT SIGNAL - Cycle Position Engine
    # ══════════════════════════════════════════════════════
    spot_lines = _format_spot_signal(allocation, conf_adj, regime, risk_level, output, rsi_1d, rsi_2h)
    lines.extend(spot_lines)
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 4. LP POLICY - Keep as is (good)
    # ══════════════════════════════════════════════════════
    if lp_policy:
        risk_lp = lp_policy.risk_lp
        risk_dir = lp_policy.risk_directional
        quadrant = lp_policy.risk_quadrant.value
        fv = lp_policy.fee_variance_ratio
        max_exp = int(lp_policy.max_exposure * 100)
        hedge = lp_policy.hedge_recommended
        range_width = lp_policy.range_width
        
        # Simple quadrant description
        quadrant_desc = {
            "Q1": "🟢 LP: Ideal conditions",
            "Q2": "🔵 LP: Good, but hedge needed",
            "Q3": "🟡 LP: Spot preferred",
            "Q4": "🔴 LP: Minimize exposure",
        }
        lines.append(quadrant_desc.get(quadrant, f"LP: {quadrant}"))
        
        # Key metrics
        lines.append(f"  Exposure: {max_exp}% | Range: {range_width}")
        
        # Fee vs IL ratio
        if fv >= 1.5:
            lines.append(f"  Fees vs IL: {fv:.1f}x ✓")
        elif fv >= 1.0:
            lines.append(f"  Fees vs IL: {fv:.1f}x (marginal)")
        else:
            lines.append(f"  Fees vs IL: {fv:.1f}x (IL превышает)")
        
        if hedge:
            lines.append(f"  Hedge: REQUIRED")
        
        # LP comment
        lp_comment = _get_lp_comment(quadrant, risk_lp, risk_dir, max_exp, max_exp)
        lines.append(f"  → {lp_comment}")
        
        lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 5. FLAGS - Fully restored
    # ══════════════════════════════════════════════════════
    display_flags = []
    
    # Tail risk flag - text depends on actual volatility
    if tail_active:
        if vol_z > 2.0:
            display_flags.append("Tail risk (экстремальная волатильность)")
        elif vol_z > 1.5:
            display_flags.append("Tail risk (повышенная волатильность)")
        else:
            display_flags.append("Structural risk (повышенный направленный риск)")
    
    if struct_break:
        display_flags.append("Structure break (слом структуры)")
    
    # Data quality
    data_quality = meta.get("data_completeness", 1.0)
    failed_sources = meta.get("failed_sources", [])
    
    if failed_sources:
        display_flags.append(f"Нет данных: {', '.join(failed_sources)}")
    elif data_quality < 0.85:
        display_flags.append("Partial data — проверь источники")
    
    if display_flags:
        lines.append("FLAGS")
        for f in display_flags:
            lines.append(f"  • {f}")
        lines.append("")
    
    # ══════════════════════════════════════════════════════
    # FOOTER
    # ══════════════════════════════════════════════════════
    lines.append("v4.4")
    
    return "\n".join(lines)


def _generate_analytical_comment(
    regime: str,
    prob_bear: float,
    prob_trans: float,
    prob_bull: float,
    conf_pct: int,
    dir_value: float,
    tail_active: bool,
    struct_break: bool,
    vol_z: float
) -> str:
    """
    Generate analytical comment without emotional language.
    
    Requirements:
    - No emotional words (паника, дно, страх)
    - No reversal predictions
    - Reflect regime conflict
    - Highlight low confidence
    - Note probability of sharp moves
    - Neutral, risk-oriented tone
    - Max 2-3 sentences
    """
    
    parts = []
    
    # Volatility state - SYNCHRONIZED
    # Only say "extreme" if vol_z actually high
    if vol_z > 2.0:
        vol_state = "Экстремальная волатильность"
    elif vol_z > 1.5:
        vol_state = "Повышенная волатильность"
    elif tail_active:
        vol_state = "Повышенный структурный риск"
    else:
        vol_state = None
    
    # Structure state
    struct_state = "слом структуры" if struct_break else None
    
    # Build first part
    first_part_items = [x for x in [vol_state, struct_state] if x]
    if first_part_items:
        first_part = " и ".join(first_part_items).capitalize()
    else:
        first_part = None
    
    # Regime conflict analysis
    max_prob = max(prob_bear, prob_trans, prob_bull)
    second_prob = sorted([prob_bear, prob_trans, prob_bull])[-2]
    
    if abs(prob_bear - prob_trans) < 0.15 and prob_bear > 0.3 and prob_trans > 0.3:
        regime_conflict = f"Конфликт TRANSITION ({int(prob_trans*100)}%) и BEAR ({int(prob_bear*100)}%) указывает на нестабильную фазу перераспределения риска."
    elif prob_trans > prob_bear and prob_trans > 0.4:
        regime_conflict = f"Доминирование переходного режима ({int(prob_trans*100)}%) при медвежьем уклоне."
    elif prob_bear > 0.5:
        regime_conflict = f"Выраженный медвежий режим ({int(prob_bear*100)}%)."
    elif prob_bull > 0.5:
        regime_conflict = f"Выраженный бычий режим ({int(prob_bull*100)}%)."
    else:
        regime_conflict = "Смешанные сигналы без выраженного доминирования."
    
    # Confidence impact
    if conf_pct < 25:
        conf_impact = f"Низкая уверенность модели ({conf_pct}%) повышает вероятность резких и разнонаправленных импульсов без устойчивого трендового подтверждения."
    elif conf_pct < 40:
        conf_impact = f"Умеренно низкая уверенность ({conf_pct}%) снижает надёжность текущего режима."
    else:
        conf_impact = None
    
    # Combine
    if first_part:
        parts.append(first_part + " " + regime_conflict.lower() if regime_conflict[0].isupper() else first_part + ".")
        if conf_impact:
            parts.append(conf_impact)
    else:
        parts.append(regime_conflict)
        if conf_impact:
            parts.append(conf_impact)
    
    return " ".join(parts)


def _get_regime_comment(regime: str, days: int, tail_active: bool, conf: float, mom: float, risk: float) -> str:
    """
    Rich logic комментарий по режиму (Russian).
    Контекстный — учитывает комбинацию факторов.
    """
    if regime == "BEAR":
        if tail_active and conf < 0.25:
            return "Паника на рынке. Возможно близко дно — не лучшее время продавать."
        elif tail_active:
            return "Сильный стресс. Защита капитала, но осторожно с продажами на лоях."
        elif days <= 2:
            return "Начало коррекции. Наблюдаем глубину падения."
        elif days > 14 and mom > -0.3:
            return "Затяжной медвежий тренд, но импульс слабеет. Возможен разворот."
        elif days > 14:
            return "Затяжной медвежий тренд. Терпение, ждём сигналы разворота."
        else:
            return "Рынок слабый. Защита капитала в приоритете."
    
    elif regime == "BULL":
        if tail_active:
            return "Рост перегрет. Фиксация прибыли разумна."
        elif days <= 2:
            return "Возможное начало роста. Подтверждение нужно."
        elif days > 14 and mom < 0.3:
            return "Зрелый бычий тренд, импульс слабеет. Осторожность."
        elif conf >= 0.6:
            return "Уверенный рост. Можно наращивать позиции."
        else:
            return "Рынок растёт. Умеренный риск допустим."
    
    elif regime == "TRANSITION":
        if risk < -0.3:
            return "Переходный период с негативным уклоном. Лучше подождать."
        elif risk > 0.3:
            return "Переходный период с позитивным уклоном. Наблюдаем."
        else:
            return "Неопределённость. Ждём ясности перед действиями."
    
    else:  # RANGE
        if conf >= 0.5:
            return "Боковик. Нет направления, но стабильно."
        else:
            return "Боковик с низкой уверенностью. Ждём."


def _get_directional_comment(btc_action: str, eth_action: str, regime: str, 
                              tail_active: bool, conf: float, mom: float) -> str:
    """
    Rich logic комментарий по directional (Russian).
    """
    if tail_active and "SELL" in btc_action:
        if conf < 0.25:
            return "Tail risk, но низкая уверенность — возможно паника. Осторожно."
        else:
            return "Tail risk активен — снижаем экспозицию."
    
    if btc_action == "HOLD" and eth_action == "HOLD":
        if conf < 0.4:
            return "Сигнал ниже порога — без действий"
        elif regime == "TRANSITION":
            return "Переходный режим — ждём подтверждения."
        else:
            return "Условия не соответствуют критериям входа/выхода."
    
    if "BUY" in btc_action:
        return "Условия для наращивания позиций."
    
    if "SELL" in btc_action and not tail_active:
        return "Условия для сокращения позиций."
    
    return ""


def _get_lp_comment(quadrant: str, risk_lp: float, risk_dir: float, eff: int, max_exp: int) -> str:
    """
    Rich logic комментарий по LP (Russian).
    """
    if quadrant == "Q1":
        return "Идеальные условия для LP. Низкий риск, хорошие комиссии."
    
    elif quadrant == "Q2":
        return "LP профитабелен, но высокий направленный риск — нужен хедж"
    
    elif quadrant == "Q3":
        return "Spot лучше LP. Направленный риск низкий, но LP не оптимален."
    
    elif quadrant == "Q4":
        return "Худшие условия. Минимизируй LP экспозицию."
    
    return ""


# ============================================================
# SHORT FORMAT (for daily summary)
# ============================================================

def format_short(output: dict, lp_policy=None, allocation=None) -> str:
    """
    Ultra-short format for daily notifications.
    """
    regime = output.get("regime", "?")
    risk = output.get("risk", {})
    risk_level = risk.get("risk_level", 0)
    meta = output.get("metadata", {})
    btc_price = meta.get("btc_price", 0)
    
    # Risk state
    if risk_level < -0.3:
        risk_state = "RISK-OFF"
    elif risk_level > 0.3:
        risk_state = "RISK-ON"
    else:
        risk_state = "NEUTRAL"
    
    lines = []
    lines.append(f"{risk_state} · {regime}")
    lines.append(f"BTC ${btc_price:,.0f}")
    
    if allocation:
        btc = allocation.get("btc", {})
        eth = allocation.get("eth", {})
        btc_action = btc.get("action", "HOLD")
        eth_action = eth.get("action", "HOLD")
        lines.append(f"BTC {btc_action} | ETH {eth_action}")
    
    if lp_policy:
        eff = int(lp_policy.effective_exposure * 100)
        hedge = "hedged" if lp_policy.hedge_recommended else ""
        lines.append(f"LP: {eff}% {hedge}".strip())
    
    if allocation and allocation.get("meta", {}).get("tail_risk_active"):
        lines.append("⚠️ Tail risk active")
    
    return "\n".join(lines)


# ============================================================
# SPOT SIGNAL WITH CYCLE POSITION
# ============================================================

def _format_spot_signal(allocation: dict, conf_adj: float, regime: str, risk_level: float, 
                         output: dict = None, rsi_1d: float = None, rsi_2h: float = None) -> list:
    """
    Форматирует блок SPOT SIGNAL с использованием Cycle Position Engine.
    
    Returns:
        List of formatted lines
    """
    lines = []
    
    # Get cycle position data if available
    btc_cycle = None
    eth_cycle = None
    data_source = "engine"  # Track where data comes from
    
    if CYCLE_ENGINE_AVAILABLE:
        try:
            _, btc_cycle = get_cycle_position("BTC")
            _, eth_cycle = get_cycle_position("ETH")
            if btc_cycle:
                data_source = "cycle_api"
        except Exception as e:
            logger.warning(f"Cycle position fetch failed: {e}")
    
    # Use BTC as primary signal (or fallback to allocation data)
    if btc_cycle:
        action = btc_cycle.action
        action_conf = btc_cycle.action_confidence
        phase = btc_cycle.phase
        cycle_pos = btc_cycle.cycle_position
        bottom_prox = btc_cycle.bottom_proximity
        top_prox = btc_cycle.top_proximity
        bt_signal = btc_cycle.bottom_top_signal
        reasons = btc_cycle.reasons
    else:
        # Smart fallback: derive from allocation + regime data
        btc = allocation.get("btc", {}) if allocation else {}
        btc_action = btc.get("action", "HOLD")
        btc_size = btc.get("size_pct", 0)
        
        # IMPORTANT: Use ADJUSTED size for signal determination
        # Raw -30% with 11% confidence = -3% adjusted = HOLD
        adj_btc_size = btc_size * conf_adj
        
        action_conf = conf_adj
        
        # === SMART FALLBACK: Estimate bottom/top from regime ===
        # Use regime + risk_level + confidence to estimate cycle position
        
        # Extract more data from output if available
        probs = output.get("probabilities", {}) if output else {}
        prob_bear = probs.get("BEAR", 0)
        prob_bull = probs.get("BULL", 0)
        
        meta = output.get("metadata", {}) if output else {}
        days_in_regime = meta.get("days_in_regime", 0)
        vol_z = meta.get("vol_z", 0)
        
        # Estimate phase based on regime
        if regime == "BEAR":
            if days_in_regime > 30 and risk_level < -0.5:
                phase_str = "CAPITULATION"
                bottom_prox = 0.7 + min(0.2, abs(risk_level) * 0.2)
                top_prox = 0.1
                cycle_pos = 15
            elif days_in_regime > 14:
                phase_str = "MID_BEAR"
                bottom_prox = 0.5 + min(0.2, abs(risk_level) * 0.2)
                top_prox = 0.2
                cycle_pos = 25
            else:
                phase_str = "EARLY_BEAR"
                bottom_prox = 0.3
                top_prox = 0.4
                cycle_pos = 35
        elif regime == "BULL":
            if days_in_regime > 30 and risk_level > 0.5:
                phase_str = "LATE_BULL"
                bottom_prox = 0.1
                top_prox = 0.7 + min(0.2, risk_level * 0.2)
                cycle_pos = 85
            elif days_in_regime > 14:
                phase_str = "MID_BULL"
                bottom_prox = 0.2
                top_prox = 0.5
                cycle_pos = 65
            else:
                phase_str = "EARLY_BULL"
                bottom_prox = 0.4
                top_prox = 0.3
                cycle_pos = 45
        elif regime == "TRANSITION":
            if risk_level < -0.3:
                phase_str = "DISTRIBUTION"
                bottom_prox = 0.3
                top_prox = 0.5
                cycle_pos = 60
            elif risk_level > 0.3:
                phase_str = "ACCUMULATION"
                bottom_prox = 0.5
                top_prox = 0.3
                cycle_pos = 30
            else:
                phase_str = "TRANSITION"
                bottom_prox = 0.4
                top_prox = 0.4
                cycle_pos = 50
        else:  # RANGE
            phase_str = "RANGE"
            bottom_prox = 0.35
            top_prox = 0.35
            cycle_pos = 50
        
        # Adjust by volatility
        if vol_z > 2.0:
            # Extreme volatility pushes closer to extremes
            if bottom_prox > top_prox:
                bottom_prox = min(0.9, bottom_prox + 0.1)
            else:
                top_prox = min(0.9, top_prox + 0.1)
        
        # ═══ RSI ADJUSTMENT ═══
        # RSI is a key indicator for cycle position
        if rsi_1d is not None:
            if rsi_1d <= 25:
                # Deeply oversold - increase bottom proximity
                bottom_prox = min(0.95, bottom_prox + 0.25)
                top_prox = max(0.05, top_prox - 0.2)
            elif rsi_1d <= 35:
                # Oversold
                bottom_prox = min(0.85, bottom_prox + 0.15)
                top_prox = max(0.1, top_prox - 0.1)
            elif rsi_1d >= 75:
                # Deeply overbought - increase top proximity
                top_prox = min(0.95, top_prox + 0.25)
                bottom_prox = max(0.05, bottom_prox - 0.2)
            elif rsi_1d >= 65:
                # Overbought
                top_prox = min(0.85, top_prox + 0.15)
                bottom_prox = max(0.1, bottom_prox - 0.1)
        
        phase = phase_str
        bt_signal = None
        
        # ═══════════════════════════════════════════════════════
        # CYCLE MODIFIER: Adjust signal based on cycle position
        # ═══════════════════════════════════════════════════════
        # Selling at bottom = bad idea
        # Buying at top = bad idea
        
        cycle_adjusted_size = adj_btc_size
        cycle_conflict = None
        
        if bottom_prox >= 0.5 and adj_btc_size < 0:
            # Near bottom + SELL signal = CONFLICT
            # Reduce sell signal proportionally to bottom proximity
            sell_dampener = 1.0 - (bottom_prox - 0.3)  # 0.5 bottom → 0.8x, 0.7 bottom → 0.6x
            sell_dampener = max(0.0, min(1.0, sell_dampener))
            cycle_adjusted_size = adj_btc_size * sell_dampener
            
            if bottom_prox >= 0.7:
                # Very close to bottom - consider contrarian BUY
                cycle_conflict = "BOTTOM"
                cycle_adjusted_size = min(0.05, abs(adj_btc_size) * 0.3)  # Flip to small BUY
            elif bottom_prox >= 0.5:
                cycle_conflict = "NEAR_BOTTOM"
        
        elif top_prox >= 0.5 and adj_btc_size > 0:
            # Near top + BUY signal = CONFLICT
            buy_dampener = 1.0 - (top_prox - 0.3)
            buy_dampener = max(0.0, min(1.0, buy_dampener))
            cycle_adjusted_size = adj_btc_size * buy_dampener
            
            if top_prox >= 0.7:
                # Very close to top - consider contrarian SELL
                cycle_conflict = "TOP"
                cycle_adjusted_size = max(-0.05, -abs(adj_btc_size) * 0.3)  # Flip to small SELL
            elif top_prox >= 0.5:
                cycle_conflict = "NEAR_TOP"
        
        # Determine final action based on CYCLE-ADJUSTED size
        if cycle_adjusted_size <= -0.15:
            action = ActionSignal.STRONG_SELL if CYCLE_ENGINE_AVAILABLE else "STRONG_SELL"
        elif cycle_adjusted_size <= -0.05:
            action = ActionSignal.SELL if CYCLE_ENGINE_AVAILABLE else "SELL"
        elif cycle_adjusted_size >= 0.15:
            action = ActionSignal.STRONG_BUY if CYCLE_ENGINE_AVAILABLE else "STRONG_BUY"
        elif cycle_adjusted_size >= 0.05:
            action = ActionSignal.BUY if CYCLE_ENGINE_AVAILABLE else "BUY"
        else:
            action = ActionSignal.HOLD if CYCLE_ENGINE_AVAILABLE else "HOLD"
        
        # Generate reasons from regime data
        reasons = []
        
        # Cycle conflict reason (most important)
        if cycle_conflict == "BOTTOM":
            reasons.append(f"⚠️ SELL на дне ({int(bottom_prox*100)}%) — сигнал инвертирован")
        elif cycle_conflict == "NEAR_BOTTOM":
            reasons.append(f"⚠️ SELL близко к дну ({int(bottom_prox*100)}%) — сигнал ослаблен")
        elif cycle_conflict == "TOP":
            reasons.append(f"⚠️ BUY на вершине ({int(top_prox*100)}%) — сигнал инвертирован")
        elif cycle_conflict == "NEAR_TOP":
            reasons.append(f"⚠️ BUY близко к вершине ({int(top_prox*100)}%) — сигнал ослаблен")
        
        if regime == "BEAR" and days_in_regime > 30:
            reasons.append("Затяжной медвежий тренд")
        elif regime == "BULL" and days_in_regime > 30:
            reasons.append("Зрелый бычий рынок")
        
        if conf_adj < 0.25:
            reasons.append(f"Низкая уверенность модели ({int(conf_adj*100)}%)")
        
        # Check if raw signal exists but adjusted is HOLD (and no cycle conflict)
        if not cycle_conflict and btc_size != 0 and abs(adj_btc_size) < 0.05:
            raw_signal = "SELL" if btc_size < 0 else "BUY"
            reasons.append(f"Сигнал {raw_signal} ослаблен низкой уверенностью → HOLD")
        
        if vol_z > 1.5:
            reasons.append("Повышенная волатильность")
        
        # RSI reasons
        if rsi_1d is not None:
            if rsi_1d <= 25:
                reasons.append(f"🟢 RSI oversold ({rsi_1d:.0f}) — покупка выгоднее")
            elif rsi_1d <= 35:
                reasons.append(f"RSI низкий ({rsi_1d:.0f})")
            elif rsi_1d >= 75:
                reasons.append(f"🔴 RSI overbought ({rsi_1d:.0f}) — продажа выгоднее")
            elif rsi_1d >= 65:
                reasons.append(f"RSI высокий ({rsi_1d:.0f})")
        
        if abs(risk_level) > 0.5:
            if risk_level < 0:
                reasons.append("Сильное давление вниз")
            else:
                reasons.append("Сильное давление вверх")
    
    # Get action string
    if CYCLE_ENGINE_AVAILABLE and hasattr(action, 'value'):
        action_str = action.value
    else:
        action_str = str(action)
    
    # ═══ HEADER ═══
    lines.append("📊 SPOT SIGNAL")
    
    # ═══ VISUAL SIGNAL SCALE ═══
    signal_map = {
        "STRONG_SELL": 0,
        "SELL": 1,
        "HOLD": 2,
        "BUY": 3,
        "STRONG_BUY": 4
    }
    signal_pos = signal_map.get(action_str, 2)
    
    signal_labels = ["⬇️ STRONG SELL", "⬇️ SELL", "➡️ HOLD", "⬆️ BUY", "⬆️ STRONG BUY"]
    
    lines.append("┌─────────────────────────────┐")
    for i, label in enumerate(signal_labels):
        if i == signal_pos:
            lines.append(f"│ {label:20} ◀──── │")
        else:
            lines.append(f"│ {label:25} │")
    lines.append("└─────────────────────────────┘")
    lines.append("")
    
    # ═══ PHASE & CYCLE POSITION ═══
    if phase:
        phase_str = phase.value if hasattr(phase, 'value') else str(phase)
        source_marker = "" if data_source == "cycle_api" else " ~"  # ~ means estimated
        lines.append(f"Phase: {phase_str}{source_marker} (conf: {int(action_conf*100)}%)")
        
        # Cycle position bar (0 = bottom, 100 = top)
        cycle_filled = int(cycle_pos / 10)
        cycle_bar = "█" * cycle_filled + "░" * (10 - cycle_filled)
        lines.append(f"Cycle: [{cycle_bar}] {int(cycle_pos)}/100")
        lines.append("")
    
    # ═══ RSI INDICATORS ═══
    if rsi_1d is not None or rsi_2h is not None:
        rsi_line = "RSI:"
        if rsi_1d is not None:
            # RSI status
            if rsi_1d <= 30:
                rsi_status = "🟢"
            elif rsi_1d >= 70:
                rsi_status = "🔴"
            else:
                rsi_status = "⚪"
            rsi_line += f" {rsi_status} 1D={rsi_1d:.0f}"
        if rsi_2h is not None:
            if rsi_2h <= 30:
                rsi_2h_status = "↓"
            elif rsi_2h >= 70:
                rsi_2h_status = "↑"
            else:
                rsi_2h_status = "→"
            rsi_line += f" | 2H={rsi_2h:.0f}{rsi_2h_status}"
        lines.append(rsi_line)
        lines.append("")
    
    # ═══ BOTTOM/TOP PROXIMITY ═══
    def prox_bar(value, width=10):
        filled = int(value * width)
        return "░" * (width - filled) + "▓" * filled
    
    est_marker = " ~" if data_source == "engine" else ""
    lines.append(f"Bottom {prox_bar(bottom_prox)} {int(bottom_prox*100)}%{est_marker}")
    lines.append(f"Top    {prox_bar(top_prox)} {int(top_prox*100)}%{est_marker}")
    lines.append("")
    
    # ═══ BTC/ETH SIGNALS ═══
    if allocation:
        btc = allocation.get("btc", {})
        eth = allocation.get("eth", {})
        btc_size = btc.get("size_pct", 0)
        eth_size = eth.get("size_pct", 0)
        
        adj_btc = btc_size * conf_adj
        adj_eth = eth_size * conf_adj
        
        def apply_cycle_modifier(adj_size, bottom_p, top_p):
            """Apply cycle position modifier to signal"""
            cycle_adj = adj_size
            conflict = None
            
            if bottom_p >= 0.5 and adj_size < 0:
                # Near bottom + SELL = conflict
                dampener = 1.0 - (bottom_p - 0.3)
                dampener = max(0.0, min(1.0, dampener))
                cycle_adj = adj_size * dampener
                if bottom_p >= 0.7:
                    conflict = "BOTTOM"
                    cycle_adj = min(0.05, abs(adj_size) * 0.3)
                elif bottom_p >= 0.5:
                    conflict = "NEAR_BOTTOM"
            elif top_p >= 0.5 and adj_size > 0:
                # Near top + BUY = conflict
                dampener = 1.0 - (top_p - 0.3)
                dampener = max(0.0, min(1.0, dampener))
                cycle_adj = adj_size * dampener
                if top_p >= 0.7:
                    conflict = "TOP"
                    cycle_adj = max(-0.05, -abs(adj_size) * 0.3)
                elif top_p >= 0.5:
                    conflict = "NEAR_TOP"
            
            return cycle_adj, conflict
        
        def get_signal_str(adj_size):
            """Determine signal based on adjusted size"""
            if adj_size <= -0.15:
                return "STRONG_SELL"
            elif adj_size <= -0.05:
                return "SELL"
            elif adj_size >= 0.15:
                return "STRONG_BUY"
            elif adj_size >= 0.05:
                return "BUY"
            else:
                return "HOLD"
        
        # Apply cycle modifier
        cycle_btc, btc_conflict = apply_cycle_modifier(adj_btc, bottom_prox, top_prox)
        cycle_eth, eth_conflict = apply_cycle_modifier(adj_eth, bottom_prox, top_prox)
        
        btc_signal = get_signal_str(cycle_btc)
        eth_signal = get_signal_str(cycle_eth)
        
        # Show actual actionable signal with cycle adjustment
        if btc_conflict:
            if btc_conflict in ["BOTTOM", "TOP"]:
                lines.append(f"BTC: {btc_signal} {cycle_btc:+.0%} (⚠️ cycle override)")
            else:
                lines.append(f"BTC: {btc_signal} {cycle_btc:+.0%} (dampened)")
        elif btc_signal == "HOLD" and btc_size != 0:
            lines.append(f"BTC: HOLD (signal weak: {adj_btc:+.0%})")
        elif btc_size != 0:
            lines.append(f"BTC: {btc_signal} {cycle_btc:+.0%}")
        else:
            lines.append("BTC: HOLD")
        
        if eth_conflict:
            if eth_conflict in ["BOTTOM", "TOP"]:
                lines.append(f"ETH: {eth_signal} {cycle_eth:+.0%} (⚠️ cycle override)")
            else:
                lines.append(f"ETH: {eth_signal} {cycle_eth:+.0%} (dampened)")
        elif eth_signal == "HOLD" and eth_size != 0:
            lines.append(f"ETH: HOLD (signal weak: {adj_eth:+.0%})")
        elif eth_size != 0:
            lines.append(f"ETH: {eth_signal} {cycle_eth:+.0%}")
        else:
            lines.append("ETH: HOLD")
    
    lines.append("")
    
    # ═══ REASONS ═══
    if reasons:
        lines.append("Reasons:")
        for r in reasons[:3]:  # Max 3 reasons
            lines.append(f"  • {r}")
    elif bt_signal and CYCLE_ENGINE_AVAILABLE:
        # Generate reason from bt_signal
        if bt_signal == BottomTopSignal.GLOBAL_BOTTOM:
            lines.append("Reasons:")
            lines.append("  • 🟢 ГЛОБАЛЬНОЕ ДНО — редкий сигнал!")
        elif bt_signal == BottomTopSignal.LOCAL_BOTTOM:
            lines.append("Reasons:")
            lines.append("  • 🟢 Локальное дно — возможность для покупки")
        elif bt_signal == BottomTopSignal.GLOBAL_TOP:
            lines.append("Reasons:")
            lines.append("  • 🔴 ГЛОБАЛЬНАЯ ВЕРШИНА — редкий сигнал!")
        elif bt_signal == BottomTopSignal.LOCAL_TOP:
            lines.append("Reasons:")
            lines.append("  • 🔴 Локальная вершина — фиксируем прибыль")
    
    return lines


# ============================================================
# SEND
# ============================================================

def send_telegram(output: dict, lp_policy=None, allocation=None, short=False) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.warning("Telegram credentials not set.")
        return False

    if short:
        text = format_short(output, lp_policy, allocation)
    else:
        text = format_output(output, lp_policy, allocation)

    if len(text) > 4096:
        text = text[:4090] + "\n..."

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"```\n{text}\n```",
        "parse_mode": "Markdown",
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            logger.info("✓ Telegram sent")
            return True
        else:
            logger.error(f"Telegram error: {resp.status_code}")
            return False
    except Exception as e:
        logger.error(f"Telegram failed: {e}")
        return False
