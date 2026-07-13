"""Математика uncollected fees — фикс 07.07.2026 (фантом $1,783.90 на
arbitrum:5571457, «к сбору» превысил «всего»). Off-chain канон: отрицательная
или завёрнутая дельта fee-growth = 0 accrued (не выдумывать деньги)."""
import pytest
from lp_monitor import calculate_uncollected_fees

Q128 = 2 ** 128


def test_normal_accrual():
    t0, t1 = calculate_uncollected_fees(
        liquidity=10**18,
        fee_growth_inside0=5 * Q128 // 10**18 * 2,
        fee_growth_inside1=0,
        fee_growth_inside0_last=0, fee_growth_inside1_last=0,
        tokens_owed0=0, tokens_owed1=0, decimals0=18, decimals1=18)
    assert 0 < t0 < 100 and t1 == 0


def test_negative_delta_clamped_to_zero_not_exploded():
    """Кейс 07.07: insideLast > inside -> раньше accrued ~= liquidity."""
    t0, _ = calculate_uncollected_fees(
        liquidity=5 * 10**17,
        fee_growth_inside0=100, fee_growth_inside1=0,
        fee_growth_inside0_last=10**30, fee_growth_inside1_last=0,
        tokens_owed0=0, tokens_owed1=0, decimals0=18, decimals1=18)
    assert t0 == 0.0, f"фантом не зажат: {t0}"


def test_tokens_owed_pass_through():
    t0, t1 = calculate_uncollected_fees(
        liquidity=0, fee_growth_inside0=0, fee_growth_inside1=0,
        fee_growth_inside0_last=0, fee_growth_inside1_last=0,
        tokens_owed0=3 * 10**18, tokens_owed1=15 * 10**17,
        decimals0=18, decimals1=18)
    assert t0 == pytest.approx(3.0) and t1 == pytest.approx(1.5)


def test_huge_wrapped_delta_also_clamped():
    t0, _ = calculate_uncollected_fees(
        liquidity=10**18,
        fee_growth_inside0=(0 - 5) % (2 ** 256), fee_growth_inside1=0,
        fee_growth_inside0_last=0, fee_growth_inside1_last=0,
        tokens_owed0=0, tokens_owed1=0, decimals0=18, decimals1=18)
    assert t0 == 0.0


def test_large_legit_delta_small_liquidity_not_clamped():
    """Регрессия 13.07: WETH-стороны обеих arbitrum-позиций обнулялись.
    Дельта = fees-per-unit-liquidity * 2^128; у 18-децимальных токенов при
    небольшой концентрированной ликвидности легитимная дельта > Q128
    (напр. 50 wei/unit -> 50*Q128). Сторожок 'delta>=Q128 -> 0' резал
    реальные комиссии. Настоящие защиты — wrap-кламп (>=Q255) и USD-пояс."""
    liq = 10 ** 14
    delta = 50 * Q128            # 50 wei комиссий на единицу ликвидности
    t0, _ = calculate_uncollected_fees(
        liquidity=liq,
        fee_growth_inside0=delta, fee_growth_inside1=0,
        fee_growth_inside0_last=0, fee_growth_inside1_last=0,
        tokens_owed0=0, tokens_owed1=0, decimals0=18, decimals1=18)
    assert t0 == pytest.approx(liq * 50 / 10**18)  # 0.005 WETH, не ноль
