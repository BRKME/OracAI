"""
Regime Engine v3.3 — Core detection logic.

Pipeline:
  data → buckets → logits → softmax(T) → EMA → switch → confidence → output
"""

import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import settings as cfg
from normalization import AdaptiveNormalizer
from buckets import (
    compute_momentum, compute_stability, compute_rotation,
    compute_sentiment, compute_macro, compute_cross_asset,
    compute_realized_vol, rolling_correlation
)

logger = logging.getLogger(__name__)

STATE_DIR = Path("state")
STATE_FILE = STATE_DIR / "engine_state.json"


# ============================================================
# LOGIT COMPUTATION
# ============================================================

def compute_logits(M, S, R, Sent, Mac, vol_z, macro_boost, bucket_history):
    """Compute raw logits for all 4 regimes."""
    wB = cfg.LOGIT_BULL
    wBR = cfg.LOGIT_BEAR
    wR = cfg.LOGIT_RANGE
    wT = cfg.LOGIT_TRANSITION

    bull = (
        wB["Momentum"] * M +
        wB["Stability"] * S +
        wB["Rotation"] * R +
        wB["Sentiment"] * Sent +
        wB["Macro"] * Mac * macro_boost
    )

    bear = (
        wBR["Momentum"] * M +
        wBR["Stability"] * S +
        wBR["Rotation"] * R +
        wBR["Sentiment"] * Sent +
        wBR["Macro"] * Mac * macro_boost
    )

    rng = (
        wR["abs_Momentum"] * abs(M) +
        wR["Stability"] * S +
        wR["abs_Vol_z"] * abs(vol_z) +
        wR["abs_Rotation"] * abs(R) +
        wR["abs_Macro"] * abs(Mac)
    )

    # Flip signal from bucket values (v3.2 fix: no feedback loop)
    flip = compute_flip_signal(bucket_history)

    # Macro delta (7d)
    macro_hist = bucket_history.get("Macro", [])
    if len(macro_hist) >= 7:
        d_macro_7d = abs(macro_hist[-1] - macro_hist[-7])
    else:
        d_macro_7d = 0.0

    trans = (
        wT["Vol_z"] * vol_z +
        wT["flip_signal"] * flip +
        wT["abs_dMacro_7d"] * d_macro_7d
    )

    return {"BULL": bull, "BEAR": bear, "RANGE": rng, "TRANSITION": trans}


def compute_flip_signal(bucket_history: dict) -> float:
    """
    Regime flip signal from bucket values (NOT probabilities).
    v3.2/v3.3: no feedback loop.
    """
    lookback = cfg.FLIP_LOOKBACK

    keys = ["Momentum", "Stability", "Rotation"]
    for k in keys:
        if len(bucket_history.get(k, [])) < lookback + 1:
            return 0.0

    current = np.array([bucket_history[k][-1] for k in keys])
    prior = np.array([np.mean(bucket_history[k][-(lookback + 1):-1]) for k in keys])

    max_delta = float(np.max(np.abs(current - prior)))

    if max_delta > cfg.FLIP_MAJOR_THRESHOLD:
        signal = 1.0
    elif max_delta > cfg.FLIP_MODERATE_THRESHOLD:
        signal = 0.5
    else:
        signal = 0.0

    return signal


# ============================================================
# SOFTMAX + TEMPERATURE
# ============================================================

def adaptive_temperature(vol_z: float) -> float:
    """Temperature from Vol_z (external, no cycle). v3.2 fix."""
    for threshold, temp in cfg.TEMPERATURE_MAP:
        if vol_z > threshold:
            return temp
    return 1.0


def softmax(logits: dict, temperature: float) -> dict:
    """Softmax with temperature scaling."""
    vals = np.array([logits[r] for r in cfg.REGIMES])
    vals = vals / temperature
    vals -= np.max(vals)  # numerical stability
    exp_vals = np.exp(vals)
    probs = exp_vals / np.sum(exp_vals)
    return {r: float(probs[i]) for i, r in enumerate(cfg.REGIMES)}


# ============================================================
# EMA SMOOTHING
# ============================================================

def adaptive_alpha(vol_z: float) -> float:
    """EMA alpha from Vol_z."""
    for threshold, alpha in cfg.EMA_ALPHA_MAP:
        if vol_z > threshold:
            return alpha
    return 0.2


def smooth_probabilities(P_new: dict, P_prev: Optional[dict], alpha: float) -> dict:
    """EMA smoothing of regime probabilities."""
    if P_prev is None:
        return P_new
    return {
        r: (1 - alpha) * P_prev.get(r, 0.25) + alpha * P_new[r]
        for r in cfg.REGIMES
    }


# ============================================================
# REGIME SWITCHING — ASYMMETRIC (v3.3)
# ============================================================

def should_switch(P: dict, current_regime: str, holds_for: int) -> Optional[str]:
    """
    Asymmetric dual-threshold switching.
    Returns new regime or None.
    """
    new_regime = max(P, key=P.get)
    if new_regime == current_regime:
        return None

    conf = cfg.REGIME_CONFIRMATION[new_regime]

    # Path 1: Strong consensus
    if P[new_regime] > conf["consensus_threshold"] and holds_for >= conf["consensus_days"]:
        return new_regime

    # Path 2: Clear leadership
    delta = P[new_regime] - P.get(current_regime, 0)
    if delta > conf["leader_delta"] and holds_for >= conf["leader_days"]:
        return new_regime

    return None


# ============================================================
# CONFIDENCE — with churn penalty (v3.3)
# ============================================================

def compute_confidence(P: dict, quality: float, sentiment_value: float,
                       corr_spx: float, corr_gold: float,
                       regime_switch_log: list) -> dict:
    """Quality-adjusted confidence based on regime gap."""
    # Gap-based confidence (v5.6)
    # Gap between top-1 and top-2 regime better reflects certainty
    sorted_probs = sorted(P.values(), reverse=True)
    gap = sorted_probs[0] - sorted_probs[1]  # e.g., 70% - 14% = 56%
    
    # Scale gap to confidence
    # gap=20% → conf=30%, gap=40% → conf=60%, gap=60% → conf=90%
    base = min(0.90, gap * 1.5)
    base = max(0.10, base)  # floor at 10%

    # Quality adjustment
    adjusted = base * quality

    # Sentiment extreme
    if abs(sentiment_value) > cfg.SENTIMENT_EXTREME_THRESHOLD:
        adjusted *= cfg.SENTIMENT_EXTREME_PENALTY

    # Decoupled bonus
    if (abs(corr_spx) < cfg.DECOUPLED_CORR_THRESHOLD and
            abs(corr_gold) < cfg.DECOUPLED_CORR_THRESHOLD):
        adjusted *= cfg.DECOUPLED_BONUS
        adjusted = min(adjusted, cfg.CONFIDENCE_CAP)

    # Churn penalty (v3.3)
    switches_30d = count_switches(regime_switch_log, cfg.CHURN_WINDOW)
    if switches_30d > cfg.CHURN_FREE_SWITCHES:
        excess = switches_30d - cfg.CHURN_FREE_SWITCHES
        churn = max(cfg.CHURN_FLOOR, 1.0 - cfg.CHURN_PENALTY_PER_SWITCH * excess)
    else:
        churn = 1.0
    adjusted *= churn

    return {
        "base": round(base, 4),
        "quality_adjusted": round(adjusted, 4),
        "churn_penalty": round(churn, 4),
        "switches_30d": switches_30d,
    }


def compute_signal_quality(momentum: float, stability: float, macro: float,
                           days_in_regime: int, data_completeness: float) -> float:
    """Signal quality metric."""
    completeness = data_completeness

    # We don't have running correlation here, use sign agreement as proxy
    # Expect: momentum up → stability down (negative correlation)
    if (momentum > 0 and stability < 0) or (momentum < 0 and stability > 0):
        consistency = 0.7  # Expected pattern
    elif abs(momentum) < 0.1 or abs(stability) < 0.1:
        consistency = 0.5  # Ambiguous
    else:
        consistency = 0.2  # Anomalous

    persistence = min(1.0, max(cfg.PERSISTENCE_MIN,
                               days_in_regime / cfg.PERSISTENCE_NORMALIZATION_DAYS))

    # Macro agreement
    if abs(macro) < 0.1 or abs(momentum) < 0.1:
        macro_agr = 0.5
    elif np.sign(momentum) == np.sign(macro):
        macro_agr = min(1.0, min(abs(momentum), abs(macro)) + 0.3)
    else:
        macro_agr = 0.2

    w = cfg.QUALITY_WEIGHTS
    quality = (
        w["completeness"] * completeness +
        w["consistency"] * consistency +
        w["persistence"] * persistence +
        w["macro_agreement"] * macro_agr
    )

    return float(np.clip(quality, 0.0, 1.0))


def count_switches(log: list, window: int) -> int:
    """Count regime switches in last N days."""
    if len(log) < 2:
        return 0
    recent = log[-window:]
    switches = sum(1 for i in range(1, len(recent)) if recent[i] != recent[i - 1])
    return switches


# ============================================================
# EXPOSURE CAP
# ============================================================

def compute_exposure_cap(regime: str, confidence: float) -> float:
    """Exposure cap based on regime and confidence."""
    caps = cfg.EXPOSURE_MAP.get(regime, cfg.EXPOSURE_MAP["TRANSITION"])
    for level in ["high_conf", "med_conf", "low_conf"]:
        threshold, cap = caps[level]
        if confidence > threshold:
            return cap
    return 0.20


# ============================================================
# RISK LEVEL — policy layer (v3.3.1)
# ============================================================

def compute_risk_level(P: dict, confidence: float) -> dict:
    """
    Continuous risk level from regime probabilities.
    
    risk_level ∈ [-1.0, +1.0]
      > +0.30  → Risk-On
      -0.30…+0.30 → Risk-Neutral
      < -0.30  → Risk-Off
    
    Confidence gate: if confidence < threshold, cap at Neutral (0.0).
    This prevents false Risk-On when the model is uncertain.
    """
    w = cfg.RISK_LEVEL_WEIGHTS
    
    raw = sum(P.get(regime, 0) * weight for regime, weight in w.items())
    risk_level = float(np.clip(raw, -1.0, 1.0))
    
    # Confidence gate: low confidence → cannot be Risk-On
    gated = False
    if confidence < cfg.RISK_CONFIDENCE_GATE and risk_level > 0.0:
        risk_level = 0.0
        gated = True
    
    # Classify
    if risk_level > cfg.RISK_ON_THRESHOLD:
        risk_state = "RISK_ON"
    elif risk_level < cfg.RISK_OFF_THRESHOLD:
        risk_state = "RISK_OFF"
    else:
        risk_state = "RISK_NEUTRAL"
    
    # Strength label
    abs_level = abs(risk_level)
    if abs_level > 0.70:
        strength = "extreme"
    elif abs_level > 0.50:
        strength = "strong"
    elif abs_level > 0.30:
        strength = "moderate"
    else:
        strength = "mild"
    
    # Risk-driven exposure cap
    risk_exposure = 0.50  # default
    for lo, hi, cap in cfg.RISK_EXPOSURE_MAP:
        if lo <= risk_level < hi:
            risk_exposure = cap
            break
    
    # Build reason list
    reasons = []
    if P.get("TRANSITION", 0) > 0.35:
        reasons.append("TRANSITION dominant")
    if P.get("BEAR", 0) > 0.35:
        reasons.append("BEAR pressure")
    if P.get("BULL", 0) > 0.50:
        reasons.append("BULL confirmed")
    if gated:
        reasons.append("confidence gate active")
    
    return {
        "risk_level": round(risk_level, 4),
        "risk_state": risk_state,
        "strength": strength,
        "risk_exposure_cap": risk_exposure,
        "confidence_gated": gated,
        "reasons": reasons,
    }


# ============================================================
# OPERATIONAL HINTS (v3.3)
# ============================================================

def operational_hints(regime: str, stability: float, vol_z: float,
                      momentum: float, days_in_regime: int) -> dict:
    """Strategy-level guidance per regime."""
    if regime == "BULL":
        return {
            "strategy_class": "directional",
            "suggested_lp_mode": "wide_range_trend_following",
            "rebalance_urgency": "low",
        }
    elif regime == "BEAR":
        return {
            "strategy_class": "capital_preservation",
            "suggested_lp_mode": "stablecoin_only_or_exit",
            "rebalance_urgency": "high",
        }
    elif regime == "RANGE":
        if stability > 0.5 and abs(vol_z) < 0.5:
            rtype = "STABLE_RANGE"
            lp_mode = "tight_range_concentrated"
        elif stability > 0.0 and abs(vol_z) < 1.0:
            rtype = "NORMAL_RANGE"
            lp_mode = "moderate_range"
        else:
            rtype = "VOLATILE_RANGE"
            lp_mode = "wide_range_or_skip"

        hints = {
            "strategy_class": "mean_reversion",
            "suggested_lp_mode": lp_mode,
            "range_type": rtype,
            "rebalance_urgency": "low",
        }
        if days_in_regime > 30:
            hints["duration_warning"] = "extended_range_30d"
        if abs(momentum) > 0.25:
            hints["breakout_proximity"] = "ELEVATED"
            hints["breakout_direction"] = "up" if momentum > 0 else "down"
        return hints
    else:  # TRANSITION
        return {
            "strategy_class": "defensive",
            "suggested_lp_mode": "reduce_or_exit",
            "rebalance_urgency": "high",
        }


# ============================================================
# BUCKET HEALTH MONITOR (v3.3)
# ============================================================

def bucket_health(bucket_history: dict, lookback: int = None) -> dict:
    """PCA-based dimensionality + pairwise correlations."""
    lookback = lookback or cfg.BUCKET_HEALTH_LOOKBACK
    names = ["Momentum", "Stability", "Rotation", "Sentiment", "Macro"]

    # Check we have enough history
    min_len = min(len(bucket_history.get(n, [])) for n in names)
    if min_len < 20:
        return {"effective_dimensionality": len(names), "flags": [],
                "pairwise_correlations": {}}

    # Build matrix
    data = np.column_stack([
        np.array(bucket_history[n][-lookback:])[:min_len]
        for n in names
    ])

    # Pairwise correlations
    corr_matrix = np.corrcoef(data.T)
    pairwise = {}
    flags = []

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            key = f"{names[i]}/{names[j]}"
            c = float(corr_matrix[i][j])
            pairwise[key] = round(c, 3)

            # Check for anomalies
            expected = cfg.EXPECTED_CORRELATIONS.get((names[i], names[j]), 0.0)
            if abs(c - expected) > cfg.CORR_ANOMALY_THRESHOLD:
                flags.append(f"ANOMALOUS_CORR_{names[i]}_{names[j]}: {c:.2f}")

            if abs(c) > cfg.HIGH_CORR_THRESHOLD:
                flags.append(f"REDUNDANCY_{names[i]}_{names[j]}: |corr|={abs(c):.2f}")

    # Effective dimensionality via eigenvalues
    try:
        eigenvalues = np.abs(np.linalg.eigvals(corr_matrix))
        eigenvalues = np.sort(eigenvalues)[::-1]
        cumsum = np.cumsum(eigenvalues) / np.sum(eigenvalues)
        eff_dim = int(np.searchsorted(cumsum, 0.90)) + 1
    except Exception:
        eff_dim = len(names)

    if eff_dim < cfg.LOW_DIMENSIONALITY_THRESHOLD:
        flags.append(f"LOW_DIMENSIONALITY: {eff_dim}/{len(names)}")

    return {
        "effective_dimensionality": eff_dim,
        "pairwise_correlations": pairwise,
        "flags": flags,
    }


# ============================================================
# TRANSITION MATRIX (v3.3)
# ============================================================

def compute_transition_matrix(regime_log: list, window: int = None) -> dict:
    """Empirical transition matrix from regime history."""
    window = window or cfg.TRANSITION_WINDOW
    recent = regime_log[-window:] if len(regime_log) > window else regime_log

    if len(recent) < 10:
        return {"matrix": {}, "anomaly_flags": []}

    counts = {r: {r2: 0 for r2 in cfg.REGIMES} for r in cfg.REGIMES}
    for i in range(1, len(recent)):
        fr, to = recent[i - 1], recent[i]
        if fr in counts and to in counts[fr]:
            counts[fr][to] += 1

    # Normalize
    matrix = {}
    for r in cfg.REGIMES:
        total = sum(counts[r].values())
        if total > 0:
            matrix[f"from_{r}"] = {r2: round(counts[r][r2] / total, 3) for r2 in cfg.REGIMES}
        else:
            matrix[f"from_{r}"] = {r2: 0.0 for r2 in cfg.REGIMES}

    # Anomaly detection
    flags = []
    trans_self = matrix.get("from_TRANSITION", {}).get("TRANSITION", 0)
    if trans_self > cfg.TRANSITION_STICKY_THRESHOLD:
        flags.append(f"TRANSITION_STICKY: self-transition={trans_self:.2f}")

    bull_bear = matrix.get("from_BULL", {}).get("BEAR", 0)
    if bull_bear > cfg.DIRECT_BULL_BEAR_THRESHOLD:
        flags.append(f"DIRECT_BULL_BEAR: {bull_bear:.2f}")

    return {"matrix": matrix, "anomaly_flags": flags}


# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state() -> dict:
    """Load persisted state from JSON."""
    STATE_DIR.mkdir(exist_ok=True)
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"State load failed: {e}")
    return default_state()


def save_state(state: dict):
    """Persist state to JSON."""
    STATE_DIR.mkdir(exist_ok=True)

    # Convert non-serializable types
    def convert(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return obj

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=convert)


def default_state() -> dict:
    return {
        "current_regime": "TRANSITION",
        "days_in_regime": 0,
        "last_regime_date": None,
        "regime_start_date": None,
        "P_prev": None,
        "bucket_history": {
            "Momentum": [], "Stability": [], "Rotation": [],
            "Sentiment": [], "Macro": [],
        },
        "regime_log": [],
        "holds_for": 0,
        "holds_candidate": None,
        "last_run": None,
        "model_version": "3.3",
    }


# ============================================================
# MAIN ENGINE
# ============================================================

class RegimeEngine:
    """Market Regime Engine v3.3"""

    VERSION = "3.3"

    def __init__(self):
        self.state = load_state()
        self.norm = AdaptiveNormalizer(cfg.NORM_WINDOW_DEFAULT)
        self.macro_norm = AdaptiveNormalizer(cfg.NORM_WINDOW_MACRO)

    def process(self, raw_data: dict) -> dict:
        """
        Main pipeline. Takes raw_data from data_pipeline.fetch_all_data().
        Returns full output schema.
        """
        flags = []

        # ── 0. Extract and validate data ──────────────────────
        price_df = raw_data.get("price")
        if price_df is None or len(price_df) < 30:
            logger.error("Insufficient price data")
            return self._emergency_output("INSUFFICIENT_DATA")

        close = price_df["close"].values.astype(float)
        high = price_df["high"].values.astype(float)
        low = price_df["low"].values.astype(float)
        volume = price_df["quote_volume"].values.astype(float)

        data_completeness = raw_data["quality"]["completeness"]
        failed_sources = raw_data["quality"].get("failed_sources", [])
        if data_completeness < cfg.DATA_QUALITY_MIN:
            flags.append("DATA_QUALITY_DEGRADED")

        # ── 1. Market cap / BTC dominance ─────────────────────
        mc_hist = raw_data.get("market_cap_history")
        if mc_hist is not None and not mc_hist.empty:
            tmc = mc_hist["market_cap"].values.astype(float)
        else:
            tmc = np.array([])

        glob = raw_data.get("global") or {}
        btc_dom_current = glob.get("btc_dominance", None)
        eth_price = glob.get("eth_price", None)

        # BTC dominance history: approximate from state or single value
        btc_dom_history = np.array(self.state.get("btc_dom_history", []))
        if btc_dom_current is not None:
            btc_dom_history = np.append(btc_dom_history, btc_dom_current)
        # Keep last 120
        btc_dom_history = btc_dom_history[-120:]

        # ── 2. Sentiment inputs ───────────────────────────────
        fg_df = raw_data.get("fear_greed")
        fg_value = 50  # default neutral
        if fg_df is not None and not fg_df.empty:
            fg_value = int(fg_df.iloc[0]["fear_greed"])  # most recent

        fund_df = raw_data.get("funding")
        funding_rates = np.array([])
        if fund_df is not None and not fund_df.empty:
            funding_rates = fund_df["fundingRate"].values.astype(float)

        oi_current = raw_data.get("open_interest")
        oi_history = np.array(self.state.get("oi_history", []))
        if oi_current is not None:
            oi_history = np.append(oi_history, oi_current)
        oi_history = oi_history[-120:]

        # ── 2.5 RSI inputs (multi-timeframe) ────────────────────
        rsi_data = raw_data.get("rsi", {})
        btc_rsi = rsi_data.get("btc", {})
        eth_rsi = rsi_data.get("eth", {})
        
        rsi_1d = btc_rsi.get("rsi_1d")  # Daily RSI-14 (strategic)
        rsi_2h = btc_rsi.get("rsi_2h")  # 2-hour RSI-14 (tactical)
        rsi_1d_7 = btc_rsi.get("rsi_1d_7")  # Daily RSI-7 (momentum)
        rsi_source = btc_rsi.get("source", "none")
        
        # RSI-based flags
        if rsi_1d is not None:
            if rsi_1d >= 80:
                flags.append("RSI_OVERBOUGHT")
            elif rsi_1d <= 20:
                flags.append("RSI_OVERSOLD")
            elif rsi_1d >= 70:
                flags.append("RSI_HIGH")
            elif rsi_1d <= 30:
                flags.append("RSI_LOW")

        # ── 3. Macro inputs ───────────────────────────────────
        fred_df = raw_data.get("fred")
        dxy_arr = np.array([])
        us10y_arr = np.array([])
        us2y_arr = np.array([])
        m2_arr = np.array([])

        yahoo_df = raw_data.get("yahoo")
        spx_arr = np.array([])
        gold_arr = np.array([])

        if yahoo_df is not None and not yahoo_df.empty:
            if "DXY" in yahoo_df.columns:
                dxy_arr = yahoo_df["DXY"].dropna().values.astype(float)
            if "SPX" in yahoo_df.columns:
                spx_arr = yahoo_df["SPX"].dropna().values.astype(float)
            if "GOLD" in yahoo_df.columns:
                gold_arr = yahoo_df["GOLD"].dropna().values.astype(float)

        if fred_df is not None and not fred_df.empty:
            if "US_10Y" in fred_df.columns:
                us10y_arr = fred_df["US_10Y"].dropna().values.astype(float)
            if "US_2Y" in fred_df.columns:
                us2y_arr = fred_df["US_2Y"].dropna().values.astype(float)
            if "M2" in fred_df.columns:
                m2_arr = fred_df["M2"].dropna().values.astype(float)

        # ── 4. Compute buckets ────────────────────────────────
        mom = compute_momentum(close, high, low, tmc, self.norm)
        stab = compute_stability(close, volume, self.norm)
        rot = compute_rotation(btc_dom_history, mom["value"], self.norm)
        sent = compute_sentiment(fg_value, funding_rates, oi_history, self.norm)
        mac = compute_macro(dxy_arr, us10y_arr, us2y_arr, m2_arr,
                           self.norm, self.macro_norm)

        if mac["disabled"]:
            flags.append("MACRO_DATA_INSUFFICIENT")

        M = mom["value"]
        S = stab["value"]
        R = rot["value"]
        Sent = sent["value"]
        Mac = mac["value"]
        vol_z = stab["vol_z"]

        # ── 5. Cross-asset ────────────────────────────────────
        btc_ret = np.diff(np.log(close + 1e-12)) if len(close) > 1 else np.array([])
        spx_ret = np.diff(np.log(spx_arr + 1e-12)) if len(spx_arr) > 1 else np.array([])
        gold_ret = np.diff(np.log(gold_arr + 1e-12)) if len(gold_arr) > 1 else np.array([])

        cross = compute_cross_asset(btc_ret, spx_ret, gold_ret)
        macro_boost = cross["macro_weight_boost"]

        # ── 6. Update bucket history ──────────────────────────
        bh = self.state["bucket_history"]
        for name, val in [("Momentum", M), ("Stability", S), ("Rotation", R),
                          ("Sentiment", Sent), ("Macro", Mac)]:
            bh[name].append(val)
            bh[name] = bh[name][-200:]  # keep last 200

        # ── 7. Logits ─────────────────────────────────────────
        logits = compute_logits(M, S, R, Sent, Mac, vol_z, macro_boost, bh)

        # ── 8. Temperature + Softmax ──────────────────────────
        T = adaptive_temperature(vol_z)
        P_raw = softmax(logits, T)

        # ── 9. EMA smooth ─────────────────────────────────────
        alpha = adaptive_alpha(vol_z)
        P = smooth_probabilities(P_raw, self.state["P_prev"], alpha)

        # ── 10. Regime switching ──────────────────────────────
        current = self.state["current_regime"]
        candidate = max(P, key=P.get)

        # Track how long candidate has been leading
        if candidate == self.state.get("holds_candidate"):
            holds = self.state.get("holds_for", 0) + 1
        else:
            holds = 1

        new_regime = should_switch(P, current, holds)

        # Track days in regime (only increment once per day)
        today = datetime.utcnow().date().isoformat()
        last_date = self.state.get("last_regime_date")
        
        if new_regime and new_regime != current:
            logger.info(f"REGIME SWITCH: {current} → {new_regime}")
            current = new_regime
            self.state["days_in_regime"] = 0
            self.state["regime_start_date"] = today
        else:
            # Only increment if it's a new day
            if last_date != today:
                self.state["days_in_regime"] += 1
        
        self.state["last_regime_date"] = today

        self.state["current_regime"] = current
        self.state["holds_for"] = holds
        self.state["holds_candidate"] = candidate

        # Update regime log
        self.state["regime_log"].append(current)
        self.state["regime_log"] = self.state["regime_log"][-200:]

        # ── 11. Confidence ────────────────────────────────────
        quality = compute_signal_quality(
            M, S, Mac, self.state["days_in_regime"], data_completeness
        )

        conf = compute_confidence(
            P, quality, Sent,
            cross["corr_BTC_SPX"], cross["corr_BTC_Gold"],
            self.state["regime_log"]
        )

        # ── 12. Exposure cap ──────────────────────────────────
        exposure_regime = compute_exposure_cap(current, conf["quality_adjusted"])

        # ── 12.5. Risk Level (policy layer) ───────────────────
        risk = compute_risk_level(P, conf["quality_adjusted"])
        
        # Final exposure: stricter of regime-based and risk-based
        exposure = min(exposure_regime, risk["risk_exposure_cap"])

        # ── 13. Diagnostics (v3.3) ────────────────────────────
        health = bucket_health(bh)
        trans_matrix = compute_transition_matrix(self.state["regime_log"])
        hints = operational_hints(current, S, vol_z, M, self.state["days_in_regime"])

        if health["flags"]:
            flags.extend(health["flags"])
        if trans_matrix["anomaly_flags"]:
            flags.extend(trans_matrix["anomaly_flags"])
        if conf["churn_penalty"] < 1.0:
            flags.append(f"CHURN_PENALTY_ACTIVE: {conf['churn_penalty']:.2f}")

        # ── 14. Edge cases ────────────────────────────────────
        if vol_z > cfg.EXTREME_VOL_THRESHOLD:
            P["TRANSITION"] = max(P["TRANSITION"], cfg.EXTREME_VOL_TRANSITION_FLOOR)
            total = sum(P.values())
            P = {r: v / total for r, v in P.items()}
            conf["quality_adjusted"] *= cfg.EXTREME_VOL_CONFIDENCE_PENALTY
            flags.append("EXTREME_VOLATILITY")

        # ── 15. Save state ────────────────────────────────────
        self.state["P_prev"] = P
        self.state["btc_dom_history"] = btc_dom_history.tolist()
        self.state["oi_history"] = oi_history.tolist()
        self.state["last_run"] = datetime.utcnow().isoformat()

        save_state(self.state)

        # ── 16. Output ────────────────────────────────────────
        return {
            "risk": risk,
            "regime": current,
            "probabilities": {r: round(v, 4) for r, v in P.items()},
            "confidence": conf,
            "buckets": {
                "Momentum": round(M, 4),
                "Stability": round(S, 4),
                "Rotation": round(R, 4),
                "Sentiment": round(Sent, 4),
                "Macro": round(Mac, 4),
            },
            "bucket_details": {
                "momentum": mom["components"],
                "stability": stab["components"],
                "rotation": rot["components"],
                "sentiment": sent["components"],
                "macro": mac["components"],
            },
            "cross_asset": cross,
            "bucket_health": health,
            "regime_dynamics": {
                "transition_matrix": trans_matrix["matrix"],
                "switches_30d": conf["switches_30d"],
            },
            "operational_hints": hints,
            "exposure_cap": round(exposure, 2),
            "risk_flags": flags,
            "normalization": {
                "price_window": self.norm.effective_window,
                "macro_window": self.macro_norm.effective_window,
                "break_active": self.norm.break_active,
            },
            "metadata": {
                "model_version": self.VERSION,
                "days_in_regime": self.state["days_in_regime"],
                "temperature": T,
                "smoothing_alpha": alpha,
                "vol_z": round(vol_z, 4),
                "data_completeness": round(data_completeness, 2),
                "failed_sources": failed_sources,
                "timestamp": datetime.utcnow().isoformat(),
                "btc_price": float(close[-1]) if len(close) > 0 else None,
                "eth_price": float(eth_price) if eth_price else None,
                # v1.4: 30d returns for counter-cyclical logic
                "returns_30d": round((close[-1] / close[-30] - 1), 4) if len(close) >= 30 else 0.0,
                # v4.3: Multi-timeframe RSI
                "rsi": {
                    "rsi_1d": rsi_1d,       # Daily RSI-14 (strategic)
                    "rsi_2h": rsi_2h,       # 2-hour RSI-14 (tactical)
                    "rsi_1d_7": rsi_1d_7,   # Daily RSI-7 (momentum)
                    "source": rsi_source,
                },
            },
        }

    def _emergency_output(self, reason: str) -> dict:
        return {
            "risk": {
                "risk_level": -1.0,
                "risk_state": "RISK_OFF",
                "strength": "extreme",
                "risk_exposure_cap": 0.10,
                "confidence_gated": False,
                "reasons": [f"EMERGENCY: {reason}"],
            },
            "regime": "TRANSITION",
            "probabilities": {r: 0.25 for r in cfg.REGIMES},
            "confidence": {"base": 0.0, "quality_adjusted": 0.0,
                          "churn_penalty": 1.0, "switches_30d": 0},
            "buckets": {b: 0.0 for b in
                       ["Momentum", "Stability", "Rotation", "Sentiment", "Macro"]},
            "exposure_cap": cfg.EMERGENCY_EXPOSURE,
            "risk_flags": [f"EMERGENCY: {reason}"],
            "operational_hints": {"strategy_class": "defensive",
                                  "suggested_lp_mode": "exit"},
            "metadata": {"model_version": self.VERSION,
                        "timestamp": datetime.utcnow().isoformat()},
        }
