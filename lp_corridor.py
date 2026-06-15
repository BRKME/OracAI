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
    """Изменение за последние ~24 точки (часовой ряд). None, если мало данных."""
    if history is None or len(history) < 24:
        return None
    past = float(history[-24])
    now = float(history[-1])
    if past <= 0:
        return None
    return (now - past) / past * 100.0


def suggest_corridor(price: float, history: Optional[np.ndarray]) -> dict:
    """Новый коридор {lower, upper, method, pump_detected, half_width_pct, note}.

    price — текущая цена токена; history — ряд цен (часовой, для ATR и 24ч).
    """
    if price <= 0:
        return {"lower": 0.0, "upper": 0.0, "method": "invalid_price",
                "pump_detected": False, "half_width_pct": 0.0,
                "note": "нет валидной цены"}

    a = atr_pct(history) if history is not None else None
    if a is None:
        # нет истории — честный симметричный дефолт, без ТА
        hw = DEFAULT_HALF_WIDTH_PCT / 100.0
        return {
            "lower": round(price * (1 - hw), 8),
            "upper": round(price * (1 + hw), 8),
            "method": "default_symmetric",
            "pump_detected": False,
            "half_width_pct": DEFAULT_HALF_WIDTH_PCT,
            "note": (f"Нет истории цен — симметричный ±{DEFAULT_HALF_WIDTH_PCT:.0f}% "
                     f"по умолчанию. ТА недоступен."),
        }

    half_width_pct = max(MIN_HALF_WIDTH_PCT,
                         min(MAX_HALF_WIDTH_PCT, a * ATR_MULTIPLIER))
    hw = half_width_pct / 100.0

    chg = pct_change_24h(history)
    pump = chg is not None and chg >= PUMP_THRESHOLD_PCT

    if pump:
        # сдвигаем центр ниже текущей цены: верх не задираем, низ держим с запасом
        shift = hw * PUMP_SHIFT_FRACTION
        center = price * (1 - shift)
        note = (f"Рост +{chg:.0f}% за 24ч — вероятен откат. Центр сдвинут ниже "
                f"цены: низкий верх, запас вниз. Полуширина ±{half_width_pct:.0f}% "
                f"(ATR {a:.1f}%).")
    else:
        center = price
        note = (f"Симметрично вокруг цены. Полуширина ±{half_width_pct:.0f}% "
                f"(ATR {a:.1f}%" + (f", 24ч {chg:+.0f}%" if chg is not None else "") + ").")

    return {
        "lower": round(center * (1 - hw), 8),
        "upper": round(center * (1 + hw), 8),
        "method": "atr_asymmetric" if pump else "atr_symmetric",
        "pump_detected": bool(pump),
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
