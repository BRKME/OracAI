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
