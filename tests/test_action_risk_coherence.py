"""Блок действия не должен противоречить риск-блоку.

Баг (живое сообщение 13.06): TAIL-риск говорит «не лови ножи, сократи плечо»,
а блок действия в той же телеге кричит «ПОКУПАТЬ, целевая 100%». Action видел
только CRISIS как тормоз, не TAIL. При TAIL покупка дна остаётся (стратегически
дно близко), но БЕЗ «100% сразу»: вход ступенями, со ссылкой на риск.

Также: целевая позиция — это цель к набору ЛЕСТНИЦЕЙ, а не «купи всё сейчас»
(согласование с cycle-ladder).
"""
from telegram_bot import action_for


class TestTailGatesAllIn:
    def test_strong_bottom_but_tail_is_laddered_not_all_in(self):
        action, note = action_for(target_pos=1.0, risk_state="TAIL",
                                   dd_from_high=-50, bear_confirmation=True)
        assert "ПОКУПАТЬ" in action          # дно близко — покупка остаётся
        assert "не на всё сразу" in note.lower()  # явный запрет единовременной
        assert "ступен" in note.lower()       # вход лесенкой
        assert "риск" in note.lower()         # ссылка на риск-блок

    def test_strong_bottom_normal_risk_can_be_full(self):
        action, note = action_for(target_pos=1.0, risk_state="NORMAL",
                                   dd_from_high=-50, bear_confirmation=True)
        assert "ПОКУПАТЬ" in action
        # при норме целевая 100% допустима, но всё равно через лестницу
        assert "лестниц" in note.lower() or "ступен" in note.lower()

    def test_crisis_still_defense(self):
        action, note = action_for(target_pos=1.0, risk_state="CRISIS",
                                   dd_from_high=-60, bear_confirmation=True)
        assert "ЗАЩИТА" in action

    def test_hold_zone_unchanged(self):
        action, note = action_for(target_pos=0.90, risk_state="NORMAL",
                                   dd_from_high=-10, bear_confirmation=False)
        assert "ДЕРЖАТЬ" in action

    def test_sell_zone_unchanged(self):
        action, note = action_for(target_pos=0.40, risk_state="NORMAL",
                                   dd_from_high=-30, bear_confirmation=True)
        assert "ПРОДАВАТЬ" in action


class TestLadderConsistency:
    def test_buy_note_never_says_buy_everything_now(self):
        for rs in ("NORMAL", "ELEVATED", "TAIL"):
            _, note = action_for(target_pos=1.0, risk_state=rs,
                                 dd_from_high=-50, bear_confirmation=True)
            # не должно звучать как «всё сразу»
            assert "сразу на всё" not in note.lower()


class TestNoSellNearBottom:
    """У дна (высокий bottom_prox / extreme fear / низкий RSI) ярлык не может
    быть 'ФИКСИРОВАТЬ/ПРОДАВАТЬ', даже при умеренной целевой доле. Продавать
    на просадке у дна = фиксировать убыток против лестницы. Баг 15.06:
    bottom_prox 0.63 + просадка 20% дали 'ФИКСИРОВАТЬ 60%' у дна."""

    def test_moderate_target_near_bottom_is_hold_not_fix(self):
        action, note = action_for(target_pos=0.60, risk_state="TAIL",
                                   dd_from_high=-20, bear_confirmation=True,
                                   bottom_prox=0.63)
        assert "ФИКСИРОВАТЬ" not in action
        assert "ПРОДАВАТЬ" not in action
        # у дна умеренная доля = удержание базовой позиции, не продажа
        assert "ДЕРЖАТЬ" in action or "ПОКУПАТЬ" in action

    def test_fix_still_valid_near_top(self):
        # у вершины фиксация остаётся корректной
        action, _ = action_for(target_pos=0.60, risk_state="NORMAL",
                               dd_from_high=-10, bear_confirmation=True,
                               bottom_prox=0.15)
        assert "ФИКСИРОВАТЬ" in action

    def test_sell_still_valid_near_top(self):
        action, _ = action_for(target_pos=0.40, risk_state="NORMAL",
                               dd_from_high=-15, bear_confirmation=True,
                               bottom_prox=0.10)
        assert "ПРОДАВАТЬ" in action

    def test_bottom_prox_optional_backward_compatible(self):
        # без bottom_prox старое поведение сохраняется
        action, _ = action_for(target_pos=0.60, risk_state="NORMAL",
                               dd_from_high=-15, bear_confirmation=True)
        assert "ФИКСИРОВАТЬ" in action
