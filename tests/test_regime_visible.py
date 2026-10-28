"""Смена режима должна быть видна сразу, а не только в справке внизу.

16.10: режим сменился BEAR → BULL, но в сообщении это было только в блоке
«Режим рынка» в самом конце — шапка и действие смену не показывали.
"""
import copy
import json
from pathlib import Path

from telegram_bot import format_output

BASE = json.loads((Path(__file__).parent / "fixtures" / "output_1610.json").read_text())


def _out(regime="BULL", days=1, prev="BEAR", start="2026-10-15", conf=0.2851):
    o = copy.deepcopy(BASE)
    o["regime"] = regime
    o["confidence"]["quality_adjusted"] = conf
    o["metadata"]["days_in_regime"] = days
    o["metadata"]["previous_regime"] = prev
    o["metadata"]["regime_start_date"] = start
    return o


def _text(o):
    return format_output(o, None, o.get("asset_allocation"))


def test_regime_line_near_top():
    lines = _text(_out()).splitlines()
    reg = next(i for i, l in enumerate(lines) if l.startswith("Режим:"))
    assert reg <= 6 and "BULL" in lines[reg]


def test_fresh_switch_line_near_top():
    lines = _text(_out()).splitlines()
    top = "\n".join(lines[:9])
    assert "🐻 BEAR → 🐂 BULL" in top
    assert "15.10" in top
    assert "низк" in top.lower()   # conf 28% — честно предупреждаем


def test_no_switch_line_when_regime_old():
    text = _text(_out(days=12))
    assert "Смена режима" not in text


def test_bear_switch_line():
    text = _text(_out(regime="BEAR", prev="BULL", days=0))
    assert "🐂 BULL → 🐻 BEAR" in text


def test_missing_previous_regime_does_not_invent_it():
    o = _out()
    del o["metadata"]["previous_regime"]
    text = _text(o)
    assert "Смена режима" not in text


def test_leader_marked_in_probability_block():
    lines = _text(_out()).splitlines()
    bull = next(l for l in lines if l.startswith("BULL "))
    bear = next(l for l in lines if l.startswith("BEAR "))
    assert "◀" in bull and "◀" not in bear


def test_previous_regime_from_log():
    from engine import previous_regime
    assert previous_regime(["BEAR", "BEAR", "BULL", "BULL"]) == "BEAR"
    assert previous_regime(["BULL", "BULL"]) is None
    assert previous_regime([]) is None
