# 📊 SPOT Signal Policy v4.5

Политика генерации торговых сигналов с учётом позиции в рыночном цикле и RSI.

## Версия

| Component | Version | Status |
|-----------|---------|--------|
| SPOT Signal Policy | v4.5 | Production |
| RSI Integration | v1.0 | Production |
| Cycle Position Engine | v1.0 | Production |
| Telegram UI | v4.5 | Production |

## Ключевая идея

**Не продавать на дне. Не покупать на вершине.**

Сигнал определяется:
1. Направлением рынка (regime)
2. Позицией в цикле (cycle position)
3. **RSI (multi-timeframe)** ← NEW in v4.5

---

## 1. Signal Flow

```
Raw Signal (-30%)
       │
       ▼
┌──────────────────┐
│ × Confidence     │  11% conf → -3% adjusted
│   Adjustment     │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ RSI Adjustment   │  RSI=28 → bottom +25%    ← NEW
│ (cycle position) │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ × Cycle Position │  75% bottom → 0.55 dampener
│   Modifier       │  -3% × 0.55 = -1.6%
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Signal Threshold │  -1.6% < 5% threshold
│   Classification │  → HOLD
└────────┬─────────┘
         │
         ▼
     HOLD (dampened)
```

---

## 2. RSI Integration (NEW in v4.5)

### 2.1 RSI Timeframes

| RSI | Period | Purpose | Source |
|-----|--------|---------|--------|
| **rsi_1d** | Daily RSI-14 | Strategic direction | Binance → Bybit → Yahoo |
| **rsi_2h** | 2-hour RSI-14 | Tactical timing | Binance → Bybit → Yahoo |
| **rsi_1d_7** | Daily RSI-7 | Momentum | Binance → Bybit → Yahoo |

### 2.2 API Fallback Chain

```
Binance SPOT API (no auth, no geo-restrictions)
       │
       ▼ (if fails)
Bybit SPOT API
       │
       ▼ (if fails)
Yahoo Finance (always works)
```

### 2.3 RSI Impact on Cycle Position

RSI корректирует bottom/top proximity **до** применения cycle modifier:

| RSI Daily | Bottom Proximity | Top Proximity |
|-----------|-----------------|---------------|
| ≤ 25 (deeply oversold) | **+25%** | -20% |
| ≤ 35 (oversold) | **+15%** | -10% |
| 65-74 (overbought) | -10% | **+15%** |
| ≥ 75 (deeply overbought) | -20% | **+25%** |

**Пример:**
```
Regime: BEAR 39 days
Base bottom_prox: 50%
RSI Daily: 28 (oversold)
→ Adjusted bottom_prox: 50% + 25% = 75%
```

### 2.4 RSI Display

```
RSI: 🟢 1D=28 | 2H=35→
```

| Icon | Meaning |
|------|---------|
| 🟢 | RSI ≤30 (oversold) |
| 🔴 | RSI ≥70 (overbought) |
| ⚪ | RSI neutral (30-70) |
| ↓ | 2H RSI < 40 (falling) |
| → | 2H RSI 40-60 (neutral) |
| ↑ | 2H RSI > 60 (rising) |

### 2.5 RSI in Reasons

```
Reasons:
  • 🟢 RSI oversold (28) — покупка выгоднее
  • 🔴 RSI overbought (78) — продажа выгоднее
```

---

## 3. Confidence Adjustment

Сырой сигнал умножается на уверенность модели:

```
Adjusted Signal = Raw Signal × Confidence
```

**Пример:**
- Raw: SELL -30%
- Confidence: 11%
- Adjusted: -30% × 0.11 = **-3%**

---

## 4. Cycle Position Modifier

### 4.1 Bottom Proximity + SELL → Dampen/Invert

| Bottom % | Modifier | Effect |
|----------|----------|--------|
| < 50% | 1.0 | No change |
| 50% | 0.8 | Dampen 20% |
| 60% | 0.7 | Dampen 30% |
| 70% | 0.6 | Dampen 40% |
| **≥ 70%** | **INVERT** | **SELL → small BUY** |

**Формула:**
```python
if bottom_prox >= 0.5 and signal < 0:
    dampener = 1.0 - (bottom_prox - 0.3)  # 0.5 → 0.8, 0.7 → 0.6
    
    if bottom_prox >= 0.7:
        # INVERT: flip to small BUY
        cycle_adjusted = min(0.05, abs(signal) * 0.3)
    else:
        cycle_adjusted = signal * dampener
```

### 4.2 Top Proximity + BUY → Dampen/Invert

| Top % | Modifier | Effect |
|-------|----------|--------|
| < 50% | 1.0 | No change |
| 50% | 0.8 | Dampen 20% |
| 60% | 0.7 | Dampen 30% |
| 70% | 0.6 | Dampen 40% |
| **≥ 70%** | **INVERT** | **BUY → small SELL** |

---

## 5. Signal Thresholds

После всех модификаторов, финальный сигнал классифицируется:

| Adjusted Size | Signal | Action |
|---------------|--------|--------|
| ≤ -15% | STRONG_SELL | Агрессивно сокращать |
| -15% to -5% | SELL | Сокращать позицию |
| **-5% to +5%** | **HOLD** | **Ничего не делать** |
| +5% to +15% | BUY | Наращивать позицию |
| ≥ +15% | STRONG_BUY | Агрессивно наращивать |

**Важно:** ±5% — это шум. Не стоит торговать.

---

## 6. Cycle Position Estimation

Когда CoinGecko API недоступен, используем данные из Regime Engine + RSI:

### 6.1 BEAR Regime

| Days | Risk Level | Phase | Base Bottom | RSI Adj |
|------|------------|-------|-------------|---------|
| > 30 | < -0.5 | CAPITULATION | 70%+ | + RSI adj |
| > 14 | any | MID_BEAR | 50%+ | + RSI adj |
| ≤ 14 | any | EARLY_BEAR | 30% | + RSI adj |

### 6.2 BULL Regime

| Days | Risk Level | Phase | Base Top | RSI Adj |
|------|------------|-------|----------|---------|
| > 30 | > 0.5 | LATE_BULL | 70%+ | + RSI adj |
| > 14 | any | MID_BULL | 50% | + RSI adj |
| ≤ 14 | any | EARLY_BULL | 30% | + RSI adj |

---

## 7. Data Status (NEW in v4.5)

Отображает недоступные критические данные:

```
📡 DATA STATUS
  ⚠️ RSI Daily недоступен (Binance/Bybit/Yahoo)
  ⚠️ RSI 2h недоступен
  ⚠️ Нет данных: Funding, OI
  ℹ️ Нет: FRED
```

### Priority

| Source | Priority | Icon |
|--------|----------|------|
| RSI Daily | Critical | ⚠️ |
| RSI 2h | Important | ⚠️ |
| BTC Price | Critical | ⚠️ |
| Fear & Greed | Critical | ⚠️ |
| Funding | Critical | ⚠️ |
| OI | Critical | ⚠️ |
| FRED | Non-critical | ℹ️ |
| Yahoo macro | Non-critical | ℹ️ |

---

## 8. Output Format

```
📊 SPOT SIGNAL
┌─────────────────────────────┐
│ ⬇️ STRONG SELL              │
│ ⬇️ SELL                     │
│ ➡️ HOLD              ◀──── │
│ ⬆️ BUY                      │
│ ⬆️ STRONG BUY               │
└─────────────────────────────┘

Phase: MID_BEAR ~ (conf: 11%)
Cycle: [██░░░░░░░░] 25/100

RSI: 🟢 1D=28 | 2H=35→

Bottom ░░░░▓▓▓▓▓▓ 75% ~
Top    ░░░▓░░░░░░ 10% ~

BTC: HOLD (dampened)
ETH: HOLD (dampened)

Reasons:
  • ⚠️ SELL близко к дну (75%) — сигнал ослаблен
  • 🟢 RSI oversold (28) — покупка выгоднее
  • Затяжной медвежий тренд

📡 DATA STATUS
  ℹ️ Нет: FRED

v4.5 · RSI:binance
```

### Markers

| Marker | Meaning |
|--------|---------|
| `~` | Estimated (not from CoinGecko API) |
| `(dampened)` | Signal weakened by cycle position |
| `(⚠️ cycle override)` | Signal inverted by cycle position |
| `(signal weak: -3%)` | Below threshold after confidence adjustment |
| `RSI:binance` | RSI data source |

---

## 9. Examples

### Example 1: SELL at Bottom with Low RSI → HOLD

**Input:**
- Raw signal: SELL -30%
- Confidence: 11%
- RSI Daily: 28 (oversold)
- Base bottom proximity: 50%

**Calculation:**
```
Adjusted = -30% × 11% = -3%
RSI adjustment: bottom_prox = 50% + 25% = 75%
Dampener = 1.0 - (0.75 - 0.3) = 0.55
Cycle-adjusted = -3% × 0.55 = -1.6%
-1.6% is within ±5% → HOLD
```

**Output:**
```
BTC: HOLD (dampened)
Reasons:
  • ⚠️ SELL близко к дну (75%) — сигнал ослаблен
  • 🟢 RSI oversold (28) — покупка выгоднее
```

### Example 2: BUY at Top with High RSI → HOLD

**Input:**
- Raw signal: BUY +25%
- Confidence: 60%
- RSI Daily: 78 (overbought)
- Base top proximity: 50%

**Calculation:**
```
Adjusted = +25% × 60% = +15%
RSI adjustment: top_prox = 50% + 25% = 75%
Top ≥ 70% → INVERT
Cycle-adjusted = max(-0.05, -0.15 × 0.3) = -4.5%
-4.5% is within ±5% → HOLD
```

**Output:**
```
BTC: HOLD (⚠️ cycle override)
Reasons:
  • ⚠️ BUY на вершине (75%) — сигнал инвертирован
  • 🔴 RSI overbought (78) — продажа выгоднее
```

### Example 3: Strong Signal with Confirming RSI

**Input:**
- Raw signal: SELL -30%
- Confidence: 70%
- RSI Daily: 72 (overbought)
- Top proximity: 65%

**Calculation:**
```
Adjusted = -30% × 70% = -21%
RSI adjustment: top_prox = 65% + 15% = 80% (confirms SELL)
No dampening for SELL when near top
Cycle-adjusted = -21%
-21% ≤ -15% → STRONG_SELL
```

**Output:**
```
BTC: STRONG_SELL -21%
Reasons:
  • 🔴 RSI overbought (72) — продажа выгоднее
```

---

## 10. Changelog

### v4.5 (2026-03-06)
- ✅ **RSI Integration**
  - Multi-timeframe: 1D, 2h, 1D-7
  - Binance → Bybit → Yahoo fallback chain
  - RSI adjusts cycle position (±25%)
  - RSI display in output
  - RSI in reasons
- ✅ **Data Status Section**
  - Shows missing critical data
  - Priority levels (⚠️ critical, ℹ️ non-critical)
- ✅ **Footer shows data sources**

### v4.4 (2026-03-06)
- ✅ RSI data fetching (Binance/Bybit/Yahoo)
- ✅ RSI in engine metadata

### v4.3 (2026-03-06)
- ✅ Cycle Position Modifier
- ✅ Dampen SELL at bottom
- ✅ Dampen BUY at top
- ✅ Invert signals at extreme positions (≥70%)

### v4.2 (2026-03-06)
- ✅ Signal based on ADJUSTED values (not raw)
- ✅ Threshold ±5% for HOLD zone

### v4.1 (2026-03-06)
- ✅ Smart fallback for cycle position
- ✅ Estimate from regime data when API unavailable

### v4.0 (2026-03-06)
- ✅ Initial SPOT SIGNAL block
- ✅ Visual signal scale
- ✅ Phase & Cycle position display
