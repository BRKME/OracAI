"""
Telegram Bot v5.0 — New UI/UX
Cleaner visual design with AI-powered analysis.
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
# CONSTANTS — AIRPLANE ANALOGIES
# ============================================================

RISK_AIRPLANE = {
    "NORMAL": "Самолет летит ровно, стюардессы разносят еду.",
    "ELEVATED": "Загорелась табличка «Пристегните ремни». Турбулентность впереди.",
    "TAIL": "Отключился автопилот, погас свет. Зона невидимой турбулентности.",
    "CRISIS": "Самолет срывается в штопор, падают маски. Связь с землей потеряна."
}

STRUCTURE_AIRPLANE = {
    "BULL": "Самолет стабильно набирает высоту, новые максимумы выше предыдущих.",
    "BEAR": "Самолет снижается, новые минимумы ниже предыдущих.",
    "RANGE": "Самолет держит эшелон, болтанка в коридоре.",
    "BREAK": "Самолет пробил облачность вверх или вниз.",
    "EXPANSION": "Резкий порыв ветра, самолет швыряет.",
    "COMPRESSION": "Затишье перед бурей, воздух плотный.",
    "CHAOS": "Все датчики сошли с ума, пилот ничего не понимает."
}

WYCKOFF_PHASES = {
    "ACCUMULATION": "Умные деньги (киты) скупают актив у толпы после падения. Цена стоит на месте или слегка растёт.",
    "MARKUP": "Начался рост, толпа подключается, киты уже в лонгах.",
    "DISTRIBUTION": "Киты продают актив толпе на хаях.",
    "MARKDOWN": "Началось падение, киты уже в шортах или в кэше.",
    "EARLY_BEAR": "Начало нисходящего тренда. Первые продажи.",
    "MID_BEAR": "Середина медвежьего рынка. Давление продавцов.",
    "CAPITULATION": "Капитуляция. Массовые продажи, возможно близко дно.",
    "EARLY_BULL": "Начало восходящего тренда. Первые покупки.",
    "MID_BULL": "Середина бычьего рынка. Рост продолжается.",
    "LATE_BULL": "Поздний бычий рынок. Эйфория, возможно близко вершина.",
    "TRANSITION": "Переходная фаза. Неопределённость направления.",
    "RANGE": "Флэт. Цена в боковике без явного направления."
}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def make_bar(value: float, width: int = 10) -> str:
    """Create visual bar with ASCII: [####......]"""
    if value is None:
        value = 0
    filled = int(value * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def make_bar_simple(active: bool) -> str:
    """Create simple bar: [#] or [.]"""
    return "[#]" if active else "[.]"


def calculate_rsi_status(rsi: float) -> tuple:
    """Return (icon, direction) for RSI."""
    if rsi is None:
        return "⚪", "→"
    if rsi <= 30:
        return "🟢", "↓"
    elif rsi >= 70:
        return "🔴", "↑"
    elif rsi < 40:
        return "⚪", "↓"
    elif rsi > 60:
        return "⚪", "↑"
    else:
        return "⚪", "→"


# ============================================================
# AI ANALYSIS GENERATOR
# ============================================================

def generate_market_analysis(
    regime: str,
    prob_bear: float,
    prob_trans: float,
    prob_bull: float,
    prob_range: float,
    conf_pct: int,
    dir_value: float,
    struct_break: bool,
    vol_z: float
) -> str:
    """
    Generate AI-style market analysis.
    Simplified - no duplication between summary and details.
    """
    parts = []
    
    # === Generate Summary FIRST ===
    if regime == "BEAR" and prob_trans > 0.35:
        summary = "Рынок в медвежьей фазе, но близок к переходу. Осторожность с шортами."
    elif regime == "BEAR" and conf_pct < 20:
        summary = "Медвежий тренд с низкой уверенностью. Возможны резкие развороты."
    elif regime == "BEAR":
        summary = f"Медвежий тренд. Давление вниз ({abs(dir_value):.2f})."
    elif regime == "BULL" and conf_pct > 50:
        summary = "Устойчивый бычий тренд. Работаем по тренду."
    elif regime == "BULL" and conf_pct < 30:
        summary = "Бычий тренд с низкой уверенностью. Возможны коррекции."
    elif regime == "BULL":
        summary = f"Бычий тренд. Давление вверх ({abs(dir_value):.2f})."
    elif regime == "TRANSITION":
        summary = "Переходная фаза. Ждём подтверждения направления."
    elif regime == "RANGE":
        summary = "Боковик. Торгуем от границ диапазона."
    else:
        summary = "Смешанные сигналы. Сниженный размер позиций."
    
    parts.append(summary)
    
    # === Ключевое противоречие (only if exists) ===
    sorted_probs = sorted([
        ("BEAR", prob_bear),
        ("TRANSITION", prob_trans),
        ("BULL", prob_bull),
        ("RANGE", prob_range)
    ], key=lambda x: x[1], reverse=True)
    
    top1, top1_prob = sorted_probs[0]
    top2, top2_prob = sorted_probs[1]
    
    if abs(top1_prob - top2_prob) < 0.15 and top1_prob > 0.3 and top2_prob > 0.3:
        if "TRANSITION" in [top1, top2] and "BEAR" in [top1, top2]:
            conflict = (
                f"Конфликт: TRANSITION ({int(prob_trans*100)}%) vs BEAR ({int(prob_bear*100)}%) — "
                f"структура нарушена, но продавцы ещё сильны."
            )
            parts.append(conflict)
        elif "TRANSITION" in [top1, top2] and "BULL" in [top1, top2]:
            conflict = (
                f"Конфликт: BULL ({int(prob_bull*100)}%) vs TRANSITION ({int(prob_trans*100)}%) — "
                f"рост неустойчив, возможна коррекция."
            )
            parts.append(conflict)
    
    if struct_break:
        parts.append("Структура: BREAK — слом, возможен разворот или ускорение.")
    
    # === Низкая уверенность ===
    if conf_pct < 25:
        parts.append(f"Уверенность: {conf_pct}% — высокая вероятность резких движений.")
    elif conf_pct < 40:
        parts.append(f"Уверенность: {conf_pct}% — повышенный риск ложных сигналов.")
    
    return "\n".join(parts)


# ============================================================
# MAIN FORMAT OUTPUT — v5.1 UI/UX
# ============================================================

def format_output(output: dict, lp_policy=None, allocation=None) -> str:
    """
    New UI/UX format v5.1
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
    
    # RSI data
    rsi_data = meta.get("rsi", {})
    rsi_1d = rsi_data.get("rsi_1d")
    rsi_2h = rsi_data.get("rsi_2h")
    rsi_source = rsi_data.get("source", "none")
    
    # v5.9: Drawdown from 90-day high (for HODL defender)
    dd_from_high = meta.get("drawdown_from_high_90d", 0.0)
    
    # Fear & Greed — get from engine output (bucket_details), not allocation
    fg_value = None
    fg_class = None
    bucket_details = output.get("bucket_details", {})
    sent_details = bucket_details.get("sentiment", {})
    fg_raw = sent_details.get("fg_raw")
    if fg_raw is not None:
        fg_value = int(fg_raw)
        if fg_value < 25: fg_class = "Extreme Fear"
        elif fg_value < 45: fg_class = "Fear"
        elif fg_value < 55: fg_class = "Neutral"
        elif fg_value < 75: fg_class = "Greed"
        else: fg_class = "Extreme Greed"
    # Fallback to allocation if available
    if fg_value is None and allocation:
        fg_value = allocation.get("meta", {}).get("fear_greed")
        fg_class = allocation.get("meta", {}).get("fg_classification")
    
    # Tail risk
    tail_active = False
    if allocation:
        tail_active = allocation.get("meta", {}).get("tail_risk_active", False)
    
    conf_pct = int(conf_adj * 100)
    
    # Probabilities
    prob_bull = probs.get("BULL", 0)
    prob_bear = probs.get("BEAR", 0)
    prob_range = probs.get("RANGE", 0)
    prob_trans = probs.get("TRANSITION", 0)
    
    # Get allocation signals
    btc = allocation.get("btc", {}) if allocation else {}
    eth = allocation.get("eth", {}) if allocation else {}
    btc_action = btc.get("action", "HOLD")
    eth_action = eth.get("action", "HOLD")
    btc_size = btc.get("size_pct", 0)
    eth_size = eth.get("size_pct", 0)
    adj_btc_size = btc_size * conf_adj
    adj_eth_size = eth_size * conf_adj
    
    lines = []
    
    # ══════════════════════════════════════════════════════
    # 1. ФАЗА РЫНКА
    # ══════════════════════════════════════════════════════
    
    lines.append("🔘 Фаза рынка:")
    lines.append("")
    
    # Main info line
    regime_line = f"{regime} ({days}d) | Conf. {conf_pct}%"
    lines.append(regime_line)
    
    # RSI (no emoji)
    if rsi_1d is not None or rsi_2h is not None:
        _, rsi_2h_dir = calculate_rsi_status(rsi_2h)
        rsi_1d_str = f"{rsi_1d:.0f}" if rsi_1d else "N/A"
        rsi_2h_str = f"{rsi_2h:.0f}{rsi_2h_dir}" if rsi_2h else "N/A"
        lines.append(f"RSI: 1D={rsi_1d_str} | 2H={rsi_2h_str}")
    
    # Fear & Greed (after RSI)
    if fg_value is not None:
        fg_label = fg_class or "?"
        lines.append(f"FG: {fg_value} ({fg_label})")
    
    # Directional pressure + structure (compact)
    dir_arrow = "↓" if risk_level < 0 else "↑"
    dir_line = f"Dir: {dir_arrow} {abs(risk_level):.2f}"
    if struct_break:
        dir_line += " | Структура: BREAK"
    lines.append(dir_line)
    
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # РЕЖИМ РЫНКА (Probabilities) - aligned bars
    # ══════════════════════════════════════════════════════
    
    lines.append("Режим рынка:")
    
    def make_prob_bar(value, width=10):
        filled = int(value * width)
        return "[" + "#" * filled + "." * (width - filled) + "]"
    
    # Compact format without alignment dependency
    lines.append(f"BULL  {make_prob_bar(prob_bull)} {int(prob_bull*100)}%")
    lines.append(f"BEAR  {make_prob_bar(prob_bear)} {int(prob_bear*100)}%")
    lines.append(f"RANGE {make_prob_bar(prob_range)} {int(prob_range*100)}%")
    lines.append(f"TRANS {make_prob_bar(prob_trans)} {int(prob_trans*100)}%")
    
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # ЦИКЛ РЫНКА (moved here, before Вывод)
    # ══════════════════════════════════════════════════════
    
    days_in_regime = meta.get("days_in_regime", 0)
    
    if regime == "BEAR":
        if days_in_regime > 30 and risk_level < -0.5:
            phase = "CAPITULATION"
            cycle_pos = 15
        elif days_in_regime > 14:
            phase = "MID_BEAR"
            cycle_pos = 25
        else:
            phase = "EARLY_BEAR"
            cycle_pos = 35
    elif regime == "BULL":
        if days_in_regime > 30 and risk_level > 0.5:
            phase = "LATE_BULL"
            cycle_pos = 85
        elif days_in_regime > 14:
            phase = "MID_BULL"
            cycle_pos = 65
        else:
            phase = "EARLY_BULL"
            cycle_pos = 45
    elif regime == "TRANSITION":
        if risk_level < -0.3:
            phase = "DISTRIBUTION"
            cycle_pos = 60
        elif risk_level > 0.3:
            phase = "ACCUMULATION"
            cycle_pos = 30
        else:
            phase = "TRANSITION"
            cycle_pos = 50
    else:
        phase = "RANGE"
        cycle_pos = 50
    
    cycle_filled = int(cycle_pos / 10)
    cycle_bar = "#" * cycle_filled + "." * (10 - cycle_filled)
    
    lines.append(f"Цикл: {phase} [{cycle_bar}] {cycle_pos}%")
    
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 2. РИСК (without structure duplicate)
    # ══════════════════════════════════════════════════════
    
    # Determine risk state
    if vol_z > 2.5 or (tail_active and vol_z > 2.0):
        risk_state = "CRISIS"
    elif tail_active:
        risk_state = "TAIL"
    elif vol_z > 1.5 or struct_break:
        risk_state = "ELEVATED"
    else:
        risk_state = "NORMAL"
    
    lines.append("🔘 Риск:")
    lines.append(f"NORM {make_bar_simple(risk_state == 'NORMAL')}")
    lines.append(f"ELEV {make_bar_simple(risk_state == 'ELEVATED')}")
    lines.append(f"TAIL {make_bar_simple(risk_state == 'TAIL')}")
    lines.append(f"CRIS {make_bar_simple(risk_state == 'CRISIS')}")
    lines.append("")
    lines.append(f"→ {RISK_AIRPLANE.get(risk_state, '')}")
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 4. BOTTOM/TOP PROXIMITY (continuous calculation)
    # ══════════════════════════════════════════════════════
    
    # Start from regime probabilities as base
    # Higher bear probability → closer to bottom, higher bull → closer to top
    bottom_prox = prob_bear * 0.4 + prob_trans * 0.2 + prob_range * 0.15
    top_prox = prob_bull * 0.4 + prob_trans * 0.2 + prob_range * 0.15
    
    # Directional pressure shifts bottom/top (continuous, always active)
    # risk_level: negative = downside, positive = upside
    if risk_level < 0:
        bottom_prox += abs(risk_level) * 0.25
        top_prox -= abs(risk_level) * 0.15
    else:
        top_prox += risk_level * 0.25
        bottom_prox -= risk_level * 0.15
    
    # Days in regime: longer trend → stronger signal
    days_in_regime = meta.get("days_in_regime", 0)
    day_factor = min(days_in_regime / 30.0, 1.0)  # 0..1 over 30 days
    if regime == "BEAR":
        bottom_prox += day_factor * 0.2
        top_prox -= day_factor * 0.1
    elif regime == "BULL":
        top_prox += day_factor * 0.2
        bottom_prox -= day_factor * 0.1
    
    # Confidence: low confidence pulls both toward center
    if conf_pct < 30:
        center_pull = (30 - conf_pct) / 100.0  # up to 0.3
        bottom_prox = bottom_prox * (1 - center_pull) + 0.35 * center_pull
        top_prox = top_prox * (1 - center_pull) + 0.35 * center_pull
    
    # RSI: continuous adjustment (not just extremes)
    if rsi_1d is not None:
        if rsi_1d < 50:
            # Below 50 → bottom signal (stronger as RSI drops)
            rsi_factor = (50 - rsi_1d) / 50.0  # 0..1
            bottom_prox += rsi_factor * 0.3
            top_prox -= rsi_factor * 0.15
        else:
            # Above 50 → top signal (stronger as RSI rises)
            rsi_factor = (rsi_1d - 50) / 50.0  # 0..1
            top_prox += rsi_factor * 0.3
            bottom_prox -= rsi_factor * 0.15
    
    # Fear & Greed: extreme fear → bottom, extreme greed → top
    if fg_value is not None:
        if fg_value < 50:
            fg_factor = (50 - fg_value) / 50.0  # 0..1
            bottom_prox += fg_factor * 0.15
            top_prox -= fg_factor * 0.05
        else:
            fg_factor = (fg_value - 50) / 50.0  # 0..1
            top_prox += fg_factor * 0.15
            bottom_prox -= fg_factor * 0.05
    
    # Clamp to valid range
    bottom_prox = max(0.05, min(0.95, bottom_prox))
    top_prox = max(0.05, min(0.95, top_prox))
    
    # ══════════════════════════════════════════════════════
    # 5. ДЕЙСТВИЕ — HODL-bias + Drawdown defender (v5.9)
    # Backtest validated: +5.3% alpha vs HODL over 4.2 years
    # ══════════════════════════════════════════════════════
    
    # Compute target position size (0.20..1.00)
    # Default: 90% — BTC long-term uptrend, stay invested
    target_pos = 0.90
    
    # Strong top signals — only reduce on combined extreme indicators
    rsi_for_check = rsi_1d if rsi_1d is not None else 50
    if rsi_for_check > 78 and top_prox > 0.70 and conf_pct > 20:
        target_pos = 0.50
    elif rsi_for_check > 82 and top_prox > 0.75:
        target_pos = 0.40
    
    # Confident bear regime
    if regime == "BEAR" and conf_pct > 30 and rsi_for_check > 40:
        target_pos = min(target_pos, 0.60)
    
    # Strong bottom — go max long
    if bottom_prox > 0.65 or rsi_for_check < 28:
        target_pos = 1.00
    
    # Risk overrides
    if risk_state == "CRISIS":
        target_pos = 0.20
    elif risk_state == "TAIL" and rsi_for_check > 50:
        target_pos = min(target_pos, 0.55)
    
    # ───────────────────────────────────────────────────────────────────
    # Drawdown defender (v5.9) — GATED by regime confirmation
    # Research finding (research_real_prod.py, research_conflict.py):
    #   - DD alone is a lagging signal. Cutting position because price already
    #     fell 18% is a classic "sell the bottom" pattern.
    #   - On 200d prod data, dd=none beats dd=current (backtest_honest.py)
    #   - Only activate DD when bear-regime signals CONFIRM: BEAR regime OR
    #     (RSI below 50 AND directional pressure negative).
    # ───────────────────────────────────────────────────────────────────
    strong_bottom = (rsi_for_check < 30) or (bottom_prox > 0.70)
    bear_confirmation = (
        regime == "BEAR"
        or (rsi_for_check < 50 and risk_level < -0.2)
        or (fg_value is not None and fg_value > 65 and dd_from_high < -15)  # Greed in drawdown = trap
    )
    if not strong_bottom and bear_confirmation:
        if dd_from_high < -25:
            target_pos = min(target_pos, 0.30)
        elif dd_from_high < -15:
            target_pos = min(target_pos, 0.60)
    
    # ───────────────────────────────────────────────────────────────────
    # Conflict detection (informational only — does NOT change target)
    # Research: BEAR + Greed gives -4.3% fwd7d vs -2.7% for clean BEAR,
    # so when regime and sentiment disagree, the regime signal is stronger.
    # We surface this to the user so they understand the signal composition.
    # ───────────────────────────────────────────────────────────────────
    conflict_note = None
    if regime == "BULL" and fg_value is not None and fg_value < 30 and conf_pct > 40:
        conflict_note = "⚠️ Конфликт: BULL режим при Fear. Исторически это продолжение тренда, но подтверждения нет."
    elif regime == "BEAR" and fg_value is not None and fg_value > 70 and conf_pct > 40:
        conflict_note = "⚠️ Конфликт: BEAR режим при Greed. Исторически усиливает медвежий сценарий."
    
    # Snap to 5% steps
    target_pos = round(target_pos * 20) / 20
    target_pct = int(target_pos * 100)
    
    # Map target position to action label
    if risk_state == "CRISIS":
        action = "⚫ ЗАЩИТА"
        action_note = f"Кризисный режим. Целевая позиция: {target_pct}%."
    elif target_pos >= 0.95:
        action = "🟢 ПОКУПАТЬ"
        action_note = f"Сильный сигнал дна. Целевая позиция: {target_pct}%."
    elif target_pos >= 0.85:
        action = "⚪ ДЕРЖАТЬ"
        action_note = f"Базовая HODL-позиция: {target_pct}%. Долгосрочный аптренд BTC."
    elif target_pos >= 0.55:
        action = "🟠 ФИКСИРОВАТЬ"
        if dd_from_high < -15 and bear_confirmation:
            action_note = f"Просадка {abs(dd_from_high):.0f}% + медвежье подтверждение. Целевая позиция: {target_pct}%."
        else:
            action_note = f"Сигнал вершины или TAIL риск. Целевая позиция: {target_pct}%."
    else:
        action = "🔴 ПРОДАВАТЬ"
        if dd_from_high < -25 and bear_confirmation:
            action_note = f"Глубокая просадка {abs(dd_from_high):.0f}% + подтверждение BEAR. Целевая позиция: {target_pct}%."
        else:
            action_note = f"Экстремальная вершина. Целевая позиция: {target_pct}%."
    
    lines.append(f"🔘 Действие: {action}")
    lines.append(f"→ {action_note}")
    if conflict_note:
        lines.append(f"→ {conflict_note}")
    lines.append(f"Bottom {make_bar(bottom_prox)} {int(bottom_prox*100):2d}%")
    lines.append(f"Top    {make_bar(top_prox)} {int(top_prox*100):2d}%")
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 7. LP POLICY
    # ══════════════════════════════════════════════════════
    
    if lp_policy:
        lines.append("🔘 LP:")
        
        # Range + Fees vs IL
        range_str = lp_policy.range_width if hasattr(lp_policy, 'range_width') else "medium"
        range_line = f"Range: {range_str}"
        if hasattr(lp_policy, 'fee_variance_ratio'):
            fv = lp_policy.fee_variance_ratio
            fv_status = "✓" if fv > 1.5 else "⚠️" if fv > 1.0 else "✗"
            range_line += f" | Fees vs IL: {fv:.1f}x {fv_status}"
        lines.append(range_line)
        
        # Hedge
        hedge_required = (
            lp_policy.hedge_recommended or 
            risk_state in ("TAIL", "CRISIS") or
            (risk_state == "ELEVATED" and conf_pct < 30)
        )
        hedge_str = "REQUIRED" if hedge_required else "recommended" if risk_state == "ELEVATED" else "optional"
        lines.append(f"Hedge: {hedge_str}")
        
        lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 7. DATA STATUS
    # ══════════════════════════════════════════════════════
    
    data_quality = meta.get("data_completeness", 1.0)
    failed_sources = meta.get("failed_sources", [])
    
    lines.append("📡 DATA STATUS v5.7 OracAi")
    lines.append(f"Качество данных: {int(data_quality*100)}%")
    
    if failed_sources:
        lines.append(f"Недоступны: {', '.join(failed_sources)}")
    
    if rsi_1d is None:
        lines.append("⚠️ RSI Daily недоступен")
    
    if data_quality < 0.9:
        lines.append("⚠️ Качество данных ниже 90% — сигналы менее надёжны")
    
    return "\n".join(lines)


def _translate_lp_signal(signal: str) -> str:
    """Translate LP signals to Russian."""
    translations = {
        "High uncertainty → fee opportunity": "Высокая неопределённость → возможность заработать на комиссиях",
        "Low vol → collect fees safely": "Низкая волатильность → безопасно собираем комиссии",
        "Trending market → hedge required": "Трендовый рынок → хедж обязателен",
        "High vol → reduce exposure": "Высокая волатильность → снижаем экспозицию",
        "Directional risk high": "Высокий направленный риск",
        "Volatility expansion": "Рост волатильности",
        "Structure break": "Слом структуры",
    }
    
    for en, ru in translations.items():
        if en.lower() in signal.lower():
            return ru
    
    return signal  # Return original if no translation found


# ============================================================
# SPOT SIGNAL v5.0
# ============================================================

# ============================================================
# SHORT FORMAT
# ============================================================

def format_short(output: dict, lp_policy=None, allocation=None) -> str:
    """Short format for quick overview."""
    meta = output.get("metadata", {})
    risk = output.get("risk", {})
    regime = output.get("regime", "?")
    
    btc_price = meta.get("btc_price", 0)
    risk_level = risk.get("risk_level", 0)
    
    lines = []
    lines.append(f"{regime} | BTC ${btc_price:,.0f}")
    lines.append(f"Dir: {risk_level:+.2f}")
    
    if allocation:
        btc = allocation.get("btc", {})
        lines.append(f"Signal: {btc.get('action', 'HOLD')}")
    
    return "\n".join(lines)


# ============================================================
# SEND
# ============================================================

def send_telegram(output: dict, lp_policy=None, allocation=None, short=False) -> bool:
    """Send message to Telegram."""
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


def send_telegram_with_chart(output: dict, lp_policy=None, allocation=None, short=False) -> bool:
    """Send charts (BTC + ETH) + message to Telegram."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.warning("Telegram credentials not set.")
        return False

    # 1. Generate and send charts (BTC + ETH)
    try:
        from chart_generator import generate_chart
        
        for symbol in ["BTC-USD", "ETH-USD"]:
            chart_buf = generate_chart(symbol, 365)
            
            if chart_buf:
                url_photo = f"https://api.telegram.org/bot{token}/sendPhoto"
                files = {'photo': (f'{symbol.lower()}_chart.png', chart_buf, 'image/png')}
                data = {'chat_id': chat_id}
                
                resp = requests.post(url_photo, files=files, data=data, timeout=30)
                if resp.status_code == 200:
                    logger.info(f"✓ {symbol} chart sent")
                else:
                    logger.warning(f"{symbol} chart failed: {resp.status_code}")
            else:
                logger.warning(f"Failed to generate {symbol} chart")
                
    except Exception as e:
        logger.warning(f"Chart error (continuing): {e}")

    # 2. Send text message
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
