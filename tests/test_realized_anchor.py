"""Тесты realized-price якоря дна в cycle_context.

Galaxy Research (12.06.2026) для оценки дна использует realized price (средняя
цена покупки всех монет) — это знаменатель MVRV: realized = price / MVRV.
Исторически дно цикла на 25-44% НИЖЕ realized price. Добавляем это как ВТОРОЙ
независимый индикатор дна → зона ACCUMULATION/капитуляция подтверждается
кворумом двух метрик, а не одной (каркас Galaxy «4 из 13»).

НЕ торговый таргет: ценовой ориентир дна — приор для согласия с MVRV-сигналом.
"""
import numpy as np

from cycle_context import realized_price_anchor, compute_cycle_context


class TestRealizedPriceAnchor:
    def test_realized_from_price_and_mvrv(self):
        a = realized_price_anchor(price=70000, mvrv=1.27)
        assert abs(a["realized_price"] - 55118) < 50      # 70000/1.27

    def test_bottom_band_25_to_44_below(self):
        a = realized_price_anchor(price=70000, mvrv=1.27)
        rp = a["realized_price"]
        assert abs(a["bottom_high"] - rp * 0.75) < 1      # -25%
        assert abs(a["bottom_low"] - rp * 0.56) < 1       # -44%

    def test_none_mvrv_returns_none(self):
        assert realized_price_anchor(price=70000, mvrv=None) is None

    def test_zero_mvrv_safe(self):
        assert realized_price_anchor(price=70000, mvrv=0) is None


class TestAnchorState:
    # price vs realized: выше = не дно; в полосе -25..44% = зона дна; ниже = овершут
    def test_above_realized_not_bottom(self):
        a = realized_price_anchor(price=70000, mvrv=1.27)   # price 70k > RP 55k
        assert a["state"] == "above_realized"

    def test_in_bottom_band(self):
        # price 38k, realized 55k -> -31% -> в полосе -25..44%
        a = realized_price_anchor(price=38000, mvrv=38000/55000)
        assert a["state"] == "in_bottom_band"

    def test_below_band_overshoot(self):
        a = realized_price_anchor(price=28000, mvrv=28000/55000)  # -49%
        assert a["state"] == "below_band_overshoot"


class TestQuorumIntegration:
    def _close(self, last, n=260, slope=0.0):
        # ряд с заданной последней ценой и лёгким наклоном
        return np.array([last * (1 + slope * (n - i)) for i in range(n)], dtype=float)

    def test_anchor_present_in_output(self):
        ctx = compute_cycle_context(self._close(60000), mvrv=1.2)
        assert "bottom_anchor" in ctx["metrics"]
        assert ctx["metrics"]["bottom_anchor"]["realized_price"] is not None

    def test_quorum_flag_when_both_agree(self):
        # MVRV<1.0 (капитуляция) И цена в полосе дна -> кворум 2/2
        close = self._close(30000)
        ctx = compute_cycle_context(close, mvrv=0.62)   # rp~48k, price 30k -> -38%
        assert ctx["bottom_quorum"]["agree"] is True
        assert ctx["bottom_quorum"]["count"] == 2

    def test_no_quorum_when_only_one(self):
        # NEUTRAL по MVRV, цена выше realized -> ни один не指 на дно
        ctx = compute_cycle_context(self._close(60000), mvrv=1.2)
        assert ctx["bottom_quorum"]["agree"] is False

    def test_quorum_absent_without_mvrv(self):
        ctx = compute_cycle_context(self._close(60000), mvrv=None)
        assert ctx["bottom_quorum"]["count"] == 0
