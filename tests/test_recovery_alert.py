# -*- coding: utf-8 -*-
"""Алерт на recovery_override (16.07, чекпойнт «месяц 3»).

Re-entry — слабейшее место движка (−141пп в walk-forward 2023–24), его фикс
ни разу не работал вживую. Момент первого срабатывания recovery_override —
стартовый выстрел живого пре-регистрированного теста (analyze_prod_log.py §2:
cap ≥0.95 за ≤5 логовых дней, без сброса 14д) — нельзя проспать, глазами CSV
никто ежедневно не смотрит.

Правила: алерт на переход False→True (старт теста) и True→False (сброс,
с числом дней) — по одному на переход, без повторов, пока значение держится.
"""
from datetime import date

from prod_logger import decide_recovery_alert


def test_first_fire_alerts_and_remembers():
    msg, st = decide_recovery_alert({}, True, date(2026, 8, 1))
    assert msg is not None and "recovery_override" in msg
    assert "re-entry" in msg.lower()
    assert st == {"active": True, "since": "2026-08-01"}


def test_no_repeat_while_true():
    st0 = {"active": True, "since": "2026-08-01"}
    msg, st = decide_recovery_alert(st0, True, date(2026, 8, 3))
    assert msg is None
    assert st == st0


def test_reset_alerts_with_days_held():
    st0 = {"active": True, "since": "2026-08-01"}
    msg, st = decide_recovery_alert(st0, False, date(2026, 8, 4))
    assert msg is not None and "сброс" in msg.lower()
    assert "3" in msg                       # держался 3 дня
    assert st == {"active": False, "since": None}


def test_quiet_while_false():
    msg, st = decide_recovery_alert({"active": False, "since": None},
                                    False, date(2026, 8, 1))
    assert msg is None
    assert st == {"active": False, "since": None}


def test_fresh_state_false_stays_quiet():
    """Исторически override всегда был False — первый прогон с пустым
    состоянием и False не должен слать ничего."""
    msg, st = decide_recovery_alert({}, False, date(2026, 8, 1))
    assert msg is None
    assert st == {"active": False, "since": None}
