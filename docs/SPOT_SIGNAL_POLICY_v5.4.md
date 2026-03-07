# SPOT Signal Policy v5.4 — HODL-First

## Philosophy

**Backtest proof (3 years, 2023-2026):**
```
Active Trading: +24%
HODL:          +131%
Alpha:         -107%
```

**Conclusion:** Active trading loses to HODL. Use model for **risk management**, not trading.

## Core Principles

1. **Default = HOLD** — Do nothing most of the time
2. **Signals are rare** — Only at extremes
3. **Exposure management** — Reduce position in risk, not exit
4. **Protect capital** — SELL only in CRISIS

## Signal Logic

### Exposure Recommendations (Primary Output)

| Risk State | Regime | Exposure | Note |
|------------|--------|----------|------|
| CRISIS | Any | 20% | Минимум. Защита капитала. |
| TAIL | Any | 50% | Сниженная. Высокий риск. |
| Any | BEAR | 60% | Осторожность в медвежьем рынке. |
| Any | BULL + Conf>40% | 100% | Полная. Бычий тренд. |
| Default | Default | 80% | Стандартная. |

### Trade Signals (Rare!)

| Signal | Trigger | Frequency |
|--------|---------|-----------|
| **BUY** | RSI<25 AND Bottom>70% | ~5-10x per year |
| **SELL** | CRISIS risk state | ~2-5x per year |
| **REDUCE** | RSI>80 AND Top>80% | ~3-7x per year |
| **HOLD** | All other cases | 95% of time |

## Signal Function

```python
def get_signal_v2(regime, risk_state, rsi, bottom_prox, top_prox, conf):
    # Default: HOLD (HODL wins long-term)
    signal = "HOLD"
    
    # SELL only in CRISIS (risk management)
    if risk_state == "CRISIS":
        signal = "SELL"
    
    # BUY only at extreme bottom
    elif rsi < 25 and bottom_prox > 0.7:
        signal = "BUY"
    
    # Reduce at extreme top
    elif rsi > 80 and top_prox > 0.8:
        signal = "REDUCE"
    
    return signal
```

## Output Format

### Standard (No Signal)

```
🔘 Позиция:
Рекомендуемая экспозиция: 80%
→ Стандартная.
```

### With Signal (Rare)

```
🔘 Позиция:
Рекомендуемая экспозиция: 50%
→ Сниженная. Высокий риск.

⚡ Сигнал: BTC=BUY ETH=BUY
→ Экстремальная перепроданность. Точка входа.
```

## Backtest Validation

### Regime Accuracy

| Metric | Value | Status |
|--------|-------|--------|
| Overall | 60.1% | ✅ >55% |
| BULL calls | 59.0% | ✅ |
| BEAR calls | 49.6% | ⚠️ |

### Timing Accuracy

| Metric | Value | Status |
|--------|-------|--------|
| Bottom detection | 65.0% | ✅ Good |
| Top detection | 74.4% | ✅ Good |

### Risk Warnings

| Metric | Value | Status |
|--------|-------|--------|
| TAIL/CRISIS before crash | 55% | ⚠️ Needs work |
| False positives | High | ⚠️ |

## What Changed from v4.5

| v4.5 | v5.4 |
|------|------|
| Active BUY/SELL signals | HOLD by default |
| Thresholds: -15%/-5%/+5%/+15% | Only extremes trigger |
| 5 signal levels | 4 signals (BUY/SELL/REDUCE/HOLD) |
| Always shows signal | Shows signal only when triggered |
| No exposure recommendation | Exposure % is primary output |

## Integration

### Telegram Output

```python
# Calculate recommended exposure
if risk_state == "CRISIS":
    exposure = "20%"
elif risk_state == "TAIL":
    exposure = "50%"
elif regime == "BEAR":
    exposure = "60%"
elif regime == "BULL" and conf > 40:
    exposure = "100%"
else:
    exposure = "80%"

# Show signal only if not HOLD
if signal != "HOLD":
    lines.append(f"⚡ Сигнал: BTC={signal}")
```

### User Action

| Output | User Action |
|--------|-------------|
| Exposure: 100% | Full position, no action |
| Exposure: 80% | Maintain position |
| Exposure: 50% | Reduce by ~30-40% |
| Exposure: 20% | Move to stables |
| Signal: BUY | DCA entry point |
| Signal: REDUCE | Take partial profit |

## Version History

- **v5.4** — HODL-first based on backtest
- v4.5 — RSI integration, cycle modifier
- v4.3 — Don't sell at bottom
- v4.0 — Initial signal policy
