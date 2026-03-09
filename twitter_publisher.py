#!/usr/bin/env python3
"""
Twitter Publisher for Market Regime Engine v2.0
Posts BTC regime analysis with chart to Twitter (2x daily)

Format: Smart rotating templates for engagement
No LP information - spot trading focus
"""

import os
import io
import json
import random
import logging
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Try importing tweepy
try:
    import tweepy
    TWEEPY_AVAILABLE = True
except ImportError:
    TWEEPY_AVAILABLE = False
    logger.warning("⚠️ tweepy not installed - Twitter posting disabled")

# Import engine components
try:
    from engine import RegimeEngine
    from data_pipeline import fetch_all_data
    from chart_generator import generate_chart
    ENGINE_AVAILABLE = True
except ImportError as e:
    ENGINE_AVAILABLE = False
    logger.error(f"❌ Engine import failed: {e}")

# Twitter API credentials
TWITTER_API_KEY = os.getenv('TWITTER_API_KEY')
TWITTER_API_SECRET = os.getenv('TWITTER_API_SECRET')
TWITTER_ACCESS_TOKEN = os.getenv('TWITTER_ACCESS_TOKEN')
TWITTER_ACCESS_TOKEN_SECRET = os.getenv('TWITTER_ACCESS_TOKEN_SECRET')
TWITTER_ENABLED = os.getenv('TWITTER_ENABLED', 'true').lower() == 'true'

# Tweet limits
MAX_TWEET_LENGTH = 280

# State file for tracking regime changes
STATE_FILE = Path("state/twitter_state.json")


# ══════════════════════════════════════════════════════════════════════════════
# SMART TEMPLATES - Rotating hooks for variety
# ══════════════════════════════════════════════════════════════════════════════

# REGIME CHANGE (rare event = viral potential)
REGIME_CHANGE_HOOKS = [
    "⚡ REGIME SHIFT: BTC flips to {regime}",
    "🚨 Bitcoin just entered {regime} mode",
    "Breaking: BTC regime changes to {regime}",
    "Alert: Bitcoin transitions to {regime}",
    "⚡ Market structure shift: {regime} confirmed",
    "🔄 Regime change detected: now {regime}",
    "Major shift: BTC enters {regime} territory",
    "🚨 Transition complete: Bitcoin is now {regime}",
    "⚡ New regime: BTC flips {regime}",
    "Alert: Market regime update → {regime}",
]

# BULL - General
BULL_HOOKS = [
    "BTC enters BULL territory 🟢",
    "Bulls are back in control 🟢",
    "Bitcoin momentum turning bullish 🟢",
    "Bull regime active on BTC 🟢",
    "Bitcoin bulls gaining strength 🟢",
    "BTC flashing green signals 🟢",
    "Bullish structure forming on BTC 🟢",
    "Bitcoin showing bullish momentum 🟢",
    "BTC bulls stepping in 🟢",
    "Bullish regime confirmed on Bitcoin 🟢",
    "BTC momentum favors bulls 🟢",
    "Bitcoin enters bullish phase 🟢",
]

# BULL - Early phase
BULL_EARLY_HOOKS = [
    "Early signs of a Bitcoin bull run? 🟢",
    "BTC showing accumulation patterns 🟢",
    "Smart money entering Bitcoin 🟢",
    "Early bull signals on BTC 🟢",
    "Bitcoin accumulation phase active 🟢",
    "BTC: Early bull cycle indicators 🟢",
    "Accumulation detected on Bitcoin 🟢",
    "Early bullish momentum building 🟢",
    "BTC entering early bull territory 🟢",
    "Smart money accumulating BTC 🟢",
    "Bitcoin showing early cycle strength 🟢",
    "BTC: Classic accumulation pattern 🟢",
]

# BULL - Mid phase
BULL_MID_HOOKS = [
    "Bitcoin bull run continues 🟢",
    "BTC maintains bullish momentum 🟢",
    "Bulls firmly in control of BTC 🟢",
    "Bitcoin mid-cycle looking strong 🟢",
    "BTC bullish trend intact 🟢",
    "Bitcoin holding bullish structure 🟢",
    "BTC: Bull market progressing 🟢",
    "Bullish momentum continues on BTC 🟢",
    "Bitcoin bulls still dominant 🟢",
    "BTC mid-bull phase active 🟢",
    "Bitcoin trend remains bullish 🟢",
    "BTC: Sustained bull momentum 🟢",
]

# BULL - Late phase (caution)
BULL_LATE_HOOKS = [
    "BTC in late bull phase — stay alert 🟡",
    "Bitcoin showing late cycle signals 🟡",
    "Late bull indicators on BTC 🟡",
    "BTC euphoria building — be cautious 🟡",
    "Bitcoin: Late cycle dynamics 🟡",
    "BTC approaching potential top zone 🟡",
    "Late bull market signals on Bitcoin 🟡",
    "BTC momentum extended — watch closely 🟡",
    "Bitcoin late cycle — risk rising 🟡",
    "BTC: Distribution patterns emerging? 🟡",
    "Late stage bull market on BTC 🟡",
    "Bitcoin showing mature cycle signs 🟡",
]

# BULL + Oversold RSI (rare opportunity)
BULL_OVERSOLD_HOOKS = [
    "BTC oversold in a bull market 👀",
    "Rare setup: Bull regime + low RSI 🎯",
    "Bitcoin dip in bullish structure 🎯",
    "BTC: Oversold bounce opportunity? 👀",
    "Bull market pullback on Bitcoin 🎯",
    "Oversold RSI in bull trend — watching 👀",
    "BTC dip while bulls control trend 🎯",
    "Bitcoin: Buy-the-dip setup? 👀",
    "Rare oversold reading in bull phase 🎯",
    "BTC RSI reset in bullish regime 👀",
    "Bull trend + oversold = opportunity? 🎯",
    "Bitcoin showing oversold divergence 👀",
]

# BEAR - General
BEAR_HOOKS = [
    "BTC enters BEAR territory 🔴",
    "Bears taking control of Bitcoin 🔴",
    "Bitcoin regime flips bearish 🔴",
    "BTC showing bearish momentum 🔴",
    "Bear market signals on Bitcoin 🔴",
    "BTC: Bears in control 🔴",
    "Bearish structure forming on BTC 🔴",
    "Bitcoin momentum turns negative 🔴",
    "BTC bears gaining strength 🔴",
    "Bearish regime active on Bitcoin 🔴",
    "BTC flashing red signals 🔴",
    "Bitcoin enters bearish phase 🔴",
]

# BEAR - Early phase
BEAR_EARLY_HOOKS = [
    "BTC showing early bear signals 🔴",
    "Bitcoin: Early distribution phase? 🔴",
    "Early bearish indicators on BTC 🔴",
    "Distribution patterns on Bitcoin 🔴",
    "BTC: Sellers stepping in 🔴",
    "Early bear market signs on BTC 🔴",
    "Bitcoin showing distribution 🔴",
    "BTC early downtrend forming 🔴",
    "Bearish momentum emerging on Bitcoin 🔴",
    "BTC: Early cycle weakness 🔴",
    "Distribution phase active on BTC 🔴",
    "Bitcoin bears emerging 🔴",
]

# BEAR - Capitulation (opportunity)
BEAR_CAPITULATION_HOOKS = [
    "BTC capitulation signals detected 💀",
    "Bitcoin showing extreme fear 💀",
    "BTC: Capitulation phase? 💀",
    "Maximum fear on Bitcoin charts 💀",
    "BTC panic selling detected 💀",
    "Capitulation indicators on Bitcoin 💀",
    "BTC: Extreme pessimism zone 💀",
    "Bitcoin fear at extremes 💀",
    "BTC showing washout signals 💀",
    "Maximum pessimism on Bitcoin 💀",
    "Capitulation vibes on BTC 💀",
    "Bitcoin: Blood in the streets? 💀",
]

# BEAR + Overbought RSI (rare bounce setup)
BEAR_OVERBOUGHT_HOOKS = [
    "BTC overbought in bear market 👀",
    "Bear rally getting extended on BTC 👀",
    "Bitcoin: Overbought in downtrend 👀",
    "BTC relief rally stretched 👀",
    "Overbought RSI in bear regime 👀",
    "Bitcoin showing bearish divergence 👀",
    "BTC rally in bear trend — caution 👀",
    "Overbought bounce in bear market 👀",
    "BTC: Short opportunity forming? 👀",
    "Bear market rally extended 👀",
    "Bitcoin overbought in downtrend 👀",
    "BTC showing resistance at RSI highs 👀",
]

# TRANSITION / RANGE
TRANSITION_HOOKS = [
    "BTC in transition zone 🔄",
    "Bitcoin: Market deciding direction 🔄",
    "BTC consolidation continues 🔄",
    "Bitcoin at a crossroads 🔄",
    "BTC regime unclear — watching 🔄",
    "Market indecision on Bitcoin 🔄",
    "BTC: Transition phase active 🔄",
    "Bitcoin building energy 🔄",
    "BTC coiling for next move 🔄",
    "Consolidation pattern on Bitcoin 🔄",
    "BTC: Direction TBD 🔄",
    "Bitcoin in wait-and-see mode 🔄",
]

# RANGE - Accumulation zone
ACCUMULATION_HOOKS = [
    "BTC showing accumulation signals 🔵",
    "Bitcoin: Quiet accumulation phase 🔵",
    "Smart money accumulating BTC? 🔵",
    "BTC range with accumulation bias 🔵",
    "Bitcoin base building continues 🔵",
    "Accumulation detected on BTC 🔵",
    "BTC: Stealthy accumulation? 🔵",
    "Bitcoin holding support, building 🔵",
    "BTC range-bound, accumulating 🔵",
    "Patient accumulation on Bitcoin 🔵",
    "BTC: Low volatility accumulation 🔵",
    "Bitcoin quietly being accumulated 🔵",
]

# RANGE - Distribution zone
DISTRIBUTION_HOOKS = [
    "BTC showing distribution signals ⚠️",
    "Bitcoin: Distribution at highs? ⚠️",
    "Smart money distributing BTC? ⚠️",
    "BTC range with distribution bias ⚠️",
    "Bitcoin top formation possible ⚠️",
    "Distribution detected on BTC ⚠️",
    "BTC: Stealth distribution? ⚠️",
    "Bitcoin struggling at resistance ⚠️",
    "BTC range-bound, distributing ⚠️",
    "Heavy selling pressure on Bitcoin ⚠️",
    "BTC: Supply entering market ⚠️",
    "Bitcoin showing exhaustion ⚠️",
]

# HIGH VOLATILITY / CRISIS
CRISIS_HOOKS = [
    "⚠️ BTC volatility spike detected",
    "🚨 High volatility on Bitcoin",
    "⚠️ BTC: Risk management mode",
    "🚨 Extreme moves on Bitcoin",
    "⚠️ BTC volatility elevated",
    "🚨 Bitcoin in turbulent waters",
    "⚠️ High risk environment for BTC",
    "🚨 Volatility explosion on Bitcoin",
    "⚠️ BTC: Proceed with caution",
    "🚨 Wild swings on Bitcoin",
    "⚠️ BTC risk levels elevated",
    "🚨 Bitcoin volatility warning",
]


# ══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def load_state() -> dict:
    """Load previous state to detect regime changes."""
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"⚠️ Could not load state: {e}")
    return {"last_regime": None, "last_hook_index": {}}


def save_state(state: dict):
    """Save state for next run."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning(f"⚠️ Could not save state: {e}")


def select_hook(hooks: list, category: str, state: dict) -> str:
    """Select hook with rotation to avoid repetition."""
    last_index = state.get("last_hook_index", {}).get(category, -1)
    
    # Get available indices (excluding last used)
    available = [i for i in range(len(hooks)) if i != last_index]
    if not available:
        available = list(range(len(hooks)))
    
    # Random selection from available
    selected_index = random.choice(available)
    
    # Update state
    if "last_hook_index" not in state:
        state["last_hook_index"] = {}
    state["last_hook_index"][category] = selected_index
    
    return hooks[selected_index]


def init_twitter_client():
    """Initialize Twitter API client."""
    if not TWEEPY_AVAILABLE:
        logger.error("❌ tweepy not available")
        return None
    
    if not all([TWITTER_API_KEY, TWITTER_API_SECRET, 
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]):
        logger.error("❌ Twitter credentials not set")
        return None
    
    try:
        client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
            wait_on_rate_limit=True
        )
        
        auth = tweepy.OAuth1UserHandler(
            TWITTER_API_KEY,
            TWITTER_API_SECRET,
            TWITTER_ACCESS_TOKEN,
            TWITTER_ACCESS_TOKEN_SECRET
        )
        api = tweepy.API(auth)
        
        logger.info("✓ Twitter API initialized")
        return {"client": client, "api": api}
    except Exception as e:
        logger.error(f"❌ Twitter init error: {e}")
        return None


def run_engine() -> dict:
    """Run the market regime engine and return results."""
    if not ENGINE_AVAILABLE:
        logger.error("❌ Engine not available")
        return None
    
    try:
        # Fetch market data
        logger.info("📡 Fetching market data...")
        raw_data = fetch_all_data()
        
        # Process through engine
        engine = RegimeEngine()
        result = engine.process(raw_data)
        logger.info(f"✓ Engine completed: regime={result.get('regime')}")
        return result
    except Exception as e:
        logger.error(f"❌ Engine error: {e}")
        import traceback
        traceback.print_exc()
        return None


def determine_phase(regime: str, days_in_regime: int, risk_level: float) -> str:
    """Determine market phase based on regime and conditions."""
    if regime == "BEAR":
        if days_in_regime > 30 and risk_level < -0.5:
            return "CAPITULATION"
        elif days_in_regime > 14:
            return "MID_BEAR"
        else:
            return "EARLY_BEAR"
    elif regime == "BULL":
        if days_in_regime > 30 and risk_level > 0.5:
            return "LATE_BULL"
        elif days_in_regime > 14:
            return "MID_BULL"
        else:
            return "EARLY_BULL"
    elif regime == "TRANSITION":
        if risk_level < -0.3:
            return "DISTRIBUTION"
        elif risk_level > 0.3:
            return "ACCUMULATION"
        else:
            return "TRANSITION"
    else:
        return "RANGE"


def get_smart_hook(regime: str, phase: str, rsi: float, risk_state: str,
                   regime_changed: bool, state: dict) -> str:
    """
    Select the best hook based on market conditions.
    Priority:
    1. Regime change (rare = viral)
    2. Crisis/High volatility
    3. Extreme RSI + regime combo
    4. Phase-specific
    5. General regime
    """
    
    # 1. Regime change - highest priority
    if regime_changed:
        hook = select_hook(REGIME_CHANGE_HOOKS, "regime_change", state)
        return hook.format(regime=regime)
    
    # 2. Crisis mode
    if risk_state == "CRISIS":
        return select_hook(CRISIS_HOOKS, "crisis", state)
    
    # 3. BULL scenarios
    if regime == "BULL":
        # Oversold in bull = rare opportunity
        if rsi and rsi < 35:
            return select_hook(BULL_OVERSOLD_HOOKS, "bull_oversold", state)
        
        # Phase-specific
        if phase == "EARLY_BULL":
            return select_hook(BULL_EARLY_HOOKS, "bull_early", state)
        elif phase == "LATE_BULL":
            return select_hook(BULL_LATE_HOOKS, "bull_late", state)
        elif phase == "MID_BULL":
            return select_hook(BULL_MID_HOOKS, "bull_mid", state)
        else:
            return select_hook(BULL_HOOKS, "bull", state)
    
    # 4. BEAR scenarios
    if regime == "BEAR":
        # Overbought in bear = potential short
        if rsi and rsi > 65:
            return select_hook(BEAR_OVERBOUGHT_HOOKS, "bear_overbought", state)
        
        # Capitulation = opportunity
        if phase == "CAPITULATION":
            return select_hook(BEAR_CAPITULATION_HOOKS, "bear_capitulation", state)
        elif phase == "EARLY_BEAR":
            return select_hook(BEAR_EARLY_HOOKS, "bear_early", state)
        else:
            return select_hook(BEAR_HOOKS, "bear", state)
    
    # 5. TRANSITION / RANGE
    if phase == "ACCUMULATION":
        return select_hook(ACCUMULATION_HOOKS, "accumulation", state)
    elif phase == "DISTRIBUTION":
        return select_hook(DISTRIBUTION_HOOKS, "distribution", state)
    else:
        return select_hook(TRANSITION_HOOKS, "transition", state)


def format_tweet(result: dict, state: dict) -> str:
    """
    Format engine result as a tweet using smart templates.
    
    Format (Variant 2):
    {SMART_HOOK}
    
    $95,234 | Confidence 68%
    
    Phase: Early Bull
    Smart money accumulating
    
    #Bitcoin #BTC
    """
    try:
        regime = result.get("regime", "RANGE")
        probs = result.get("probabilities", {})
        meta = result.get("metadata", {})
        risk = result.get("risk", {})
        conf = result.get("confidence", {})
        
        # Key metrics
        prob_bull = int(probs.get("BULL", 0) * 100)
        prob_bear = int(probs.get("BEAR", 0) * 100)
        btc_price = meta.get("btc_price")
        days = meta.get("days_in_regime", 0)
        risk_level = risk.get("risk_level", 0)
        conf_pct = int(conf.get("quality_adjusted", 0) * 100)
        
        # RSI
        rsi_data = meta.get("rsi", {})
        rsi_1d = rsi_data.get("rsi_1d")
        
        # Risk state
        vol_z = meta.get("vol_z", 0)
        if vol_z > 2.5:
            risk_state = "CRISIS"
        elif vol_z > 1.5:
            risk_state = "ELEVATED"
        else:
            risk_state = "NORMAL"
        
        # Phase
        phase = determine_phase(regime, days, risk_level)
        
        # Check regime change
        last_regime = state.get("last_regime")
        regime_changed = last_regime is not None and last_regime != regime
        
        # Get smart hook
        hook = get_smart_hook(regime, phase, rsi_1d, risk_state, regime_changed, state)
        
        # Format price
        price_str = f"${btc_price:,.0f}" if btc_price else ""
        
        # Phase description (friendly)
        phase_descriptions = {
            "EARLY_BULL": "Early bull cycle",
            "MID_BULL": "Bull trend continues",
            "LATE_BULL": "Late cycle - stay alert",
            "EARLY_BEAR": "Early bear signals",
            "MID_BEAR": "Bear trend active",
            "CAPITULATION": "Capitulation phase",
            "ACCUMULATION": "Accumulation zone",
            "DISTRIBUTION": "Distribution zone",
            "TRANSITION": "Market in transition",
            "RANGE": "Range-bound market",
        }
        phase_text = phase_descriptions.get(phase, phase.replace("_", " ").title())
        
        # Action hint based on phase
        action_hints = {
            "EARLY_BULL": "Smart money accumulating",
            "MID_BULL": "Trend remains strong",
            "LATE_BULL": "Consider taking profits",
            "EARLY_BEAR": "Risk management advised",
            "MID_BEAR": "Patience required",
            "CAPITULATION": "Opportunity for the brave",
            "ACCUMULATION": "Building positions",
            "DISTRIBUTION": "Watching for breakdown",
            "TRANSITION": "Waiting for clarity",
            "RANGE": "Range trading active",
        }
        action_hint = action_hints.get(phase, "Monitor closely")
        
        # Build tweet
        lines = [hook, ""]
        
        # Price + Confidence
        if price_str:
            lines.append(f"{price_str} | Confidence {conf_pct}%")
        else:
            lines.append(f"Bull {prob_bull}% | Bear {prob_bear}%")
        
        lines.append("")
        
        # Phase + Action hint
        lines.append(f"Phase: {phase_text}")
        lines.append(action_hint)
        
        # Hashtags
        lines.append("")
        lines.append("#Bitcoin #BTC")
        
        tweet = "\n".join(lines)
        
        # Trim if needed
        if len(tweet) > MAX_TWEET_LENGTH:
            # Remove action hint
            lines = [l for l in lines if l != action_hint]
            tweet = "\n".join(lines)
        
        if len(tweet) > MAX_TWEET_LENGTH:
            # Minimal version
            tweet = f"{hook}\n\n{price_str}\n\n#Bitcoin #BTC"
        
        # Update state
        state["last_regime"] = regime
        
        return tweet[:MAX_TWEET_LENGTH]
        
    except Exception as e:
        logger.error(f"❌ Format error: {e}")
        return "Bitcoin market update 📊\n\n#Bitcoin #BTC"


def post_tweet(tweet_text: str, chart_buf: io.BytesIO = None) -> bool:
    """Post tweet with optional image."""
    if not TWITTER_ENABLED:
        logger.info("ℹ️ Twitter disabled - preview mode")
        print(f"\n📝 Tweet preview ({len(tweet_text)} chars):\n{'-'*40}\n{tweet_text}\n{'-'*40}")
        return True
    
    twitter = init_twitter_client()
    if not twitter:
        return False
    
    client = twitter["client"]
    api = twitter["api"]
    
    try:
        # Upload image
        media_id = None
        if chart_buf:
            try:
                chart_buf.seek(0)
                media = api.media_upload(filename="btc_chart.png", file=chart_buf)
                media_id = media.media_id
                logger.info("✓ Chart uploaded")
            except Exception as e:
                logger.warning(f"⚠️ Image upload failed: {e}")
        
        # Post
        if media_id:
            response = client.create_tweet(text=tweet_text, media_ids=[media_id])
        else:
            response = client.create_tweet(text=tweet_text)
        
        if response and hasattr(response, 'data'):
            try:
                tweet_id = response.data.get('id') if hasattr(response.data, 'get') else response.data.id
                logger.info(f"✓ Tweet posted (ID: {tweet_id})")
            except:
                logger.info("✓ Tweet posted")
            return True
        else:
            logger.error("❌ Empty response")
            return False
            
    except Exception as e:
        error_str = str(e)
        if "rate limit" in error_str.lower() or "429" in error_str:
            logger.warning("⚠️ Rate limited")
            return False
        if "duplicate" in error_str.lower() or "187" in error_str:
            logger.warning("⚠️ Duplicate tweet")
            return True
        logger.error(f"❌ Twitter error: {e}")
        return False


def main():
    """Main function - run engine and post to Twitter."""
    logger.info("🚀 Starting Market Regime Twitter Publisher v2.0...")
    logger.info(f"   Timestamp: {datetime.utcnow().isoformat()}")
    
    # Load state
    state = load_state()
    logger.info(f"   Last regime: {state.get('last_regime', 'None')}")
    
    # Run engine
    result = run_engine()
    if not result:
        logger.error("❌ No engine result - aborting")
        return False
    
    # Log metrics
    regime = result.get("regime", "UNKNOWN")
    probs = result.get("probabilities", {})
    logger.info(f"📊 Current regime: {regime}")
    logger.info(f"   BULL: {int(probs.get('BULL', 0)*100)}%")
    logger.info(f"   BEAR: {int(probs.get('BEAR', 0)*100)}%")
    
    # Check for regime change
    if state.get("last_regime") and state.get("last_regime") != regime:
        logger.info(f"⚡ REGIME CHANGE: {state.get('last_regime')} → {regime}")
    
    # Generate chart
    logger.info("📈 Generating BTC chart...")
    chart_buf = generate_chart("BTC-USD", days_to_show=365)
    if chart_buf:
        logger.info("✓ Chart generated")
    else:
        logger.warning("⚠️ Chart failed - posting without image")
    
    # Format tweet
    tweet_text = format_tweet(result, state)
    logger.info(f"📝 Tweet ({len(tweet_text)} chars):\n{tweet_text}")
    
    # Post
    success = post_tweet(tweet_text, chart_buf)
    
    # Save state
    save_state(state)
    
    if success:
        logger.info("🎉 Successfully posted!")
    else:
        logger.error("💥 Failed to post")
    
    return success


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
