"""Prune стейл-ключей трекинга (10.07.2026): ключи, не виденные в сканах
>3 дней, удаляются из positions_fees_tracking (закрытые/перевёрнутые позиции
— OBED меняет token_id при reopen). last_seen хранится рядом. Фейл-режим
безопасен: ошибочный prune живой позиции = потеря одного интервала fees
(вернётся -> baseline, delta 0), не накрутка. Миграция: ключи без last_seen,
отсутствующие в текущем скане, прюнятся сразу (известные 18 стейлов)."""
import json
from datetime import datetime, timedelta, timezone
from lp_system import prune_stale_tracking

NOW = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


def test_fresh_keys_kept_stale_pruned():
    tracking = {"a": 10.0, "b": 5.0, "stale": 90.0}
    last_seen = {"a": _iso(0.1), "b": _iso(2), "stale": _iso(5)}
    t2, ls2 = prune_stale_tracking(tracking, last_seen, {"a"}, now=NOW)
    assert "stale" not in t2 and "stale" not in ls2
    assert "a" in t2 and "b" in t2          # b: 2д < порога 3д — живёт


def test_current_scan_refreshes_last_seen():
    tracking = {"a": 1.0}
    last_seen = {"a": _iso(2.9)}
    t2, ls2 = prune_stale_tracking(tracking, last_seen, {"a"}, now=NOW)
    assert ls2["a"] == NOW.isoformat()      # виден сейчас -> обновлён


def test_migration_absent_without_last_seen_pruned_immediately():
    tracking = {"live": 3.0, "ghost1": 50.0, "ghost2": 40.0}
    t2, ls2 = prune_stale_tracking(tracking, {}, {"live"}, now=NOW)
    assert set(t2) == {"live"}              # 18 стейлов умирают первым прогоном


def test_new_key_in_scan_gets_last_seen():
    t2, ls2 = prune_stale_tracking({}, {}, {"newpos"}, now=NOW)
    assert ls2 == {"newpos": NOW.isoformat()}
