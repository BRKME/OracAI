# 📊 Market Regime Engine v5.4

Probabilistic crypto market regime detection with HODL-first strategy based on backtesting.

## Current Versions

| Component | Version | Status |
|-----------|---------|--------|
| **Market Regime Engine** | **v5.4** | **Production** |
| Signal Policy | v5.4 HODL-first | Production |
| RSI Integration | v1.0 | Production |
| LP Intelligence | v2.0.2 | Production |

## 🆕 What's New in v5.4

### HODL-First Strategy (Based on Backtest)

**Backtest Results (3 years):**
```
Active Trading: +24%
HODL:          +131%
Conclusion:    HODL wins on bull market
```

**New Approach:**
- Default: **HOLD** (no active trading)
- SELL only in **CRISIS** (capital protection)
- BUY only at extreme bottom (RSI<25 + Bottom>70%)
- REDUCE at extreme top (RSI>80 + Top>80%)

### Exposure Recommendations

| Condition | Exposure | Note |
|-----------|----------|------|
| CRISIS | 20% | Минимум. Защита капитала. |
| TAIL | 50% | Сниженная. Высокий риск. |
| BEAR | 60% | Осторожность. |
| BULL + Conf>40% | 100% | Полная. Бычий тренд. |
| Default | 80% | Стандартная. |

### Signal Triggers (Rare!)

| Signal | Trigger | Action |
|--------|---------|--------|
| BUY | RSI<25 AND Bottom>70% | Extreme oversold |
| SELL | CRISIS risk state | Capital protection |
| REDUCE | RSI>80 AND Top>80% | Partial profit taking |
| HOLD | All other cases | Default (HODL wins) |

## Quick Start

```bash
pip install -r requirements.txt
python main.py              # Full analysis
python main.py --dry-run    # No Telegram
python backtest_v5.py       # Backtest (3 years)
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    MARKET REGIME ENGINE v5.4                 │
├─────────────────────────────────────────────────────────────┤
│  Inputs: BTC price, volume, funding, OI, macro, sentiment   │
│  Output: BULL | BEAR | RANGE | TRANSITION + probabilities   │
└──────────────────────────┬──────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  SIGNAL v5.4    │ │  LP INTELLIGENCE │ │   TELEGRAM      │
│  HODL-FIRST     │ │     (v2.0.2)     │ │    OUTPUT       │
├─────────────────┤ ├─────────────────┤ ├─────────────────┤
│ • Default HOLD  │ │ • Vol decompose │ │ • ASCII bars    │
│ • Exposure %    │ │ • Dual risk     │ │ • Probabilities │
│ • BUY at bottom │ │ • LP regimes    │ │ • Data status   │
│ • SELL in CRISIS│ │ • Fee/variance  │ │ • Mobile-ready  │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

## Backtest Results

```
Period: 3 years (1095 days)

PERFORMANCE:
Model Return:  +23.7%
HODL Return:  +131.2%

REGIME ACCURACY:
Overall:       60.1%
BULL calls:    59.0%
BEAR calls:    49.6%

BOTTOM/TOP TIMING:
Bottom accuracy: 65.0% ✅
Top accuracy:    74.4% ✅

CONCLUSION:
→ Model NOT for active trading
→ Model IS for risk management
→ Use exposure recommendations
```

## Output Example

```
🔘 Фаза рынка:
TRANSITION (4d) | Conf. 13%
RSI: 1D=50 | 2H=41→

Режим рынка:
BULL  [###.......] 33%
BEAR  [##........] 15%
TRANS [#####.....] 47%

🔘 Риск:
TAIL [#]

🔘 Позиция:
Рекомендуемая экспозиция: 50%
→ Сниженная. Высокий риск.

🔘 Сигнал Дно-Вершина:
Bottom [###.......] 30%
Top    [#####.....] 50%

📡 DATA STATUS v5.4 OracAi
```

## Documentation

- `docs/SPOT_SIGNAL_POLICY_v5.4.md` — Signal Policy
- `docs/MARKET_REGIME_ENGINE_v3.4.md` — Regime detection
- `docs/LP_INTELLIGENCE_SYSTEM_v2.0.2.md` — LP policy

## License

MIT
