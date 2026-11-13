"""UX-аудит сообщения 28.10.

Находки: шапка «🐂 BULL» при уверенности 18%; строка смены режима на каждом
колебании; постоянные «слом структуры» / «дёргается» / «хедж ОБЯЗАТЕЛЕН» от
флага, включённого 100% дней; LP 5.0x / 0.1x — края клипа под видом точных
чисел; дубли в блоке действия; жаргон; нет цены и сравнения со вчера.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

from telegram_bot import format_output

BASE = json.loads((Path(__file__).parent / "fixtures" / "output_1610.json").read_text())


def _out(regime="BULL", conf=0.18, days=1, prev="BEAR", switches=1,
         probs=(0.42, 0.25, 0.19, 0.14), prev_target=None, vol_z=-0.5,
         tail=False, price=81925.6):
    o = copy.deepcopy(BASE)
    o["regime"] = regime
    o["confidence"]["quality_adjusted"] = conf
    o["probabilities"] = dict(zip(("BULL", "BEAR", "RANGE", "TRANSITION"), probs))
    m = o["metadata"]
    m.update(days_in_regime=days, previous_regime=prev,
             regime_start_date="2026-10-27", regime_switches_14d=switches,
             vol_z=vol_z, btc_price=price)
    if prev_target is not None:
        m["prev_sent_target"] = prev_target
    o["normalization"]["break_active"] = True
    o["asset_allocation"]["meta"]["tail_risk_active"] = tail
    return o


def _lp(fv=5.0, hedge=False):
    return SimpleNamespace(range_width="wide", fee_variance_ratio=fv,
                           hedge_recommended=hedge)


def _text(o, lp=None):
    return format_output(o, lp, o.get("asset_allocation"))


def test_header_has_price_not_overconfident_regime():
    head = _text(_out()).splitlines()[0]
    assert "$81,926" in head
    assert "BULL" not in head


def test_low_confidence_regime_is_called_unclear():
    t = _text(_out())
    line = next(l for l in t.splitlines() if l.startswith("Режим:"))
    assert "неясен" in line and "BULL 42%" in line and "BEAR 25%" in line
    assert "18%" in line


def test_confident_regime_stated_plainly():
    t = _text(_out(conf=0.55, days=12, probs=(0.70, 0.10, 0.10, 0.10)))
    line = next(l for l in t.splitlines() if l.startswith("Режим:"))
    assert "неясен" not in line and "BULL" in line


def test_flip_flop_replaces_switch_line():
    t = _text(_out(switches=5))
    assert "неустойчив" in t and "5 смен" in t
    assert "Смена режима" not in t


def test_single_fresh_switch_still_shown():
    t = _text(_out(switches=1, conf=0.40))
    assert "Смена режима" in t


def test_no_constant_alarms_from_always_on_break_flag():
    t = _text(_out(), _lp())
    assert "слом структуры" not in t
    assert "дёргается" not in t
    assert "ОБЯЗАТЕЛЕН" not in t


def test_real_volatility_still_raises_risk():
    t = _text(_out(vol_z=1.8), _lp())
    assert "дёргается" in t


def test_tail_still_forces_hedge():
    t = _text(_out(tail=True), _lp())
    assert "ОБЯЗАТЕЛЕН" in t


def test_lp_clip_edges_shown_as_bounds():
    assert "≥5x" in _text(_out(), _lp(fv=5.0))
    assert "≤0.1x" in _text(_out(), _lp(fv=0.1))
    assert "2.4x" in _text(_out(), _lp(fv=2.4))


def test_action_block_has_no_duplicates():
    t = _text(_out())
    assert t.count("выше SMA200") == 1
    assert t.lower().count("удерживаем позицию") <= 1


def test_action_line_has_no_previous_value():
    """12.11: «· было 90%» в строке действия оператору не нужно."""
    for pt in (0.95, 0.60):
        head = _text(_out(prev_target=pt)).splitlines()[2]
        assert "было" not in head and "без изменений" not in head


def test_no_english_phase_jargon():
    t = _text(_out())
    assert "EARLY_BULL" not in t and "Фаза:" not in t


def test_probability_block_kept():
    lines = _text(_out()).splitlines()
    assert any(l.startswith("BULL ") and "◀" in l for l in lines)


def test_score_line_shown_when_present():
    o = _out()
    o["metadata"]["score_line"] = "Счёт с 21.04: стратегия −4.0% · держать −0.7%"
    assert "Счёт с 21.04" in _text(o)
    assert "Счёт" not in _text(_out())


def test_tail_buy_says_ladder_once():
    """12.11: «активен хвостовой риск … ступенями» стояло в двух строках подряд."""
    o = _out(tail=True, regime="TRANSITION", conf=0.09)
    o["metadata"]["rsi"]["rsi_1d"] = 25.8          # RSI < 28 + близость к дну > 45%
    o["metadata"]["drawdown_from_ath"] = -41.0
    o["metadata"]["mvrv"] = 1.37
    t = _text(o)
    assert "ПОКУПАТЬ" in t.splitlines()[2]
    assert t.count("хвостовой риск") == 1
    assert t.lower().count("ступен") == 1


class TestCrisisOnlyOnFalls:
    """Кризис (продажа до 20%) — только при падении цены за 7 дней.

    Аудит 13.11: в 5 из 16 срабатываний 2021–2026 правило продавало после
    роста на 10–27% за неделю — волатильность взлетает и на ралли. С условием
    «цена за 7 дней упала» итог на истории ×3.00 против ×2.98, оборот ниже.
    """

    def _o(self, r7):
        o = _out(vol_z=2.6)
        o["metadata"]["returns_7d"] = r7
        return o

    def test_spike_on_rally_is_not_crisis(self):
        t = _text(self._o(0.15))
        assert "паника" not in t.lower()
        assert "дёргается" in t                 # остаётся повышенный риск

    def test_spike_on_fall_is_crisis(self):
        t = _text(self._o(-0.08))
        assert "ПРОДАВАТЬ" in t.splitlines()[2] and "паник" in t.lower()

    def test_unknown_7d_return_keeps_old_rule(self):
        o = _out(vol_z=2.6)
        o["metadata"].pop("returns_7d", None)
        assert "паник" in _text(o).lower()


def test_engine_reports_7d_return():
    import inspect, engine
    assert '"returns_7d"' in inspect.getsource(engine.RegimeEngine.process)
