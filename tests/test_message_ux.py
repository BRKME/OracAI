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


class TestCrisisHysteresis:
    """12–16.11.2026: доля шла 100 → 20 → 100 → 100 → 20%. Волатильность
    колебалась у порога входа в кризис (2.04 / 1.97 / 1.95 / 2.11), и разница
    в 0.07 переворачивала решение на 80 пп. Вошли в кризис — остаёмся, пока
    волатильность не опустится ниже 1.5. На истории итог тот же (×3.01
    против ×3.00)."""

    def _o(self, vol_z, prev, r7=-0.1):
        o = _out(vol_z=vol_z, tail=True)
        o["metadata"]["returns_7d"] = r7
        o["metadata"]["prev_crisis"] = prev
        return o

    def test_stays_in_crisis_just_below_entry(self):
        assert "паник" in _text(self._o(1.97, True)).lower()

    def test_does_not_enter_below_entry(self):
        assert "паник" not in _text(self._o(1.97, False)).lower()

    def test_leaves_when_volatility_calms(self):
        assert "паник" not in _text(self._o(1.4, True)).lower()

    def test_leaves_when_price_recovers(self):
        assert "паник" not in _text(self._o(1.97, True, r7=0.05)).lower()


def test_crisis_block_says_panic_once_and_explains_bottom():
    """16.11: «на рынке паника» дважды подряд, а «цикл ближе к дну» рядом с
    «ПРОДАВАТЬ» читалось как противоречие."""
    o = _out(vol_z=2.6, tail=True)
    o["metadata"]["returns_7d"] = -0.14
    o["metadata"]["drawdown_from_ath"] = -48.0
    o["metadata"]["mvrv"] = 1.21
    block = "\n".join(_text(o).splitlines()[2:5])
    assert block.lower().count("паник") == 1
    assert "временн" in block.lower()


def test_transition_leader_is_not_called_unclear_twice():
    """«Режим: неясен — перевес TRANSITION» — TRANSITION и значит «переходный»."""
    o = _out(regime="TRANSITION", conf=0.25, probs=(0.05, 0.35, 0.04, 0.56))
    line = next(l for l in _text(o).splitlines() if l.startswith("Режим:"))
    assert "переходный" in line and "BEAR 35%" in line
    assert "перевес 🔄 TRANSITION" not in line


def test_crisis_label_and_share_agree():
    """Кризис в аптренде: восстановление поднимало долю до 95%, а ярлык
    оставался «ПРОДАВАТЬ» — «ПРОДАВАТЬ · 95%» (13 дней на истории)."""
    o = _out(vol_z=2.6)
    o["metadata"]["returns_7d"] = -0.08
    head = _text(o).splitlines()[2]
    assert "ПРОДАВАТЬ" in head and "20%" in head


class TestShareWording:
    """16.11: «ПРОДАВАТЬ · 20%» читалось как «продай 20%», а примечание
    говорило «сократи до этой доли». Число — целевая доля, и это должно быть
    видно в самой строке."""

    def test_sell_says_down_to(self):
        o = _out(vol_z=2.6)
        o["metadata"]["returns_7d"] = -0.08
        lines = _text(o).splitlines()
        assert lines[2] == "🔴 ПРОДАВАТЬ · оставить 20% в BTC"
        assert "до 20%" in lines[4] and "этой доли" not in lines[4]

    def test_hold_says_share(self):
        assert _text(_out()).splitlines()[2] == "⚪ ДЕРЖАТЬ · доля BTC 95%"


def test_fear_greed_has_its_own_bar_under_probabilities():
    lines = _text(_out()).splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith("TRANS "))
    j = next(k for k, l in enumerate(lines) if l.startswith("Fear & Greed"))
    assert j > i
    assert lines[j + 1].startswith("[######....] 63") and "жадность" in lines[j + 1]
    assert "страх/жадность" not in _text(_out())      # не дублируется в строке RSI


class TestCapitulation:
    """16.11.2026: кризис при RSI 20 и страхе 10 велел продать до 20% —
    после падения на 20%. На истории кризис при RSI ≥ 25 оправдан (через
    30 дней −2.7%), при RSI < 25 — поздний (+2.4%, через 90 дней +27.6%).
    С условием «RSI < 25 → держим 90%» итог ×3.26 против ×2.95. Фильтр по
    Fear & Greed проверен и НЕ подтвердился (×2.83 при ≤15, ×2.60 при ≤20):
    сильный страх держится всё падение."""

    def _o(self, rsi, fg=10):
        o = _out(vol_z=2.6, tail=True)
        m = o["metadata"]
        m["returns_7d"] = -0.14
        m["rsi"]["rsi_1d"] = rsi
        m["drawdown_from_ath"] = -48.0
        m["mvrv"] = 1.21
        o["bucket_details"]["sentiment"]["fg_raw"] = fg
        return o

    def test_oversold_panic_holds_instead_of_selling(self):
        lines = _text(self._o(20)).splitlines()
        assert lines[2] == "⚪ ДЕРЖАТЬ · доля BTC 90%"
        block = "\n".join(lines[2:5]).lower()
        assert "паник" in block and "перепродан" in block
        assert "не продаём" in block and "не докупаем" in block

    def test_early_panic_still_sells(self):
        assert _text(self._o(31)).splitlines()[2] == "🔴 ПРОДАВАТЬ · оставить 20% в BTC"

    def test_fear_alone_does_not_cancel_the_sale(self):
        assert "ПРОДАВАТЬ" in _text(self._o(31, fg=8)).splitlines()[2]

    def test_hedge_stays_mandatory_in_capitulation(self):
        assert "ОБЯЗАТЕЛЕН" in _text(self._o(20), _lp())
