# 📊 SPOT Signal Policy v4.3

Политика генерации торговых сигналов с учётом позиции в рыночном цикле.

## Версия

| Component | Version | Status |
|-----------|---------|--------|
| SPOT Signal Policy | v4.3 | Production |
| Cycle Position Engine | v1.0 | Production |
| Telegram UI | v4.3 | Production |

## Ключевая идея

**Не продавать на дне. Не покупать на вершине.**

Сигнал определяется не только направлением рынка, но и позицией в цикле:
- SELL на дне → ослабить или инвертировать
- BUY на вершине → ослабить или инвертировать

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
│ × Cycle Position │  63% bottom → 0.67 dampener
│   Modifier       │  -3% × 0.67 = -2%
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Signal Threshold │  -2% < 5% threshold
│   Classification │  → HOLD
└────────┬─────────┘
         │
         ▼
     HOLD (dampened)
```

---

## 2. Confidence Adjustment

Сырой сигнал умножается на уверенность модели:

```
Adjusted Signal = Raw Signal × Confidence
```

**Пример:**
- Raw: SELL -30%
- Confidence: 11%
- Adjusted: -30% × 0.11 = **-3%**

---

## 3. Cycle Position Modifier

### 3.1 Bottom Proximity + SELL → Dampen/Invert

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

### 3.2 Top Proximity + BUY → Dampen/Invert

| Top % | Modifier | Effect |
|-------|----------|--------|
| < 50% | 1.0 | No change |
| 50% | 0.8 | Dampen 20% |
| 60% | 0.7 | Dampen 30% |
| 70% | 0.6 | Dampen 40% |
| **≥ 70%** | **INVERT** | **BUY → small SELL** |

**Формула:**
```python
if top_prox >= 0.5 and signal > 0:
    dampener = 1.0 - (top_prox - 0.3)
    
    if top_prox >= 0.7:
        # INVERT: flip to small SELL
        cycle_adjusted = max(-0.05, -abs(signal) * 0.3)
    else:
        cycle_adjusted = signal * dampener
```

---

## 4. Signal Thresholds

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

## 5. Cycle Position Estimation

Когда CoinGecko API недоступен, используем данные из Regime Engine:

### 5.1 BEAR Regime

| Days | Risk Level | Phase | Bottom % | Top % | Cycle |
|------|------------|-------|----------|-------|-------|
| > 30 | < -0.5 | CAPITULATION | 70%+ | 10% | 15/100 |
| > 14 | any | MID_BEAR | 50%+ | 20% | 25/100 |
| ≤ 14 | any | EARLY_BEAR | 30% | 40% | 35/100 |

### 5.2 BULL Regime

| Days | Risk Level | Phase | Bottom % | Top % | Cycle |
|------|------------|-------|----------|-------|-------|
| > 30 | > 0.5 | LATE_BULL | 10% | 70%+ | 85/100 |
| > 14 | any | MID_BULL | 20% | 50% | 65/100 |
| ≤ 14 | any | EARLY_BULL | 40% | 30% | 45/100 |

### 5.3 TRANSITION Regime

| Risk Level | Phase | Bottom % | Top % |
|------------|-------|----------|-------|
| < -0.3 | DISTRIBUTION | 30% | 50% |
| > 0.3 | ACCUMULATION | 50% | 30% |
| else | TRANSITION | 40% | 40% |

### 5.4 RANGE Regime

| Phase | Bottom % | Top % |
|-------|----------|-------|
| RANGE | 35% | 35% |

---

## 6. Output Format

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

Bottom ░░░░▓▓▓▓▓▓ 63% ~
Top    ░░░░░░░░▓▓ 20% ~

BTC: HOLD (dampened)
ETH: HOLD (dampened)

Reasons:
  • ⚠️ SELL близко к дну (63%) — сигнал ослаблен
  • Затяжной медвежий тренд
  • Низкая уверенность модели (11%)
```

### Markers

| Marker | Meaning |
|--------|---------|
| `~` | Estimated (not from CoinGecko API) |
| `(dampened)` | Signal weakened by cycle position |
| `(⚠️ cycle override)` | Signal inverted by cycle position |
| `(signal weak: -3%)` | Below threshold after confidence adjustment |

---

## 7. Examples

### Example 1: SELL at Bottom → HOLD

**Input:**
- Raw signal: SELL -30%
- Confidence: 11%
- Bottom proximity: 63%

**Calculation:**
```
Adjusted = -30% × 11% = -3%
Dampener = 1.0 - (0.63 - 0.3) = 0.67
Cycle-adjusted = -3% × 0.67 = -2%
-2% is within ±5% → HOLD
```

**Output:**
```
BTC: HOLD (dampened)
Reasons: ⚠️ SELL близко к дну (63%) — сигнал ослаблен
```

### Example 2: SELL at Deep Bottom → BUY

**Input:**
- Raw signal: SELL -30%
- Confidence: 50%
- Bottom proximity: 75%

**Calculation:**
```
Adjusted = -30% × 50% = -15%
Bottom ≥ 70% → INVERT
Cycle-adjusted = min(0.05, 0.15 × 0.3) = +4.5%
+4.5% is within ±5% → HOLD (borderline BUY)
```

**Output:**
```
BTC: HOLD (⚠️ cycle override)
Reasons: ⚠️ SELL на дне (75%) — сигнал инвертирован
```

### Example 3: Strong SELL with High Confidence

**Input:**
- Raw signal: SELL -30%
- Confidence: 70%
- Bottom proximity: 20%

**Calculation:**
```
Adjusted = -30% × 70% = -21%
Bottom < 50% → no dampening
Cycle-adjusted = -21%
-21% ≤ -15% → STRONG_SELL
```

**Output:**
```
BTC: STRONG_SELL -21%
```

### Example 4: BUY at Top → HOLD

**Input:**
- Raw signal: BUY +25%
- Confidence: 60%
- Top proximity: 72%

**Calculation:**
```
Adjusted = +25% × 60% = +15%
Top ≥ 70% → INVERT
Cycle-adjusted = max(-0.05, -0.15 × 0.3) = -4.5%
-4.5% is within ±5% → HOLD
```

**Output:**
```
BTC: HOLD (⚠️ cycle override)
Reasons: ⚠️ BUY на вершине (72%) — сигнал инвертирован
```

---

## 8. Integration with Other Components

### 8.1 Regime Engine

Получает:
- `regime`: BULL | BEAR | RANGE | TRANSITION
- `risk_level`: -1 to +1
- `confidence`: 0 to 1
- `days_in_regime`: int
- `vol_z`: volatility z-score

### 8.2 Asset Allocation

Получает:
- `btc.size_pct`: raw signal
- `eth.size_pct`: raw signal

### 8.3 Cycle Position Engine (optional)

Если доступен CoinGecko API:
- `CycleMetrics`: ATH, MA, RSI, Fear&Greed
- `CyclePosition`: phase, bottom_prox, top_prox

---

## 9. Changelog

### v4.3 (2026-03-01)
- ✅ Cycle Position Modifier
- ✅ Dampen SELL at bottom
- ✅ Dampen BUY at top
- ✅ Invert signals at extreme positions (≥70%)
- ✅ Visual indicators: `(dampened)`, `(⚠️ cycle override)`

### v4.2 (2026-03-01)
- ✅ Signal based on ADJUSTED values (not raw)
- ✅ Threshold ±5% for HOLD zone

### v4.1 (2026-03-01)
- ✅ Smart fallback for cycle position
- ✅ Estimate from regime data when API unavailable
- ✅ `~` marker for estimated values

### v4.0 (2026-03-01)
- ✅ Initial SPOT SIGNAL block
- ✅ Visual signal scale
- ✅ Phase & Cycle position display
- ✅ Bottom/Top proximity bars
