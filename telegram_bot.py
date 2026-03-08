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
    
    # Fear & Greed
    fg_value = None
    fg_class = None
    if allocation:
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
    
    # Fear & Greed
    if fg_value is not None:
        fg_label = fg_class or ("Extreme Fear" if fg_value < 25 else "Fear" if fg_value < 45 else "Neutral" if fg_value < 55 else "Greed" if fg_value < 75 else "Extreme Greed")
        lines.append(f"{fg_label} ({fg_value})")
    
    # RSI (no emoji)
    if rsi_1d is not None or rsi_2h is not None:
        _, rsi_2h_dir = calculate_rsi_status(rsi_2h)
        rsi_1d_str = f"{rsi_1d:.0f}" if rsi_1d else "N/A"
        rsi_2h_str = f"{rsi_2h:.0f}{rsi_2h_dir}" if rsi_2h else "N/A"
        lines.append(f"RSI: 1D={rsi_1d_str} | 2H={rsi_2h_str}")
    
    # Directional pressure
    if risk_level < 0:
        lines.append(f"Downside pressure. Dir: ↓ {abs(risk_level):.2f}")
    else:
        lines.append(f"Upside pressure. Dir: ↑ {abs(risk_level):.2f}")
    
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
    
    # Wyckoff explanation
    phase_explanation = WYCKOFF_PHASES.get(phase, "")
    if phase_explanation:
        lines.append(f"→ {phase_explanation}")
    
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # ВЫВОД (AI Analysis) - Simplified, no duplication
    # ══════════════════════════════════════════════════════
    
    analysis = generate_market_analysis(
        regime=regime,
        prob_bear=prob_bear,
        prob_trans=prob_trans,
        prob_bull=prob_bull,
        prob_range=prob_range,
        conf_pct=conf_pct,
        dir_value=risk_level,
        struct_break=struct_break,
        vol_z=vol_z
    )
    
    lines.append("Вывод:")
    for line in analysis.split("\n"):
        lines.append(line)
    
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
    # 3. BOTTOM/TOP PROXIMITY (must be before signals)
    # ══════════════════════════════════════════════════════
    
    # Calculate bottom/top proximity
    if regime == "BEAR":
        if days_in_regime > 30 and risk_level < -0.5:
            bottom_prox = min(0.9, 0.7 + abs(risk_level) * 0.2)
            top_prox = 0.1
        elif days_in_regime > 14:
            bottom_prox = min(0.7, 0.5 + abs(risk_level) * 0.2)
            top_prox = 0.2
        else:
            bottom_prox = 0.3
            top_prox = 0.4
    elif regime == "BULL":
        if days_in_regime > 30 and risk_level > 0.5:
            bottom_prox = 0.1
            top_prox = min(0.9, 0.7 + risk_level * 0.2)
        elif days_in_regime > 14:
            bottom_prox = 0.2
            top_prox = 0.5
        else:
            bottom_prox = 0.4
            top_prox = 0.3
    elif regime == "TRANSITION":
        if risk_level < -0.3:
            bottom_prox = 0.3
            top_prox = 0.5
        elif risk_level > 0.3:
            bottom_prox = 0.5
            top_prox = 0.3
        else:
            bottom_prox = 0.4
            top_prox = 0.4
    else:
        bottom_prox = 0.35
        top_prox = 0.35
    
    # RSI adjustment for bottom/top
    if rsi_1d is not None:
        if rsi_1d <= 25:
            bottom_prox = min(0.95, bottom_prox + 0.25)
            top_prox = max(0.05, top_prox - 0.2)
        elif rsi_1d <= 35:
            bottom_prox = min(0.85, bottom_prox + 0.15)
            top_prox = max(0.1, top_prox - 0.1)
        elif rsi_1d >= 75:
            top_prox = min(0.95, top_prox + 0.25)
            bottom_prox = max(0.05, bottom_prox - 0.2)
        elif rsi_1d >= 65:
            top_prox = min(0.85, top_prox + 0.15)
            bottom_prox = max(0.1, bottom_prox - 0.1)
    
    # ══════════════════════════════════════════════════════
    # 4. ДЕЙСТВИЕ (based on Bottom/Top - backtest proven: 65%/74%)
    # ══════════════════════════════════════════════════════
    
    # Logic: buy at bottom, sell at top, in portions
    if bottom_prox >= 0.7:
        action = "ПОКУПАТЬ"
        action_note = f"Дно {int(bottom_prox*100)}%. Докупать частями (25-50%)."
    elif bottom_prox >= 0.5 and top_prox < 0.4:
        action = "ДОКУПИТЬ"
        action_note = f"Дно {int(bottom_prox*100)}%. Можно добавить 10-20%."
    elif top_prox >= 0.7:
        action = "ПРОДАВАТЬ"
        action_note = f"Вершина {int(top_prox*100)}%. Фиксировать частями (25-50%)."
    elif top_prox >= 0.5 and bottom_prox < 0.4:
        action = "ФИКСИРОВАТЬ"
        action_note = f"Вершина {int(top_prox*100)}%. Можно продать 10-20%."
    elif risk_state == "CRISIS":
        action = "ЗАЩИТА"
        action_note = "CRISIS. Сократить до 20-30%."
    else:
        action = "ДЕРЖАТЬ"
        action_note = "Нет сигнала. Ничего не делать."
    
    lines.append(f"🔘 Действие: {action}")
    lines.append(f"→ {action_note}")
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 5. СИГНАЛ ДНО-ВЕРШИНА (display only, calculation done above)
    # ══════════════════════════════════════════════════════
    
    lines.append("🔘 Сигнал Дно-Вершина:")
    lines.append(f"Bottom {make_bar(bottom_prox)} {int(bottom_prox*100):2d}%")
    lines.append(f"Top    {make_bar(top_prox)} {int(top_prox*100):2d}%")
    lines.append("")
    
    # Smart conclusion based on bottom/top
    if top_prox > bottom_prox + 0.15:
        lines.append("Вывод: Рынок с большей вероятностью перегрет и может упасть, чем недооценён и готов расти.")
    elif bottom_prox > top_prox + 0.15:
        lines.append("Вывод: Рынок с большей вероятностью недооценён и может вырасти, чем перегрет и готов падать.")
    elif bottom_prox >= 0.6:
        lines.append("Вывод: Высокая вероятность дна — покупка выгоднее продажи.")
    elif top_prox >= 0.6:
        lines.append("Вывод: Высокая вероятность вершины — фиксируем прибыль.")
    else:
        lines.append("Вывод: Нейтральная зона — следуем сигналу модели.")
    
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 6. LP POLICY
    # ══════════════════════════════════════════════════════
    
    if lp_policy:
        lines.append("🔘 LP:")
        
        # LP regime and quadrant
        lp_regime_str = str(lp_policy.lp_regime.value) if hasattr(lp_policy.lp_regime, 'value') else str(lp_policy.lp_regime)
        quadrant_str = str(lp_policy.risk_quadrant.value) if hasattr(lp_policy.risk_quadrant, 'value') else str(lp_policy.risk_quadrant)
        lines.append(f"{lp_regime_str} ({quadrant_str})")
        
        # Exposure - only max
        max_exp = int(lp_policy.max_exposure * 100)
        range_str = lp_policy.range_width if hasattr(lp_policy, 'range_width') else "medium"
        lines.append(f"Exposure: {max_exp}% | Range: {range_str}")
        
        # Fees vs IL
        if hasattr(lp_policy, 'fee_variance_ratio'):
            fv = lp_policy.fee_variance_ratio
            fv_status = "✓" if fv > 1.5 else "⚠️" if fv > 1.0 else "✗"
            lines.append(f"Fees vs IL: {fv:.1f}x {fv_status}")
        
        # Hedge - REQUIRED when TAIL or CRISIS risk
        hedge_required = lp_policy.hedge_recommended or risk_state in ("TAIL", "CRISIS")
        hedge_str = "REQUIRED" if hedge_required else "optional"
        lines.append(f"Hedge: {hedge_str}")
        
        # Signals as comment (in Russian)
        if hasattr(lp_policy, 'signals') and lp_policy.signals:
            signal_ru = _translate_lp_signal(lp_policy.signals[0])
            lines.append(f"→ {signal_ru}")
        
        lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 7. DATA STATUS
    # ══════════════════════════════════════════════════════
    
    data_quality = meta.get("data_completeness", 1.0)
    failed_sources = meta.get("failed_sources", [])
    
    lines.append("📡 DATA STATUS v5.5 OracAi")
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
