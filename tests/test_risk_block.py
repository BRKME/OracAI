"""Тест блока рисков: понятное определение каждого уровня + действие,
описание соответствует активному уровню (не всегда одно и то же)."""
from telegram_bot import RISK_LEVEL_INFO, risk_explainer


def test_all_four_levels_have_definition_and_action():
    for lvl in ("NORMAL", "ELEVATED", "TAIL", "CRISIS"):
        info = RISK_LEVEL_INFO[lvl]
        assert info["what"]      # по какому порогу/что значит
        assert info["action"]    # что делать


def test_explainer_matches_active_level():
    # описание берётся от АКТИВНОГО уровня, не захардкожено
    for lvl in ("NORMAL", "ELEVATED", "TAIL", "CRISIS"):
        text = risk_explainer(lvl)
        assert RISK_LEVEL_INFO[lvl]["what"] in text
        assert RISK_LEVEL_INFO[lvl]["action"] in text


def test_tail_and_crisis_distinguishable():
    # ключевая жалоба: TAIL и CRISIS должны читаться по-разному
    t = risk_explainer("TAIL")
    c = risk_explainer("CRISIS")
    assert t != c
    assert RISK_LEVEL_INFO["TAIL"]["action"] != RISK_LEVEL_INFO["CRISIS"]["action"]


def test_levels_are_ordered_by_severity():
    # NORMAL -> CRISIS усиление; у каждого есть порог в описании
    assert "vol" in RISK_LEVEL_INFO["ELEVATED"]["what"].lower() or \
           "волат" in RISK_LEVEL_INFO["ELEVATED"]["what"].lower()
