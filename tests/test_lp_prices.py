"""Цена токена в LP не может молча стать нулём.

12.11.2026: CoinGecko перестал отдавать цену UNI на BSC (пустой ответ).
Код подставлял 0, и ~600 UNI (~$3,600) пропали из TVL: отчёт показал
«−$3,900 за день», хотя оператор ничего не выводил.
"""
import pytest

import lp_monitor as M


class _Resp:
    def __init__(self, code, data):
        self.status_code, self._d = code, data

    def json(self):
        return self._d


UNI = "0xbf5140a22578168fd562dccf235e5d43a02ce9b1"


def test_defillama_fallback_when_coingecko_empty(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        if "coingecko" in url:
            return _Resp(200, {})
        assert "coins.llama.fi" in url and f"bsc:{UNI}" in url
        return _Resp(200, {"coins": {f"bsc:{UNI}": {"price": 6.0}}})
    monkeypatch.setattr(M.requests, "get", fake_get)
    assert M.PriceService().get_price("binance-smart-chain", UNI) == pytest.approx(6.0)


def test_zero_price_is_not_cached(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        return _Resp(200, {})
    monkeypatch.setattr(M.requests, "get", fake_get)
    ps = M.PriceService()
    assert ps.get_price("binance-smart-chain", UNI) == 0
    n = calls["n"]
    ps.get_price("binance-smart-chain", UNI)
    assert calls["n"] > n                      # спросил снова, а не взял 0 из кэша


def test_price_from_pool_when_one_side_missing():
    # WBNB (18) / UNI (18): 1 WBNB = 109 UNI -> UNI = 654 / 109 = 6.0
    p = 109.0
    sqrt_x96 = int((p ** 0.5) * 2 ** 96)
    p0, p1 = M.fill_missing_price(654.0, 0.0, sqrt_x96, 18, 18)
    assert p0 == pytest.approx(654.0) and p1 == pytest.approx(6.0, rel=1e-6)
    p0, p1 = M.fill_missing_price(0.0, 6.0, sqrt_x96, 18, 18)
    assert p0 == pytest.approx(654.0, rel=1e-6)


def test_pool_price_respects_decimals():
    # WETH (18) / USDC (6): 1 WETH = 2000 USDC
    raw = 2000.0 * 10 ** (6 - 18)
    sqrt_x96 = int((raw ** 0.5) * 2 ** 96)
    p0, p1 = M.fill_missing_price(0.0, 1.0, sqrt_x96, 18, 6)
    assert p0 == pytest.approx(2000.0, rel=1e-6)


def test_both_missing_stays_zero():
    assert M.fill_missing_price(0.0, 0.0, 2 ** 96, 18, 18) == (0.0, 0.0)
