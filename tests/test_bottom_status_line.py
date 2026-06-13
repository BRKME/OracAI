"""Тест строки статуса дна в Telegram-сообщении лестницы.

Оператор видит «BUY 30%» без контекста и читает это как «дно». Строка статуса
делает кворим дна видимым: сколько метрик из 2 за дно, и где полоса дна, чтобы
«покупай 30%» читалось как «первая ступень, дно ещё ниже».
"""
from cycle_logger import bottom_status_line


def _ctx(count, state, low=30743, high=41173, rp=54897, price=61400):
    return {
        "bottom_quorum": {"count": count, "agree": count == 2},
        "metrics": {"price": price, "bottom_anchor": {
            "realized_price": rp, "bottom_low": low,
            "bottom_high": high, "state": state}},
    }


def test_not_at_bottom_shows_band_and_quorum():
    line = bottom_status_line(_ctx(0, "above_realized"))
    assert "0/2" in line
    assert "дно ещё не подтверждено" in line.lower() or "не дошли" in line.lower()
    # полоса дна видна оператору
    assert "30" in line and "41" in line


def test_partial_quorum():
    line = bottom_status_line(_ctx(1, "in_bottom_band"))
    assert "1/2" in line


def test_full_quorum_says_bottom_zone():
    line = bottom_status_line(_ctx(2, "in_bottom_band", price=38000))
    assert "2/2" in line
    assert "дно" in line.lower()


def test_no_anchor_returns_empty():
    ctx = {"bottom_quorum": {"count": 0}, "metrics": {"bottom_anchor": {"realized_price": None}}}
    assert bottom_status_line(ctx) == ""
