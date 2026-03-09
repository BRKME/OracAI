# 📊 Market Regime Engine v5.7

Probabilistic crypto market regime detection with integrated action logic.

## Current Versions

| Component | Version | Status |
|-----------|---------|--------|
| **Market Regime Engine** | **v5.7** | **Production** |
| Signal Policy | v5.6 Integrated | Production |
| Charts | EMA50/200 + RSI | Production |
| LP Intelligence | v2.0.2 | Production |
| **Twitter Publisher** | **v1.0** | **New** |

## 🆕 What's New in v5.7

### Twitter Publisher

Automated Twitter posting with BTC chart (2x daily):

```
🟢 #BTC BULL | $95,234
📊 Bull 68% · Bear 12%
📈 Early Bull (Day 5)
🟡 Consider adding

#Bitcoin #Crypto #Trading
```

**Schedule:** 08:00 MSK + 20:00 MSK

**Features:**
- BTC-only chart (EMA50/200 + RSI)
- English format for global reach
- Optimized hashtags for visibility
- No LP info (spot trading focus)

### Integrated Action Logic

Action now considers **Phase/Cycle**, not just Bottom/Top:

| Phase | Condition | Action |
|-------|-----------|--------|
| EARLY_BULL | bottom ≥30% | 🟡 ДОКУПИТЬ |
| ACCUMULATION | bottom ≥30% | 🟡 ДОКУПИТЬ |
| LATE_BULL | top ≥30% | 🟠 ФИКСИРОВАТЬ |
| DISTRIBUTION | top ≥30% | 🟠 ФИКСИРОВАТЬ |
| CAPITULATION | bottom ≥40% | 🟢 ПОКУПАТЬ |

### Smart Hedge Logic

| Risk | Confidence | Hedge |
|------|------------|-------|
| CRISIS/TAIL | Any | REQUIRED |
| ELEVATED | <30% | REQUIRED |
| ELEVATED | ≥30% | recommended |
| NORMAL | Any | optional |

### LP Exposure with Regime Modifier

| Condition | Modifier |
|-----------|----------|
| BULL + conf>30% | +20% |
| BEAR | -20% |
| CRISIS | max 10% |
| TAIL | max 30% |

### Charts (BTC + ETH)

- Daily timeframe, 1 year view
- EMA50 (orange) + EMA200 (red)
- RSI panel with 30/70 zones

## Quick Start

```bash
pip install -r requirements.txt
python main.py              # Full analysis + charts (Telegram)
python twitter_publisher.py # Twitter post with BTC chart
python main.py --dry-run    # No Telegram
python backtest_v5.py       # Backtest (3 years)
```

### Twitter Setup

Add these secrets to GitHub:
- `TWITTER_API_KEY`
- `TWITTER_API_SECRET`
- `TWITTER_ACCESS_TOKEN`
- `TWITTER_ACCESS_TOKEN_SECRET`

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    MARKET REGIME ENGINE v5.6                 │
├─────────────────────────────────────────────────────────────┤
│  Inputs: BTC price, volume, funding, OI, macro, sentiment   │
│  Output: BULL | BEAR | RANGE | TRANSITION + probabilities   │
└──────────────────────────┬──────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  SIGNAL v5.6    │ │  LP INTELLIGENCE │ │   TELEGRAM      │
│  INTEGRATED     │ │     (v2.0.2)     │ │    OUTPUT       │
├─────────────────┤ ├─────────────────┤ ├─────────────────┤
│ • Phase-aware   │ │ • Vol decompose │ │ • BTC/ETH charts│
│ • Smart hedge   │ │ • Regime-adj exp│ │ • ASCII bars    │
│ • Bottom/Top    │ │ • LP regimes    │ │ • Mobile-ready  │
│ • Cycle signals │ │ • Fee/variance  │ │ • Data status   │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

## Action Priority

```
1. CRISIS → ⚫ ЗАЩИТА
2. Bottom ≥70% → 🟢 ПОКУПАТЬ
3. Top ≥70% → 🔴 ПРОДАВАТЬ
4. Bottom ≥50% → 🟡 ДОКУПИТЬ
5. Top ≥50% → 🟠 ФИКСИРОВАТЬ
6. Phase modifier (EARLY_BULL, etc.)
7. Default → ⚪ ДЕРЖАТЬ
```

## Backtest Results

```
Period: 3 years (1095 days)

TIMING ACCURACY:
Bottom: 65.0% ✅
Top:    74.4% ✅

REGIME ACCURACY:
Overall: 60.1%
BULL:    59.0%
BEAR:    49.6%

USE FOR:
✅ Position sizing
✅ Bottom/Top timing
✅ Risk management
❌ Active trading
```

## Output Example

```
🔘 Фаза рынка:
BULL (2d) | Conf. 45%
RSI: 1D=50 | 2H=61↑

Режим рынка:
BULL  [######....] 68%
BEAR  [#.........] 10%
TRANS [#.........] 17%

Цикл: EARLY_BULL [####......] 45%
→ Начало восходящего тренда. Первые покупки.

🔘 Риск:
ELEV [#]

🔘 Действие: 🟡 ДОКУПИТЬ
→ Фаза EARLY_BULL — начало роста. Добавить 10-20% к позиции.

🔘 Сигнал Дно-Вершина:
Bottom [####......] 40%
Top    [###.......] 30%

🔘 LP:
BREAKOUT (Q1)
Exposure: 50% | Range: wide
Hedge: REQUIRED

📡 DATA STATUS v5.6 OracAi
```

## Documentation

- `docs/SPOT_SIGNAL_POLICY_v5.6.md` — Signal Policy
- `docs/BACKTEST_RESULTS_v5.4.md` — Backtest analysis
- `docs/LP_INTELLIGENCE_SYSTEM_v2.0.2.md` — LP policy

## Version History

- **v5.7** — Twitter Publisher for global reach
- **v5.6** — Integrated logic (phase + hedge + LP exposure)
- v5.5 — BTC/ETH charts with EMA
- v5.4 — HODL-first strategy
- v5.3 — Mobile-friendly ASCII bars

## License

MIT
