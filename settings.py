"""
Market Regime Engine v3.3 — All weights, thresholds, and configuration.

Every tunable parameter lives here. No magic numbers in engine code.
"""

# ============================================================
# NORMALIZATION
# ============================================================

NORM_WINDOW_DEFAULT = 90
NORM_WINDOW_MIN = 30
NORM_WINDOW_MACRO = 180
Z_CLIP = 3.0

# Structural break detection
BREAK_VARIANCE_RATIO_THRESHOLD = 2.5
BREAK_T_STAT_THRESHOLD = 3.0
BREAK_SHORT_WINDOW = 30
BREAK_LONG_WINDOW = 60

# ============================================================
# MOMENTUM BUCKET WEIGHTS
# ============================================================

MOMENTUM_WEIGHTS = {
    "ROC_blend": 0.35,
    "Trend_strength": 0.25,
    "Alignment": 0.20,
    "DTMC": 0.20,
}

ROC_BLEND_30D_WEIGHT = 0.6
ROC_BLEND_90D_WEIGHT = 0.4

# Anti-decay
MOMENTUM_RELATIVE_WEIGHT = 0.75
MOMENTUM_ABSOLUTE_WEIGHT = 0.25
ABSOLUTE_ROC_THRESHOLD = 0.50  # 50% in 90d = extreme

# EMA periods for alignment
EMA_FAST = 20
EMA_MEDIUM = 50
EMA_SLOW = 200

# ADX
ADX_PERIOD = 14

# ============================================================
# STABILITY BUCKET WEIGHTS
# ============================================================

STABILITY_WEIGHTS = {
    "Vol": 0.40,
    "Liquidity": 0.35,
    "Depth": 0.25,
}

STABILITY_WEIGHTS_FALLBACK = {
    "Vol": 0.50,
    "Liquidity": 0.50,
}

REALIZED_VOL_WINDOW = 30

# ============================================================
# ROTATION BUCKET WEIGHTS
# ============================================================

ROTATION_VELOCITY_WEIGHT = 0.6
ROTATION_ACCEL_WEIGHT = 0.4

# Context adjustment
ROTATION_CONTEXT_MOMENTUM_THRESHOLD = 0.3
ROTATION_BULL_DAMPENING = 0.6
ROTATION_BEAR_DAMPENING = 0.4

# ============================================================
# SENTIMENT BUCKET WEIGHTS
# ============================================================

SENTIMENT_WEIGHTS = {
    "FG": 0.35,
    "Funding": 0.40,
    "OI": 0.25,
}

FG_ZONES = {
    (0, 25): -1.0,    # Extreme Fear
    (26, 45): -0.5,   # Fear
    (46, 55): 0.0,    # Neutral
    (56, 75): 0.5,    # Greed
    (76, 100): 1.0,   # Extreme Greed
}

# ============================================================
# MACRO BUCKET WEIGHTS
# ============================================================

MACRO_WEIGHTS = {
    "Dollar": 0.30,
    "Rate": 0.25,
    "YieldCurve": 0.20,
    "M2": 0.25,
}

MACRO_STALE_DAYS = 7
MACRO_STALE_PENALTY = 0.7
MACRO_EXPIRED_DAYS = 30

# ============================================================
# CROSS-ASSET
# ============================================================

CROSS_ASSET_CORR_WINDOW = 30
MACRO_BOOST_THRESHOLD = 0.6
MACRO_BOOST_MULTIPLIER = 1.3

# ============================================================
# LOGIT WEIGHTS
# ============================================================

LOGIT_BULL = {
    "Momentum": 1.2,
    "Stability": 0.5,
    "Rotation": -0.4,
    "Sentiment": 0.2,
    "Macro": 0.3,
}

LOGIT_BEAR = {
    "Momentum": -1.2,
    "Stability": -0.5,
    "Rotation": 0.4,
    "Sentiment": -0.2,
    "Macro": -0.3,
}

LOGIT_RANGE = {
    "abs_Momentum": -0.8,
    "Stability": 0.7,
    "abs_Vol_z": -0.3,
    "abs_Rotation": -0.3,
    "abs_Macro": -0.2,
}

LOGIT_TRANSITION = {
    "Vol_z": 0.7,
    "flip_signal": 1.0,
    "abs_dMacro_7d": 0.3,
}

# ============================================================
# SOFTMAX TEMPERATURE (f(Vol_z), no cycle)
# ============================================================

TEMPERATURE_MAP = [
    (2.0, 1.5),   # Vol_z > 2.0 → T = 1.5
    (1.0, 1.2),   # Vol_z > 1.0 → T = 1.2
    (0.0, 1.0),   # Vol_z > 0.0 → T = 1.0
    (float('-inf'), 0.8),  # else T = 0.8
]

# ============================================================
# EMA SMOOTHING
# ============================================================

EMA_ALPHA_MAP = [
    (1.5, 0.5),   # Vol_z > 1.5 → α = 0.5
    (0.5, 0.3),   # Vol_z > 0.5 → α = 0.3
    (float('-inf'), 0.2),  # else α = 0.2
]

# ============================================================
# REGIME SWITCHING — ASYMMETRIC (v3.3)
# ============================================================

REGIME_CONFIRMATION = {
    "BULL": {
        # v3.4 (Phase 4): was 0.65/3/0.22/2 — asymmetric re-entry killed alpha
        # Phase 3 backtest showed engine defended 2022 bear ($100k vs $55k HODL)
        # but failed to re-risk during 2023-2025 recovery ($140k→$190k vs $90k→$400k HODL).
        # Root cause: BULL needed 65% consensus for 3 days; BEAR only 55% for 1 day.
        # Fix: make BULL symmetric with BEAR — if the evidence is good enough to call
        # BEAR quickly, it's good enough to call BULL quickly too.
        "consensus_threshold": 0.55,
        "consensus_days": 1,
        "leader_delta": 0.18,
        "leader_days": 1,
    },
    "BEAR": {
        "consensus_threshold": 0.55,
        "consensus_days": 1,
        "leader_delta": 0.18,
        "leader_days": 1,
    },
    "RANGE": {
        "consensus_threshold": 0.60,
        "consensus_days": 2,
        "leader_delta": 0.20,
        "leader_days": 1,
    },
    "TRANSITION": {
        # v3.4: was 0.55/1/0.18/1 — same as BEAR made TRANSITION too easy to enter.
        # Backtest showed 49% of days in TRANSITION with mean exposure_cap=0.21.
        # Tighten entry so TRANSITION only wins when it's genuinely ambiguous,
        # not a fallback from weak BULL confidence.
        "consensus_threshold": 0.60,
        "consensus_days": 2,
        "leader_delta": 0.20,
        "leader_days": 1,
    },
}

# ============================================================
# CONFIDENCE
# ============================================================

QUALITY_WEIGHTS = {
    "completeness": 0.30,
    "consistency": 0.25,
    "persistence": 0.25,
    "macro_agreement": 0.20,
}

PERSISTENCE_NORMALIZATION_DAYS = 7.0
PERSISTENCE_MIN = 0.3

SENTIMENT_EXTREME_THRESHOLD = 0.8
SENTIMENT_EXTREME_PENALTY = 0.85

DECOUPLED_CORR_THRESHOLD = 0.3
DECOUPLED_BONUS = 1.05
CONFIDENCE_CAP = 0.95

# Churn penalty (v3.3)
CHURN_WINDOW = 30
CHURN_FREE_SWITCHES = 2
CHURN_PENALTY_PER_SWITCH = 0.10
CHURN_FLOOR = 0.50

# ============================================================
# FLIP SIGNAL
# ============================================================

FLIP_MAJOR_THRESHOLD = 0.50
FLIP_MODERATE_THRESHOLD = 0.30
FLIP_DECAY = 0.7
FLIP_LOOKBACK = 3  # days

# ============================================================
# EDGE CASES
# ============================================================

DATA_QUALITY_MIN = 0.85
DATA_QUALITY_PENALTY = 0.5

MISSING_DAYS_FREEZE = 2
MISSING_DAYS_EMERGENCY = 5
EMERGENCY_CONFIDENCE = 0.30
EMERGENCY_EXPOSURE = 0.30

EXTREME_VOL_THRESHOLD = 2.5
EXTREME_VOL_TRANSITION_FLOOR = 0.40
EXTREME_VOL_CONFIDENCE_PENALTY = 0.7

FLASH_CRASH_THRESHOLD = -0.15  # -15% in 1h
FLASH_RECOVERY_CHECK_HOURS = [4, 8, 12, 24]
FLASH_RECOVERY_THRESHOLD = 0.50
FLASH_CRASH_REAL_THRESHOLD = 0.20

# ============================================================
# EXPOSURE CAP
# ============================================================

EXPOSURE_MAP = {
    "BULL": {
        # v3.4 iteration 3 (Phase 4): thresholds lowered.
        # Observation from 5y backtest: mean confidence was 0.20 and only 24% of
        # days crossed 0.30. With previous thresholds (0.60/0.35/0) even high-
        # conviction days rarely hit "high_conf". Calibrate thresholds to match
        # actual confidence distribution seen in prod.
        "high_conf": (0.35, 1.00),   # conf > 0.35 → 100%
        "med_conf": (0.18, 0.85),    # conf > 0.18 → 85%
        "low_conf": (0.0, 0.70),     # low conf BULL → 70%
    },
    "BEAR": {
        # Unchanged — defensive mechanism works, don't touch
        "high_conf": (0.70, 0.30),
        "med_conf": (0.50, 0.40),
        "low_conf": (0.0, 0.50),
    },
    "RANGE": {
        "high_conf": (0.70, 0.60),
        "med_conf": (0.50, 0.50),
        "low_conf": (0.0, 0.35),
    },
    "TRANSITION": {
        # v3.4 iteration 3: same thresholds as BULL for consistency.
        "high_conf": (0.35, 0.75),
        "med_conf": (0.18, 0.60),
        "low_conf": (0.0, 0.50),
    },
}

# ============================================================
# BUCKET HEALTH
# ============================================================

BUCKET_HEALTH_LOOKBACK = 60
LOW_DIMENSIONALITY_THRESHOLD = 3
HIGH_CORR_THRESHOLD = 0.75

EXPECTED_CORRELATIONS = {
    ("Momentum", "Stability"): -0.3,
    ("Momentum", "Rotation"): -0.1,
    ("Momentum", "Sentiment"): 0.3,
    ("Momentum", "Macro"): 0.2,
    ("Stability", "Rotation"): 0.0,
    ("Stability", "Sentiment"): 0.1,
    ("Stability", "Macro"): 0.1,
    ("Rotation", "Sentiment"): 0.0,
    ("Rotation", "Macro"): 0.1,
    ("Sentiment", "Macro"): 0.2,
}

CORR_ANOMALY_THRESHOLD = 0.5

# ============================================================
# TRANSITION MATRIX
# ============================================================

TRANSITION_WINDOW = 180
TRANSITION_STICKY_THRESHOLD = 0.60
DIRECT_BULL_BEAR_THRESHOLD = 0.10
RARE_TRANSITION_MULTIPLIER = 10

# ============================================================
# RISK LEVEL (policy layer over regime detection)
# ============================================================

# Weights: Regime → Risk contribution
# Design: v3.4 — TRANSITION weight changed from -1.00 to -0.20.
# Previous design treated TRANSITION as more bearish than BEAR itself, which
# misrepresents what TRANSITION actually means. TRANSITION = "we're not sure",
# not "crash imminent". In the 5y backtest this was the single largest alpha
# killer: 49% of days in TRANSITION → risk_level always ≤ -0.25 → exposure
# capped at 20% → missed the entire 2023-2025 bull run.
# New design: TRANSITION = mild bearish bias (uncertainty default), BEAR =
# strong bearish (actual signal), BULL = strong positive.
# Sum at uniform P=[0.25]*4 = +0.025 (Neutral) — appropriate for ambiguity.
RISK_LEVEL_WEIGHTS = {
    "BULL": +0.80,
    "RANGE": +0.40,
    "BEAR": -0.90,
    "TRANSITION": -0.20,
}

# Risk state thresholds
RISK_ON_THRESHOLD = +0.30
RISK_OFF_THRESHOLD = -0.30

# Confidence gate: if confidence < this, risk_level capped at 0 (Neutral)
# Prevents false Risk-On when model is uncertain
RISK_CONFIDENCE_GATE = 0.15

# Risk Level → Exposure override (takes priority over regime-based exposure)
# v3.4 iteration 5: relaxed. Previous config capped Risk-Neutral at 50%, which
# meant any BULL regime with risk_level < +0.30 (most of the bull run days)
# was forced to 50% max — making regime-based caps in EXPOSURE_MAP irrelevant.
# New logic: risk_cap only fires hard when risk_level is genuinely bearish.
# Neutral zone gives 80% (let regime-based cap do its job). Risk-On gives 100%.
RISK_EXPOSURE_MAP = [
    (-1.0, -0.60, 0.15),   # deep Risk-Off → 15%
    (-0.60, -0.30, 0.35),  # Risk-Off → 35%
    (-0.30, +0.10, 0.70),  # mildly bearish neutral → 70%
    (+0.10, +0.30, 0.85),  # mildly bullish neutral → 85%
    (+0.30, +0.60, 1.00),  # Risk-On → full
    (+0.60, +1.01, 1.00),  # Strong Risk-On → full
]

# Risk Level labels
RISK_STATE_LABELS = {
    "RISK_ON": "🟢 RISK-ON",
    "RISK_NEUTRAL": "🟡 RISK-NEUTRAL",
    "RISK_OFF": "🔴 RISK-OFF",
}

# ============================================================
# REGIMES LIST
# ============================================================

REGIMES = ["BULL", "BEAR", "RANGE", "TRANSITION"]


# ============================================================
# LP POLICY ENGINE v2.0.1 — Configuration
# ============================================================

# ── Trend Persistence Thresholds ──────────────────────────
# v2.0.2 — Earlier trend detection
LP_PERSISTENCE_CHOPPY = 0.25       # Was 0.20
LP_PERSISTENCE_MODERATE = 0.35    # Was 0.40
LP_PERSISTENCE_TRENDING = 0.45    # Was 0.50

# ── Uncertainty Inversion ─────────────────────────────────
LP_UNCERTAINTY_REGIME_MULT = {
    "BULL": 0.8,
    "BEAR": 0.8,
    "RANGE": 1.0,
    "TRANSITION": 1.2,  # Uncertainty is premium for LP
}

# ── Fee/Variance Ratio Thresholds ─────────────────────────
LP_FV_HIGHLY_PROFITABLE = 3.0
LP_FV_PROFITABLE = 2.0
LP_FV_MARGINAL = 1.5
LP_FV_UNPROFITABLE = 1.0

# ── Fee Regime Multipliers ────────────────────────────────
LP_FEE_REGIME_MULT = {
    "ELEVATED": 1.5,
    "NORMAL": 1.0,
    "DEPRESSED": 0.6,
}

LP_FEE_REGIME_CONTRIB = {
    "ELEVATED": +0.5,
    "NORMAL": 0,
    "DEPRESSED": -0.5,
}

# ── Vol Structure Contribution to risk_lp ─────────────────
LP_VOL_STRUCTURE_CONTRIB = {
    "RANGE_DOMINANT": +0.5,
    "BALANCED": +0.1,
    "LOW_VOL": -0.2,
    "JUMP_ELEVATED": -0.3,
    "TREND_DOMINANT": -0.5,
}

# ── risk_lp Computation Weights ───────────────────────────
LP_RISK_WEIGHTS = {
    "vol_structure": 0.25,
    "trend_persistence": 0.25,
    "uncertainty_value": 0.20,
    "fee_variance": 0.20,
    "fee_regime": 0.10,
}

# ── Churn Detection ───────────────────────────────────────
LP_CHURN_SWITCHES = 5  # switches in 30d to trigger CHURN

# ── LP Regime Parameters ──────────────────────────────────
# v2.0.2 — More conservative in trends, based on CFO backtest
LP_REGIME_PARAMS = {
    "HARVEST": {"notional": 0.80, "range": "tight", "rebalance": "aggressive"},      # Reduced from 0.90
    "MEAN_REVERT": {"notional": 0.60, "range": "standard", "rebalance": "normal"},   # Reduced from 0.70
    "VOLATILE_CHOP": {"notional": 0.50, "range": "moderate", "rebalance": "active"}, # Reduced from 0.80
    "TRENDING": {"notional": 0.10, "range": "wide", "rebalance": "minimal"},         # Reduced from 0.30
    "BREAKOUT": {"notional": 0.20, "range": "wide", "rebalance": "cautious"},        # Reduced from 0.40
    "CHURN": {"notional": 0.05, "range": "very_wide", "rebalance": "disabled"},      # Reduced from 0.10
    "GAP_RISK": {"notional": 0.25, "range": "wide", "rebalance": "cautious"},        # Reduced from 0.50
    "AVOID": {"notional": 0.00, "range": "n/a", "rebalance": "disabled"},
}

# ── LP Regime Emoji ───────────────────────────────────────
LP_REGIME_EMOJI = {
    "HARVEST": "🌾",
    "MEAN_REVERT": "🔄",
    "VOLATILE_CHOP": "⚡",
    "TRENDING": "📉",
    "BREAKOUT": "⚠️",
    "CHURN": "🚫",
    "GAP_RISK": "🕳",
    "AVOID": "🛑",
}

# ── Risk Quadrant Descriptions ────────────────────────────
LP_QUADRANT_DESC = {
    "Q1": "Q1: Both favorable",
    "Q2": "Q2: LP opportunity ✨",
    "Q3": "Q3: Prefer spot",
    "Q4": "Q4: Defensive",
}

# ============================================================
# ASSET ALLOCATION POLICY v1.3.1 — Configuration
# ============================================================

# ── Confidence Gates ──────────────────────────────────────
AA_CONF_NO_ACTION = 0.40      # Below this: HOLD only
AA_CONF_ACTION = 0.50         # Above this: BUY/SELL allowed
AA_CONF_STRONG_SELL = 0.60    # Above this: STRONG_SELL allowed
AA_CONF_STRONG_BUY = 0.70     # Above this: STRONG_BUY allowed

# ── Momentum Thresholds ───────────────────────────────────
AA_MOM_STRONG = 0.50          # Threshold for STRONG actions
AA_MOM_WEAK = 0.0             # Threshold for regular actions

# ── Risk Thresholds ───────────────────────────────────────
AA_RISK_TRANSITION_SELL = -0.30  # TRANSITION + risk below this = SELL

# ── Anti-Churn Protection ─────────────────────────────────
AA_MAX_ACTIONS_30D = 3              # Max non-HOLD actions per 30 days
AA_COOLDOWN_BUY_AFTER_SELL = 7      # Days to wait for BUY after SELL
AA_COOLDOWN_SELL_AFTER_BUY = 3      # Days to wait for SELL after BUY
AA_COOLDOWN_STRONG = 14             # Days between STRONG actions

# ── Position Sizes (BTC) ──────────────────────────────────
AA_SIZE_BTC_STRONG_BUY = +0.20      # +20% of portfolio
AA_SIZE_BTC_BUY = +0.10             # +10% of portfolio
AA_SIZE_BTC_SELL = -0.15            # -15% of position
AA_SIZE_BTC_STRONG_SELL = -0.50     # -50% of position

# ── Position Sizes (ETH) ──────────────────────────────────
AA_SIZE_ETH_BUY = +0.05             # +5% of portfolio
AA_SIZE_ETH_SELL = -0.20            # -20% of position
AA_SIZE_ETH_STRONG_SELL = -0.70     # -70% of position

# ── Action Emoji ──────────────────────────────────────────
AA_ACTION_EMOJI = {
    "STRONG_BUY": "🟢🟢",
    "BUY": "🟢",
    "HOLD": "⚪️",
    "SELL": "🔴",
    "STRONG_SELL": "🔴🔴",
}
