# 📊 Market Regime Engine v4.3

Probabilistic crypto market regime detection with LP intelligence, asset allocation, and cycle-aware signals.

## Current Versions

| Component | Version | Status |
|-----------|---------|--------|
| Market Regime Engine | v3.4 | Production |
| **SPOT Signal Policy** | **v4.3** | **Production** |
| LP Intelligence | v2.0.2 | Production |
| Asset Allocation | v1.4.1 | Production |

## 🆕 What's New in v4.3

### Cycle Position Modifier

**Не продавать на дне. Не покупать на вершине.**

| Situation | Action |
|-----------|--------|
| SELL + Bottom ≥50% | Dampen signal |
| SELL + Bottom ≥70% | **Invert to BUY** |
| BUY + Top ≥50% | Dampen signal |
| BUY + Top ≥70% | **Invert to SELL** |

```
Raw: SELL -30%
× Confidence 11% = -3%
× Cycle dampener 0.67 (63% bottom) = -2%
→ HOLD (dampened)
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run full analysis
python main.py

# Dry run (no Telegram)
python main.py --dry-run

# Backtest
python backtest.py
```

## Documentation

📚 All documentation is in the `/docs` folder:

### Core
- **[SPOT_SIGNAL_POLICY_v4.3.md](docs/SPOT_SIGNAL_POLICY_v4.3.md)** — SPOT Signal Policy (latest) ⭐
- **[MARKET_REGIME_ENGINE_v3.4.md](docs/MARKET_REGIME_ENGINE_v3.4.md)** — Regime detection
- **[ASSET_ALLOCATION_POLICY_v1.4.1.md](docs/ASSET_ALLOCATION_POLICY_v1.4.1.md)** — Asset allocation

### LP
- **[LP_INTELLIGENCE_SYSTEM_v2.0.2.md](docs/LP_INTELLIGENCE_SYSTEM_v2.0.2.md)** — LP policy
- **[lp_hedge_policy.md](docs/lp_hedge_policy.md)** — LP hedging

### Full Specifications
- [MARKET_REGIME_ENGINE_v3.3.md](docs/MARKET_REGIME_ENGINE_v3.3.md)
- [LP_INTELLIGENCE_SYSTEM_v2.0.1.md](docs/LP_INTELLIGENCE_SYSTEM_v2.0.1.md)
- [ASSET_ALLOCATION_POLICY_v1_4.md](docs/ASSET_ALLOCATION_POLICY_v1_4.md)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    MARKET REGIME ENGINE                      │
│                         (v3.4)                               │
├─────────────────────────────────────────────────────────────┤
│  Inputs: BTC price, volume, funding, OI, macro, sentiment   │
│  Output: BULL | BEAR | RANGE | TRANSITION + probabilities   │
└──────────────────────────┬──────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  SPOT SIGNAL    │ │  LP INTELLIGENCE │ │   TELEGRAM      │
│    (v4.3)       │ │     (v2.0.2)     │ │    OUTPUT       │
├─────────────────┤ ├─────────────────┤ ├─────────────────┤
│ • Cycle modifier│ │ • Vol decompose │ │ • Visual scales │
│ • Don't sell    │ │ • Dual risk     │ │ • Probabilities │
│   at bottom     │ │ • LP regimes    │ │ • Reasons       │
│ • Conf adjust   │ │ • Fee/variance  │ │                 │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

## Key Features

### SPOT Signal Policy (v4.3)

**Signal Flow:**
```
Raw Signal → × Confidence → × Cycle Modifier → Threshold → Final Signal
```

**Thresholds:**
| Range | Signal |
|-------|--------|
| ≤ -15% | STRONG_SELL |
| -15% to -5% | SELL |
| -5% to +5% | HOLD |
| +5% to +15% | BUY |
| ≥ +15% | STRONG_BUY |

### Regime Detection
- 4 regimes: BULL, BEAR, RANGE, TRANSITION
- Probabilistic classification with confidence scoring
- Structural break detection

### Asset Allocation (v1.4 Counter-Cyclical)
- **Don't sell panic**: Blocks SELL when momentum < -0.70 AND vol_z > 1.5
- **Buy fear**: Accumulate on extreme panic + deep drawdown
- **Sell greed**: Take profit on euphoria + big rally

### LP Intelligence
- Volatility decomposition (trend/range/jump)
- Dual risk model (directional vs LP-specific)
- 8 LP regimes with specific policies

## Output Example (v4.3)

```
BULL ─── RANGE ─── TRANSITION ─── BEAR
                                   ▲

🔴 BEAR (39d)
[█░░░░░░░░░] 11%
↓ Downside pressure. Dir: ↓ 0.64

Regime probabilities:
BULL       █░░░░░░░░░░░ 11%
BEAR       ████░░░░░░░░ 34%
RANGE      █░░░░░░░░░░░ 8%
TRANSITION █████░░░░░░░ 45%

→ Повышенный структурный риск...

⚠️ RISK SCALE
NORMAL ─── ELEVATED ─── TAIL ─── CRISIS
                                ▲

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

🔵 LP: Good, but hedge needed
  Exposure: 40% | Range: wide
  Fees vs IL: 2.5x ✓
  Hedge: REQUIRED
  → LP профитабелен, но высокий направленный риск

v4.3
```

## Backtest Results

```
Metric              v1.3.1    v1.4    Improvement
─────────────────────────────────────────────────
Sells at bottom     39%       13%     -26% ✅
Sells at top        0%        14%     +14% ✅
Buys at bottom      3%        5%      +2%
```

## GitHub Actions Setup

### 1. Fork this repo

### 2. Set GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|--------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your chat/group ID |
| `FRED_API_KEY` | FRED API key (optional, for macro data) |

### 3. Enable GitHub Actions

The engine runs at **07:00 UTC** and **19:00 UTC** daily.

## Data Sources (all free)

| Data | Source | Auth |
|------|--------|------|
| BTC price, volume | Yahoo Finance / Binance | None |
| Market cap, BTC.D | CoinGecko | None |
| Fear & Greed | alternative.me | None |
| Funding, OI | Binance | None |
| DXY, SPX, Gold | Yahoo Finance | None |
| US Treasury, M2 | FRED | Free key |

## License

MIT
