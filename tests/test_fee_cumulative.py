"""Пофиционный учёт cumulative fees (фикс 02.07: агрегатная детекция harvest
надувала счётчик на RPC-фейлах и reopen'ах — «всего $5,999» при честном
run-rate на порядок ниже)."""
import json
import pytest
import lp_system


@pytest.fixture
def hist(tmp_path, monkeypatch):
    f = tmp_path / "history.json"
    monkeypatch.setattr(lp_system, "HISTORY_FILE", f)
    return f


def _snap(**kw):
    base = dict(tvl=20000, fees=30, positions_count=2, in_range=2,
                by_wallet={}, by_wallet_fees={})
    base.update(kw)
    lp_system.add_snapshot(**base)
    return json.loads(open(lp_system.HISTORY_FILE).read())["snapshots"][-1]


def test_normal_growth_counted(hist):
    _snap(positions_fees={"bsc:1": 10.0, "bsc:2": 20.0})
    s = _snap(positions_fees={"bsc:1": 12.0, "bsc:2": 25.0})
    assert s["fees_cumulative"] == pytest.approx(7.0)


def test_rpc_blink_does_not_double_count(hist):
    """Кошелёк пропал на прогон и вернулся — дельта от сохранённого, не с нуля."""
    _snap(positions_fees={"bsc:1": 10.0, "bsc:2": 20.0})
    _snap(positions_fees={"bsc:1": 11.0})                    # bsc:2 не загрузился
    s = _snap(positions_fees={"bsc:1": 12.0, "bsc:2": 22.0}) # вернулся
    # честно: (11-10)+(12-11)+(22-20)=4. Старый код добавил бы ~22 фантома
    assert s["fees_cumulative"] == pytest.approx(4.0)


def test_reopen_does_not_inflate(hist):
    """Коридор переоткрыт: старый token_id исчез, новый — baseline с delta 0."""
    _snap(positions_fees={"bsc:1": 30.0})
    s = _snap(positions_fees={"bsc:99": 0.5})   # новая позиция
    assert s["fees_cumulative"] == pytest.approx(0.0)
    s = _snap(positions_fees={"bsc:99": 2.5})
    assert s["fees_cumulative"] == pytest.approx(2.0)


def test_real_harvest_counts_regrowth(hist):
    _snap(positions_fees={"bsc:1": 30.0})
    s = _snap(positions_fees={"bsc:1": 1.5})    # собрали, наросло заново
    assert s["fees_cumulative"] == pytest.approx(1.5)


def test_migration_resets_inflated_legacy(hist):
    """Первый прогон после фикса: legacy-цифра архивируется, счёт с нуля."""
    # старый снапшот без positions_fees_tracking, с надутым cumulative
    lp_system.save_history([{"date": "2026-07-01", "timestamp": "2026-07-01T00:00:00+00:00",
                             "tvl": 21000, "fees": 30, "fees_cumulative": 5999.0}])
    s = _snap(positions_fees={"bsc:1": 30.0})
    assert s["fees_cumulative"] == pytest.approx(0.0)
    assert s["fees_cumulative_legacy"] == pytest.approx(5999.0)


def test_sanity_cap_one_percent(hist):
    _snap(positions_fees={"bsc:1": 10.0})
    s = _snap(positions_fees={"bsc:1": 5000.0})   # глитч цены
    assert s["fees_cumulative"] <= 20000 * 0.01 + 1e-9
