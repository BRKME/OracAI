# SPOT Signal Policy v5.6 — Integrated Logic

## Philosophy

**Backtest proof (3 years, 2023-2026):**
- Bottom timing: 65% accuracy ✅
- Top timing: 74% accuracy ✅
- Active trading loses to HODL

**Conclusion:** Use model for **position sizing** and **timing**, not frequent trading.

## Core Principles

1. **Bottom/Top signals are primary** — proven by backtest
2. **Phase/Cycle modifies action** — EARLY_BULL = buy, LATE_BULL = sell
3. **Risk overrides all** — CRISIS = protect capital
4. **Confidence affects hedge** — low conf + elevated risk = hedge required

## Action Logic (v5.6)

### Priority Order

```
1. CRISIS → ЗАЩИТА (override all)
2. Bottom ≥70% → ПОКУПАТЬ
3. Top ≥70% → ПРОДАВАТЬ
4. Bottom ≥50% + Top <40% → ДОКУПИТЬ
5. Top ≥50% + Bottom <40% → ФИКСИРОВАТЬ
6. Phase modifier (if neutral zone)
7. Default → ДЕРЖАТЬ
```

### Phase-Based Signals

| Phase | Condition | Action |
|-------|-----------|--------|
| EARLY_BULL | bottom ≥30% | 🟡 ДОКУПИТЬ |
| ACCUMULATION | bottom ≥30% | 🟡 ДОКУПИТЬ |
| LATE_BULL | top ≥30% | 🟠 ФИКСИРОВАТЬ |
| DISTRIBUTION | top ≥30% | 🟠 ФИКСИРОВАТЬ |
| CAPITULATION | bottom ≥40% | 🟢 ПОКУПАТЬ |

### Action Table

| Action | Emoji | Trigger | Size |
|--------|-------|---------|------|
| ПОКУПАТЬ | 🟢 | Bottom≥70% OR Capitulation | 25-50% |
| ДОКУПИТЬ | 🟡 | Bottom≥50% OR Early Bull | 10-20% |
| ФИКСИРОВАТЬ | 🟠 | Top≥50% OR Late Bull | 10-20% |
| ПРОДАВАТЬ | 🔴 | Top≥70% | 25-50% |
| ЗАЩИТА | ⚫ | CRISIS | reduce to 20-30% |
| ДЕРЖАТЬ | ⚪ | Neutral zone | 0% |

## Hedge Logic (v5.6)

| Risk State | Confidence | Hedge |
|------------|------------|-------|
| CRISIS | Any | REQUIRED |
| TAIL | Any | REQUIRED |
| ELEVATED | <30% | REQUIRED |
| ELEVATED | ≥30% | recommended |
| NORMAL | Any | optional |

```python
hedge_required = (
    risk_state in ("TAIL", "CRISIS") or
    (risk_state == "ELEVATED" and conf < 30)
)
```

## LP Exposure (v5.6)

Base exposure from LP regime, modified by market regime:

| Condition | Modifier |
|-----------|----------|
| CRISIS | max 10% |
| TAIL | max 30% |
| BULL + conf>30% | +20% |
| BEAR | -20% |

```python
if risk_state == "CRISIS":
    exposure = 10%
elif risk_state == "TAIL":
    exposure = min(30%, base)
elif regime == "BULL" and conf > 30:
    exposure = min(90%, base + 20%)
elif regime == "BEAR":
    exposure = max(10%, base - 20%)
```

## Output Format

```
🔘 Действие: 🟡 ДОКУПИТЬ
→ Фаза EARLY_BULL — начало роста. Добавить 10-20% к позиции. Дно: 40%.

🔘 LP:
BREAKOUT (Q1)
Exposure: 50% | Range: wide
Fees vs IL: 5.0x ✓
Hedge: REQUIRED
```

## Backtest Validation

| Metric | Value | Status |
|--------|-------|--------|
| Bottom detection | 65.0% | ✅ |
| Top detection | 74.4% | ✅ |
| Regime accuracy | 60.1% | ✅ |

## What Changed from v5.4

| v5.4 | v5.6 |
|------|------|
| Action ignores Phase | Action considers Phase |
| Hedge: TAIL/CRISIS only | Hedge: + low conf + elevated |
| LP Exposure: static | LP Exposure: regime-adjusted |
| EARLY_BULL = HOLD | EARLY_BULL = ДОКУПИТЬ |

## Version History

- **v5.6** — Integrated logic (phase + hedge + LP exposure)
- v5.5 — BTC/ETH charts with EMA
- v5.4 — HODL-first based on backtest
- v4.5 — RSI integration

