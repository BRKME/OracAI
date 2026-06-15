"""Тесты расчёта коридора для LP-позиций, вышедших из range.

Логика (дефолты, оператор может поправить):
  • ширина от волатильности (ATR%): узкий на тихих, шире на волатильных;
  • асимметрия: если монета выросла > PUMP_THRESHOLD за 24ч — центр коридора
    сдвигается НИЖЕ текущей цены (ловим откат), верх не задираем;
  • без истории цен — честная деградация в симметричный коридор по умолчанию,
    без ложной ТА-точности.
Это предложение к ручному ребалансу, НЕ автоматическое исполнение.
"""
import numpy as np

from lp_corridor import suggest_corridor, atr_pct, pct_change_24h


def _series(prices):
    return np.array(prices, dtype=float)


class TestATR:
    def test_atr_pct_quiet_vs_volatile(self):
        quiet = _series([100, 101, 100, 101, 100, 101] * 5)
        volatile = _series([100, 120, 90, 125, 85, 130] * 5)
        assert atr_pct(quiet) < atr_pct(volatile)

    def test_atr_none_without_history(self):
        assert atr_pct(_series([100])) is None


class TestPctChange:
    def test_24h_change(self):
        # 24 часовых точки: 100 -> 120 = +20%
        s = _series([100] + [110] * 22 + [120])
        assert abs(pct_change_24h(s) - 20.0) < 1.0

    def test_none_without_enough(self):
        assert pct_change_24h(_series([100, 110])) is None


class TestCorridorWidth:
    def test_quiet_coin_narrow(self):
        quiet = _series([100 + (i % 2) for i in range(60)])
        c = suggest_corridor(price=100, history=quiet)
        width = (c["upper"] - c["lower"]) / 100 * 100
        assert width < 20          # тихая монета -> узкий коридор

    def test_volatile_coin_wider(self):
        volatile = _series([100 + 15 * np.sin(i) for i in range(60)])
        cq = suggest_corridor(price=100, history=_series([100 + (i % 2) for i in range(60)]))
        cv = suggest_corridor(price=100, history=volatile)
        wq = cq["upper"] - cq["lower"]
        wv = cv["upper"] - cv["lower"]
        assert wv > wq


class TestPumpAsymmetry:
    def test_pump_shifts_center_below_price(self):
        # резкий памп +30% за 24ч
        pumped = _series([100] * 12 + list(np.linspace(100, 130, 13)))
        c = suggest_corridor(price=130, history=pumped)
        center = (c["upper"] + c["lower"]) / 2
        assert center < 130            # центр ниже текущей цены
        assert c["pump_detected"] is True
        # верх не задран далеко над ценой
        assert (c["upper"] - 130) / 130 < (130 - c["lower"]) / 130

    def test_no_pump_symmetric(self):
        flat = _series([100 + (i % 3) for i in range(60)])
        c = suggest_corridor(price=100, history=flat)
        assert c["pump_detected"] is False
        center = (c["upper"] + c["lower"]) / 2
        assert abs(center - 100) / 100 < 0.05   # примерно симметрично


class TestDegradation:
    def test_no_history_symmetric_default(self):
        c = suggest_corridor(price=50, history=None)
        assert c["method"] == "default_symmetric"
        center = (c["upper"] + c["lower"]) / 2
        assert abs(center - 50) / 50 < 0.02
        assert c["pump_detected"] is False

    def test_corridor_always_brackets_price_when_no_pump(self):
        flat = _series([100 + (i % 3) for i in range(60)])
        c = suggest_corridor(price=100, history=flat)
        assert c["lower"] < 100 < c["upper"]
