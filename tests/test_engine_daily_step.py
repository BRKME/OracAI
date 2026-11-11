"""Движок делает один шаг в сутки, сколько бы раз его ни запускали.

Аудит 11.11.2026: public_channel каждый час вызывал engine.process() и
сохранял состояние. Сглаживание вероятностей, «дни подтверждения» и история
блоков считались в ЗАПУСКАХ, а не в днях: 32 записи в журнале режима за
5 дней вместо ~11, 27 смен режима за 205 дней вживую против 2 в тесте на тех
же датах. Повторный запуск в те же сутки теперь ПЕРЕСЧИТЫВАЕТ шаг дня от
состояния на начало суток, а не добавляет новый.
"""
import copy

import pytest

import engine
import backtest_engine_real as B


@pytest.fixture(scope="module")
def dfs():
    return B.load_all_data()


@pytest.fixture
def ram(monkeypatch):
    box = {"state": engine.default_state()}
    monkeypatch.setattr(engine, "load_state", lambda: copy.deepcopy(box["state"]))
    monkeypatch.setattr(engine, "save_state",
                        lambda s: box.__setitem__("state", copy.deepcopy(s)))
    monkeypatch.setattr(engine, "_fetch_mvrv_safe", lambda *a, **k: None)
    return box


def _run(dfs, i, as_of):
    return engine.RegimeEngine().process(B.build_raw_data_for_day(dfs, i, 120), as_of=as_of)


def test_repeat_runs_same_day_make_one_step(dfs, ram):
    for i, d in ((1500, "2025-05-01"), (1501, "2025-05-02")):
        _run(dfs, i, d)
    once = copy.deepcopy(ram["state"])

    ram["state"] = engine.default_state()
    _run(dfs, 1500, "2025-05-01")
    for _ in range(6):                       # «ежечасные» повторы за те же сутки
        out = _run(dfs, 1501, "2025-05-02")
    many = ram["state"]

    assert len(many["regime_log"]) == len(once["regime_log"]) == 2
    assert len(many["bucket_history"]["Momentum"]) == 2
    assert many["P_prev"] == pytest.approx(once["P_prev"])
    assert many["holds_for"] == once["holds_for"]
    assert out["regime"] == many["current_regime"]   # сообщение = сохранённое


def test_next_day_advances(dfs, ram):
    _run(dfs, 1500, "2025-05-01")
    _run(dfs, 1500, "2025-05-01")
    _run(dfs, 1501, "2025-05-02")
    assert len(ram["state"]["regime_log"]) == 2
    assert ram["state"]["last_step_date"] == "2025-05-02"


def test_days_in_regime_counts_days_in_replay(dfs, ram):
    """В тесте на истории «сегодня» бралось с часов, и счётчик стоял на нуле."""
    for k in range(5):
        out = _run(dfs, 1500 + k, f"2025-05-0{k + 1}")
    assert out["metadata"]["days_in_regime"] >= 1 or \
        len(set(ram["state"]["regime_log"])) > 1


class TestTrueAth:
    """«Просадка от ATH» считалась от максимума в окне цен (в бою — 1 год).
    11.11.2026 система видела −27.6% при настоящих −41%: вершина 06.10.2025
    выпала из окна. Вес этого слагаемого в оценке дна — 0.35."""

    def test_ath_older_than_window_is_used(self, dfs, ram):
        btc = dfs["btc_ohlcv"]
        i = len(btc) - 1
        as_of = str(btc["date"].iloc[i].date())
        out = _run(dfs, i, as_of)
        true_dd = (btc["close"].iloc[i] / btc["close"].max() - 1) * 100
        assert out["metadata"]["drawdown_from_ath"] == pytest.approx(true_dd, abs=0.1)
        assert out["metadata"]["drawdown_from_ath"] < -35

    def test_no_lookahead_in_replay(self, dfs, ram):
        """На истории ATH берётся только из дней до прогоняемого."""
        btc = dfs["btc_ohlcv"]
        i = 600                                   # 2022 год, до вершины 2025
        as_of = str(btc["date"].iloc[i].date())
        out = _run(dfs, i, as_of)
        past_max = btc["close"].iloc[: i + 1].max()
        exp = (btc["close"].iloc[i] / past_max - 1) * 100
        assert out["metadata"]["drawdown_from_ath"] == pytest.approx(exp, abs=0.1)
