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
    """Create visual bar: ░░░░▓▓▓▓▓▓"""
    if value is None:
        value = 0
    filled = int(value * width)
    return "▓" * filled + "░" * (width - filled)


def make_bar_simple(active: bool) -> str:
    """Create simple bar: ░░ or ██"""
    return "██" if active else "░░"


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
    Generate AI-style market analysis based on the template.
    
    Template:
    - Текущий вектор
    - Ключевое противоречие (если есть)
    - Интерпретация низкой уверенности (если применимо)
    - Резюме
    """
    parts = []
    
    # === Текущий вектор ===
    if regime == "BEAR":
        vector = f"Давление вниз ({abs(dir_value):.2f}), фаза BEAR активна."
    elif regime == "BULL":
        vector = f"Давление вверх ({abs(dir_value):.2f}), фаза BULL активна."
    elif regime == "TRANSITION":
        vector = "Переходная фаза, направление не определено."
    else:
        vector = "Боковое движение в диапазоне."
    
    parts.append(f"Текущий вектор: {vector}")
    
    # === Ключевое противоречие ===
    # Check for conflict between top 2 probabilities
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
                f"Высокий процент TRANSITION ({int(prob_trans*100)}%) говорит о том, что нисходящая "
                f"структура (BEAR {int(prob_bear*100)}%) нарушена или исчерпала себя. Рынок пытается "
                f"развернуться или уйти в коррекцию. Однако метрика «Downside pressure» ({abs(dir_value):.2f}) "
                f"говорит о том, что продавцы всё ещё сильны."
            )
            parts.append(f"Ключевое противоречие: {conflict}")
        elif "TRANSITION" in [top1, top2] and "BULL" in [top1, top2]:
            conflict = (
                f"Конфликт BULL ({int(prob_bull*100)}%) и TRANSITION ({int(prob_trans*100)}%) "
                f"указывает на неустойчивость роста. Возможна фиксация прибыли или коррекция."
            )
            parts.append(f"Ключевое противоречие: {conflict}")
    
    if struct_break:
        parts.append("Структура: BREAK — слом рыночной структуры, возможен разворот или ускорение.")
    
    # === Низкая уверенность ===
    if conf_pct < 25:
        conf_text = (
            f"Низкая уверенность модели ({conf_pct}%) означает, что любая из фаз "
            f"(продолжение падения или переход в рост/флэт) может реализоваться "
            f"с равной вероятностью, но движение будет резким."
        )
        parts.append(f"Уверенность: {conf_text}")
    elif conf_pct < 40:
        parts.append(f"Уверенность: Умеренно низкая ({conf_pct}%), повышенная вероятность ложных сигналов.")
    
    # === Резюме ===
    if regime == "BEAR" and prob_trans > 0.35:
        summary = "Рынок в медвежьей фазе, но близок к переходу. Осторожность с шортами."
    elif regime == "BEAR" and conf_pct < 20:
        summary = "Медвежий тренд с низкой уверенностью. Возможны резкие развороты."
    elif regime == "BULL" and conf_pct > 50:
        summary = "Устойчивый бычий тренд. Работаем по тренду."
    elif regime == "TRANSITION":
        summary = "Неопределённость. Ждём подтверждения направления."
    else:
        summary = "Смешанные сигналы. Сниженный размер позиций."
    
    parts.append(f"Резюме: {summary}")
    
    return "\n".join(parts)


# ============================================================
# MAIN FORMAT OUTPUT — v5.0 UI/UX
# ============================================================

def format_output(output: dict, lp_policy=None, allocation=None) -> str:
    """
    New UI/UX format v5.0
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
    
    # Fear & Greed (from allocation meta if available)
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
    
    # RSI
    if rsi_1d is not None or rsi_2h is not None:
        rsi_icon, _ = calculate_rsi_status(rsi_1d)
        _, rsi_2h_dir = calculate_rsi_status(rsi_2h)
        rsi_1d_str = f"{rsi_1d:.0f}" if rsi_1d else "N/A"
        rsi_2h_str = f"{rsi_2h:.0f}{rsi_2h_dir}" if rsi_2h else "N/A"
        lines.append(f"RSI: {rsi_icon} 1D={rsi_1d_str} | 2H={rsi_2h_str}")
    
    # Directional pressure
    if risk_level < 0:
        lines.append(f"Downside pressure. Dir: ↓ {abs(risk_level):.2f}")
    else:
        lines.append(f"Upside pressure. Dir: ↑ {abs(risk_level):.2f}")
    
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # РЕЖИМ РЫНКА (Probabilities)
    # ══════════════════════════════════════════════════════
    
    lines.append("Режим рынка:")
    
    def make_prob_bar(value, width=12):
        filled = int(value * width)
        return "█" * filled + "░" * (width - filled)
    
    lines.append(f"BULL       {make_prob_bar(prob_bull)} {int(prob_bull*100)}%")
    lines.append(f"BEAR       {make_prob_bar(prob_bear)} {int(prob_bear*100)}%")
    lines.append(f"RANGE      {make_prob_bar(prob_range)} {int(prob_range*100)}%")
    lines.append(f"TRANSITION {make_prob_bar(prob_trans)} {int(prob_trans*100)}%")
    
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # ВЫВОД (AI Analysis)
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
    # 2. РИСК
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
    lines.append(f"NORMAL   {make_bar_simple(risk_state == 'NORMAL')}")
    lines.append(f"ELEVATED {make_bar_simple(risk_state == 'ELEVATED')}")
    lines.append(f"TAIL     {make_bar_simple(risk_state == 'TAIL')}")
    lines.append(f"CRISIS   {make_bar_simple(risk_state == 'CRISIS')}")
    lines.append("")
    lines.append(f"→ {RISK_AIRPLANE.get(risk_state, '')}")
    lines.append("")
    
    # Structure
    if struct_break:
        structure = "BREAK"
    elif vol_z > 2.0:
        structure = "EXPANSION"
    elif vol_z < 0.5:
        structure = "COMPRESSION"
    elif regime == "BEAR":
        structure = "BEAR"
    elif regime == "BULL":
        structure = "BULL"
    elif regime == "RANGE":
        structure = "RANGE"
    else:
        structure = "BREAK"
    
    lines.append(f"Структура рынка: {structure}")
    lines.append(f"→ {STRUCTURE_AIRPLANE.get(structure, '')}")
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 3. SPOT SIGNAL
    # ══════════════════════════════════════════════════════
    
    spot_lines = _format_spot_signal_v5(allocation, conf_adj, regime, risk_level, output, rsi_1d, rsi_2h)
    lines.extend(spot_lines)
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 4. LP POLICY
    # ══════════════════════════════════════════════════════
    
    if lp_policy:
        lines.append("🔘 LP:")
        
        # LP recommendation
        if lp_policy.recommendation:
            lines.append(lp_policy.recommendation)
        
        # Exposure
        eff_exp = int(lp_policy.effective_exposure * 100)
        range_str = "wide" if lp_policy.range_width == "wide" else "narrow" if lp_policy.range_width == "narrow" else "medium"
        lines.append(f"Exposure: {eff_exp}% | Range: {range_str}")
        
        # Fees vs IL
        if hasattr(lp_policy, 'fee_to_variance_ratio'):
            fv = lp_policy.fee_to_variance_ratio
            fv_status = "✓" if fv > 1.5 else "⚠️" if fv > 1.0 else "✗"
            lines.append(f"Fees vs IL: {fv:.1f}x {fv_status}")
        
        # Hedge
        hedge_str = "REQUIRED" if lp_policy.hedge_recommended else "optional"
        lines.append(f"Hedge: {hedge_str}")
        
        # Comment
        if lp_policy.comment:
            lines.append(f"→ {lp_policy.comment}")
        
        lines.append("")
    
    # ══════════════════════════════════════════════════════
    # 5. DATA STATUS
    # ══════════════════════════════════════════════════════
    
    data_quality = meta.get("data_completeness", 1.0)
    failed_sources = meta.get("failed_sources", [])
    
    lines.append("📡 DATA STATUS")
    lines.append(f"Качество данных: {int(data_quality*100)}%")
    
    # Detailed breakdown
    if failed_sources:
        lines.append(f"Недоступны: {', '.join(failed_sources)}")
    
    if rsi_1d is None:
        lines.append("⚠️ RSI Daily недоступен")
    
    if data_quality < 0.9:
        lines.append("⚠️ Качество данных ниже 90% — сигналы менее надёжны")
    
    lines.append("")
    
    # ══════════════════════════════════════════════════════
    # FOOTER
    # ══════════════════════════════════════════════════════
    
    footer_parts = ["v5.0"]
    if rsi_source and rsi_source != "none":
        footer_parts.append(f"RSI:{rsi_source}")
    
    lines.append(" · ".join(footer_parts))
    
    return "\n".join(lines)


# ============================================================
# SPOT SIGNAL v5.0
# ============================================================

def _format_spot_signal_v5(allocation: dict, conf_adj: float, regime: str, risk_level: float,
                            output: dict = None, rsi_1d: float = None, rsi_2h: float = None) -> list:
    """Format SPOT SIGNAL section with new UI/UX."""
    lines = []
    
    # Get allocation data
    btc = allocation.get("btc", {}) if allocation else {}
    eth = allocation.get("eth", {}) if allocation else {}
    btc_action = btc.get("action", "HOLD")
    eth_action = eth.get("action", "HOLD")
    btc_size = btc.get("size_pct", 0)
    eth_size = eth.get("size_pct", 0)
    
    # Adjusted size
    adj_btc_size = btc_size * conf_adj
    adj_eth_size = eth_size * conf_adj
    
    # Determine final signal based on adjusted size
    def get_signal(adj_size):
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
    
    signal = get_signal(adj_btc_size)
    
    # === Estimate cycle position from regime ===
    meta = output.get("metadata", {}) if output else {}
    days_in_regime = meta.get("days_in_regime", 0)
    
    # Estimate phase and cycle position
    if regime == "BEAR":
        if days_in_regime > 30 and risk_level < -0.5:
            phase = "CAPITULATION"
            bottom_prox = min(0.9, 0.7 + abs(risk_level) * 0.2)
            top_prox = 0.1
            cycle_pos = 15
        elif days_in_regime > 14:
            phase = "MID_BEAR"
            bottom_prox = min(0.7, 0.5 + abs(risk_level) * 0.2)
            top_prox = 0.2
            cycle_pos = 25
        else:
            phase = "EARLY_BEAR"
            bottom_prox = 0.3
            top_prox = 0.4
            cycle_pos = 35
    elif regime == "BULL":
        if days_in_regime > 30 and risk_level > 0.5:
            phase = "LATE_BULL"
            bottom_prox = 0.1
            top_prox = min(0.9, 0.7 + risk_level * 0.2)
            cycle_pos = 85
        elif days_in_regime > 14:
            phase = "MID_BULL"
            bottom_prox = 0.2
            top_prox = 0.5
            cycle_pos = 65
        else:
            phase = "EARLY_BULL"
            bottom_prox = 0.4
            top_prox = 0.3
            cycle_pos = 45
    elif regime == "TRANSITION":
        if risk_level < -0.3:
            phase = "DISTRIBUTION"
            bottom_prox = 0.3
            top_prox = 0.5
            cycle_pos = 60
        elif risk_level > 0.3:
            phase = "ACCUMULATION"
            bottom_prox = 0.5
            top_prox = 0.3
            cycle_pos = 30
        else:
            phase = "TRANSITION"
            bottom_prox = 0.4
            top_prox = 0.4
            cycle_pos = 50
    else:  # RANGE
        phase = "RANGE"
        bottom_prox = 0.35
        top_prox = 0.35
        cycle_pos = 50
    
    # RSI adjustment to cycle position
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
    
    # === SPOT SIGNAL header ===
    lines.append(f"🔘 SPOT SIGNAL: {signal}")
    lines.append(f"STRONG_SELL {make_bar_simple(signal == 'STRONG_SELL')}")
    lines.append(f"SELL        {make_bar_simple(signal == 'SELL')}")
    lines.append(f"HOLD        {make_bar_simple(signal == 'HOLD')}")
    lines.append(f"BUY         {make_bar_simple(signal == 'BUY')}")
    lines.append(f"STRONG_BUY  {make_bar_simple(signal == 'STRONG_BUY')}")
    lines.append("")
    
    # === Cycle position ===
    lines.append(f"🔘 Цикл рынка: {phase} (conf: {int(conf_adj*100)}%)")
    
    cycle_filled = int(cycle_pos / 10)
    cycle_bar = "█" * cycle_filled + "░" * (10 - cycle_filled)
    lines.append(f"Cycle: [{cycle_bar}] {cycle_pos}/100")
    lines.append("")
    
    # Wyckoff explanation
    phase_explanation = WYCKOFF_PHASES.get(phase, "")
    if phase_explanation:
        lines.append(f"Вывод: {phase_explanation}")
        
        # Additional context based on cycle position
        if cycle_pos < 30:
            lines.append(f"Cycle ({cycle_pos}%) — киты ещё не накупились. Рост может быть не сразу.")
        elif cycle_pos > 70:
            lines.append(f"Cycle ({cycle_pos}%) — поздняя фаза, осторожность.")
    
    lines.append("")
    
    # === Bottom/Top signal ===
    lines.append("🔘 Сигнал Дно-Вершина:")
    lines.append(f"Bottom {make_bar(bottom_prox)} {int(bottom_prox*100)}%")
    lines.append(f"Top    {make_bar(top_prox)} {int(top_prox*100)}%")
    lines.append("")
    
    # BTC/ETH actions
    btc_display = btc_action
    eth_display = eth_action
    
    # Check if signal was dampened
    if abs(adj_btc_size) < 0.05 and abs(btc_size) > 0.05:
        btc_display = f"HOLD (signal weak: {int(adj_btc_size*100)}%)"
    if abs(adj_eth_size) < 0.05 and abs(eth_size) > 0.05:
        eth_display = f"HOLD (signal weak: {int(adj_eth_size*100)}%)"
    
    lines.append(f"BTC: {btc_display}")
    lines.append(f"ETH: {eth_display}")
    lines.append("")
    
    # Bottom/Top analysis
    if bottom_prox >= 0.7:
        lines.append("Вывод: 🟢 Высокая вероятность дна — покупка выгоднее продажи.")
    elif bottom_prox >= 0.5:
        lines.append("Вывод: Умеренная вероятность дна — осторожность с продажами.")
    elif top_prox >= 0.7:
        lines.append("Вывод: 🔴 Высокая вероятность вершины — фиксируем прибыль.")
    elif top_prox >= 0.5:
        lines.append("Вывод: Умеренная вероятность вершины — осторожность с покупками.")
    else:
        lines.append("Вывод: Нейтральная зона — следуем сигналу модели.")
    
    return lines


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
