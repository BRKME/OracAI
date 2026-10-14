"""Тексты сообщения не должны утверждать то, чего система не посчитала.

Живое сообщение 12.10:
  «🟠 ФИКСИРОВАТЬ · 55% … Сигнал вершины или TAIL риск.» — при top 47%,
  сигнала вершины не было: долю срезали TAIL-риск и уверенный BEAR. «Или»
  перекладывало на читателя выяснение причины, которую код знает.
  «комиссии кроют IL с запасом 0.1x ✗» — при 0.1x комиссии покрывают IL на
  десятую часть; «с запасом» — прямая неправда (0.1 — ещё и нижний клип).
"""
from telegram_bot import action_for, lp_fee_phrase


class TestFixReasonNamed:
    def test_tail_without_top_does_not_claim_top(self):
        action, note = action_for(target_pos=0.55, risk_state="TAIL",
                                   dd_from_high=-8, bear_confirmation=True,
                                   bottom_prox=0.52, top_signal=False,
                                   bear_regime=True)
        assert "ФИКСИРОВАТЬ" in action
        assert "вершин" not in note.lower() or "не вершин" in note.lower()
        assert " или " not in note
        assert "хвост" in note.lower()
        assert "bear" in note.lower()

    def test_top_signal_named_as_top(self):
        _, note = action_for(target_pos=0.55, risk_state="NORMAL",
                             dd_from_high=-3, bear_confirmation=False,
                             top_signal=True)
        assert "вершин" in note.lower()
        assert " или " not in note

    def test_bear_regime_only(self):
        _, note = action_for(target_pos=0.60, risk_state="NORMAL",
                             dd_from_high=-5, bear_confirmation=True,
                             top_signal=False, bear_regime=True)
        assert "bear" in note.lower()
        assert "вершин" not in note.lower() or "не вершин" in note.lower()

    def test_drawdown_branch_unchanged(self):
        _, note = action_for(target_pos=0.60, risk_state="NORMAL",
                             dd_from_high=-20, bear_confirmation=True)
        assert "Просадка 20%" in note


class TestLpFeePhrase:
    def test_below_one_says_not_covering(self):
        s = lp_fee_phrase(0.1)
        assert "запас" not in s
        assert "не кроют" in s.lower()
        assert "0.1x" in s and "✗" in s

    def test_marginal(self):
        s = lp_fee_phrase(1.2)
        assert "запас" not in s
        assert "1.2x" in s and "⚠️" in s

    def test_comfortable(self):
        s = lp_fee_phrase(2.4)
        assert "с запасом" in s and "2.4x" in s and "✓" in s


class TestRegimePercents:
    """12.10: 5+76+4+12 = 97% — каждый процент обрезался int() вниз."""

    def test_sums_to_100_when_probs_sum_to_one(self):
        from telegram_bot import regime_percents
        p = regime_percents([0.0599, 0.7699, 0.0499, 0.1203])
        assert sum(p) == 100
        assert p == [6, 77, 5, 12]

    def test_does_not_invent_mass(self):
        from telegram_bot import regime_percents
        # вероятности сами в сумме 0.90 — 100% не рисуем
        assert sum(regime_percents([0.30, 0.30, 0.30, 0.0])) == 90

    def test_exact_values_unchanged(self):
        from telegram_bot import regime_percents
        assert regime_percents([0.25, 0.25, 0.25, 0.25]) == [25, 25, 25, 25]


class TestCase1410:
    """14.10: TAIL активен, но RSI 49 — потолок TAIL (RSI>50) НЕ сработал.
    60% дал только уверенный BEAR. Нельзя писать «долю режет хвостовой риск»."""

    def test_tail_active_but_not_cutting(self):
        _, note = action_for(target_pos=0.60, risk_state="TAIL",
                             dd_from_high=-8, bear_confirmation=True,
                             bottom_prox=0.54, top_signal=False,
                             bear_regime=True, tail_cut=False)
        assert "хвост" not in note.lower()
        assert "bear" in note.lower()
