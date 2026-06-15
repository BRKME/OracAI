"""Расчёт нового коридора для LP-позиций, вышедших из range.

Предложение к РУЧНОМУ ребалансу (не автоисполнение). Логика:
  • ширина от волатильности (ATR%): узкий коридор на тихих монетах (макс fees),
    шире на волатильных (меньше шанс снова выпасть);
  • асимметрия при пампе: если монета выросла > PUMP_THRESHOLD_PCT за 24ч,
    центр коридора сдвигается ниже текущей цены — ловим вероятный откат, верх
    не задираем (низкий верх и низкий низ, как просил оператор);
  • без истории цен — честная деградация в симметричный коридор по DEFAULT_*,
    без ложной ТА-точности (свежие токены вроде ASTER).

Все пороги — модульные константы, меняются одним местом.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

PUMP_THRESHOLD_PCT = 15.0     # рост за 24ч выше — включаем асимметрию вниз
ATR_PERIOD = 14
ATR_MULTIPLIER = 4.0          # полуширина коридора ≈ ATR% * этот множитель
MIN_HALF_WIDTH_PCT = 4.0      # не уже этого (агрессивно, но не самоубийственно)
MAX_HALF_WIDTH_PCT = 35.0     # не шире этого
DEFAULT_HALF_WIDTH_PCT = 12.0  # когда истории нет
PUMP_SHIFT_FRACTION = 0.5     # насколько сдвинуть центр вниз при пампе (доля полуширины)

# Фаза цикла даёт МЯГКИЙ наклон центра вниз (ширину не трогает — это ATR).
# Сдвиг — доля полуширины, ограниченная PHASE_SHIFT_MAX_PCT, чтобы LP не стал
# направленной ставкой. Фазы повышенного риска отката вниз: дно (резкие
# проливы) и вершина (откат с любой высоты).
PHASE_SHIFT_FRACTION = 0.25   # доля полуширины на фазовый сдвиг
PHASE_SHIFT_MAX_PCT = 3.0     # жёсткий потолок фазового сдвига, %
PHASE_DOWNSHIFT = {
    "CAPITULATION", "ACCUMULATION", "LATE_BEAR", "EARLY_BEAR", "MID_BEAR",
    "DISTRIBUTION", "EUPHORIA", "LATE_BULL",
}


def atr_pct(history: np.ndarray) -> Optional[float]:
    """Грубый ATR% по ряду цен (|Δ| между соседними как proxy true range).

    История — ряд цен (часовые или дневные). None, если данных мало.
    """
    if history is None or len(history) < ATR_PERIOD + 1:
        return None
    diffs = np.abs(np.diff(history[-(ATR_PERIOD + 1):]))
    atr = float(np.mean(diffs))
    last = float(history[-1])
    if last <= 0:
        return None
    return atr / last * 100.0


def pct_change_24h(history: np.ndarray) -> Optional[float]:
    """Изменение за ~24 точки (часовой ряд), устойчивое к шуму на концах.

    Сравниваются МЕДИАНЫ коротких окон: вокруг точки −24 (центрированное окно,
    чтобы не занижать постепенный дрейф) и на свежем конце. Медиана гасит
    одиночные шумовые выбросы, не съедая настоящее устойчивое движение.
    None, если данных мало.
    """
    if history is None or len(history) < 24:
        return None
    win = 3
    # центрированное окно вокруг точки −24: [-25:-22] при достаточной длине
    if len(history) >= 25:
        past_window = history[-25:-22]
    else:
        past_window = history[-24:-24 + win]
    now_window = history[-win:]
    past = float(np.median(past_window))
    now = float(np.median(now_window))
    if past <= 0:
        return None
    return (now - past) / past * 100.0


def suggest_corridor(price: float, history: Optional[np.ndarray],
                     phase: Optional[str] = None) -> dict:
    """Новый коридор {lower, upper, method, pump_detected, phase_shift_applied,
    half_width_pct, note}.

    price — текущая цена; history — ряд цен (часовой); phase — фаза цикла из
    OracAI (опционально). Ширину задаёт ATR. Центр сдвигается вниз при пампе
    ИЛИ в фазах повышенного риска отката — берётся МАКСИМУМ из двух сдвигов
    (не сумма), чтобы LP не превратился в направленную ставку.
    """
    if price <= 0:
        return {"lower": 0.0, "upper": 0.0, "method": "invalid_price",
                "pump_detected": False, "phase_shift_applied": False,
                "half_width_pct": 0.0, "note": "нет валидной цены"}

    a = atr_pct(history) if history is not None else None
    if a is None:
        hw = DEFAULT_HALF_WIDTH_PCT / 100.0
        return {
            "lower": round(price * (1 - hw), 8),
            "upper": round(price * (1 + hw), 8),
            "method": "default_symmetric",
            "pump_detected": False,
            "phase_shift_applied": False,
            "half_width_pct": DEFAULT_HALF_WIDTH_PCT,
            "note": (f"Нет истории цен — симметричный ±{DEFAULT_HALF_WIDTH_PCT:.0f}% "
                     f"по умолчанию. ТА недоступен."),
        }

    half_width_pct = max(MIN_HALF_WIDTH_PCT,
                         min(MAX_HALF_WIDTH_PCT, a * ATR_MULTIPLIER))
    hw = half_width_pct / 100.0

    chg = pct_change_24h(history)
    pump = chg is not None and chg >= PUMP_THRESHOLD_PCT

    # сдвиг от пампа (доля полуширины)
    pump_shift = hw * PUMP_SHIFT_FRACTION if pump else 0.0
    # сдвиг от фазы (доля полуширины, но не больше жёсткого потолка)
    phase_down = bool(phase) and phase.upper() in PHASE_DOWNSHIFT
    phase_shift = min(hw * PHASE_SHIFT_FRACTION,
                      PHASE_SHIFT_MAX_PCT / 100.0) if phase_down else 0.0

    # МАКСИМУМ из двух, не сумма — берём худший риск, но не удваиваем
    shift = max(pump_shift, phase_shift)
    center = price * (1 - shift)

    if pump and phase_down:
        method = "atr_pump_phase"
        note = (f"Рост +{chg:.0f}% за 24ч + медвежья фаза ({phase}) — центр ниже "
                f"цены (max сдвига, не сумма): низкий верх, запас вниз. "
                f"Полуширина ±{half_width_pct:.0f}% (ATR {a:.1f}%).")
    elif pump:
        method = "atr_pump"
        note = (f"Рост +{chg:.0f}% за 24ч — вероятен откат. Центр ниже цены: "
                f"низкий верх, запас вниз. Полуширина ±{half_width_pct:.0f}% "
                f"(ATR {a:.1f}%).")
    elif phase_down:
        method = "atr_phase"
        note = (f"Фаза {phase} — повышен риск отката вниз. Центр чуть ниже цены "
                f"(мягкий сдвиг). Полуширина ±{half_width_pct:.0f}% (ATR {a:.1f}%"
                + (f", 24ч {chg:+.0f}%" if chg is not None else "") + ").")
    else:
        method = "atr_symmetric"
        note = (f"Симметрично вокруг цены. Полуширина ±{half_width_pct:.0f}% "
                f"(ATR {a:.1f}%" + (f", 24ч {chg:+.0f}%" if chg is not None else "") + ").")

    return {
        "lower": round(center * (1 - hw), 8),
        "upper": round(center * (1 + hw), 8),
        "method": method,
        "pump_detected": bool(pump),
        "phase_shift_applied": bool(phase_down),
        "half_width_pct": round(half_width_pct, 1),
        "note": note,
    }


def format_corridor_suggestion(symbol: str, price: float,
                               corridor: dict) -> str:
    """Одна строка предложения для Telegram-отчёта LP."""
    lo, hi = corridor["lower"], corridor["upper"]
    flag = "📉" if corridor["pump_detected"] else "🎯"
    return (f"{flag} {symbol}: новый коридор ${lo:,.4g}–${hi:,.4g} "
            f"(±{corridor['half_width_pct']:.0f}%)\n   {corridor['note']}")
