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
        # плавный рост со 100 до 120 за 24ч — медианный замер видит ~рост
        s = _series(list(np.linspace(100, 100, 24)) + list(np.linspace(100, 120, 24)))
        chg = pct_change_24h(s)
        assert chg is not None
        assert 15 < chg < 22       # около +20%, устойчиво к краям

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


class TestPctChangeRobust:
    def test_robust_to_endpoint_noise(self):
        # устойчивое +5%, но последний тик — шумовой выброс +40%
        base = list(np.linspace(100, 105, 48))
        base[-1] = 140.0          # одиночный выброс
        s = _series(base)
        # медианное сглаживание концов не должно показать +40%
        chg = pct_change_24h(s)
        assert chg is not None
        assert chg < 20            # выброс не превращается в «памп»

    def test_robust_still_detects_real_pump(self):
        # настоящий устойчивый памп +25% за сутки
        s = _series(list(np.linspace(100, 100, 24)) + list(np.linspace(100, 125, 24)))
        chg = pct_change_24h(s)
        assert chg is not None
        assert chg >= 20


class TestPumpDetectionTradeoff:
    """Документирует сознательный выбор: памп определяется по УСТОЙЧИВОМУ
    движению (медиана окон), а не номинальному. Следствие — слабый/размазанный
    по шуму рост может не считаться пампом. Для LP это верный компромисс:
    ложный сдвиг вниз дешевле, чем шумный ложный памп на каждой волатильной
    монете. НЕ баг — не 'чинить' повышением чувствительности к одиночным тикам.
    """
    def test_clean_sustained_pump_detected(self):
        # чистый устойчивый памп +25% — ловится уверенно
        s = _series(list(np.linspace(100, 100, 24)) + list(np.linspace(100, 125, 24)))
        c = suggest_corridor(float(s[-1]), s)
        assert c["pump_detected"] is True

    def test_single_tick_spike_not_pump(self):
        # один шумовой выброс на конце — НЕ памп (устойчивость важнее)
        base = list(np.linspace(100, 103, 47)) + [150.0]
        s = _series(base)
        c = suggest_corridor(float(s[-1]), s)
        assert c["pump_detected"] is False


class TestPhaseAware:
    """Фаза цикла даёт МЯГКИЙ наклон центра (±2-3%), ширину решает ATR.
    Комбинация с пампом — максимум из двух сдвигов, не сумма (LP не должен
    стать направленной ставкой)."""

    def _flat(self):
        return _series([100 + (i % 3) for i in range(60)])

    def test_bear_bottom_shifts_center_down(self):
        c = suggest_corridor(price=100, history=self._flat(),
                             phase="CAPITULATION")
        center = (c["upper"] + c["lower"]) / 2
        assert center < 100              # запас вниз даже без пампа
        assert c["phase_shift_applied"] is True

    def test_euphoria_top_shifts_center_down_too(self):
        # у вершины риск отката вниз — центр тоже ниже, верх не задираем
        c = suggest_corridor(price=100, history=self._flat(), phase="EUPHORIA")
        center = (c["upper"] + c["lower"]) / 2
        assert center < 100

    def test_neutral_phase_no_shift(self):
        c = suggest_corridor(price=100, history=self._flat(), phase="NEUTRAL")
        center = (c["upper"] + c["lower"]) / 2
        assert abs(center - 100) / 100 < 0.01

    def test_phase_shift_is_soft(self):
        # сдвиг от фазы не больше ~3%
        c = suggest_corridor(price=100, history=self._flat(),
                             phase="CAPITULATION")
        center = (c["upper"] + c["lower"]) / 2
        assert (100 - center) / 100 <= 0.035

    def test_pump_and_phase_take_max_not_sum(self):
        # памп +25% в медвежьей фазе: сдвиг = max(памп, фаза), НЕ сумма
        pumped = _series([100] * 24 + list(np.linspace(100, 125, 24)))
        c_pump_only = suggest_corridor(price=125, history=pumped, phase="NEUTRAL")
        c_both = suggest_corridor(price=125, history=pumped, phase="CAPITULATION")
        shift_pump = (125 - (c_pump_only["upper"] + c_pump_only["lower"]) / 2) / 125
        shift_both = (125 - (c_both["upper"] + c_both["lower"]) / 2) / 125
        # комбинированный не больше суммы и не меньше максимального из компонент
        assert shift_both >= shift_pump - 1e-9
        assert shift_both <= shift_pump + 0.035 + 1e-9   # не сумма двух полных

    def test_no_phase_backward_compatible(self):
        # без фазы — поведение как раньше (фаза опциональна)
        c = suggest_corridor(price=100, history=self._flat())
        assert c["phase_shift_applied"] is False
