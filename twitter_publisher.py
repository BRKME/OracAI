#!/usr/bin/env python3
"""
Twitter Publisher for Market Regime Engine v1.0
Posts BTC regime analysis with chart to Twitter (2x daily)

Format: English only, hashtags for maximum reach
No LP information - spot trading focus
"""

import os
import io
import logging
import time
from datetime import datetime

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
    from engine import MarketRegimeEngine
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
        # Tweepy v2 Client for posting
        client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
            wait_on_rate_limit=True
        )
        
        # API v1.1 for media upload
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
        engine = MarketRegimeEngine()
        result = engine.run()
        logger.info(f"✓ Engine completed: regime={result.get('regime')}")
        return result
    except Exception as e:
        logger.error(f"❌ Engine error: {e}")
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


def determine_action(phase: str, bottom_prox: float, top_prox: float, 
                    risk_state: str) -> tuple:
    """
    Determine action based on phase and proximity signals.
    Returns (action_emoji, action_text)
    """
    # Crisis mode overrides everything
    if risk_state == "CRISIS":
        return "⚫", "PROTECT"
    
    # Strong bottom signal
    if bottom_prox >= 0.6:
        return "🟢", "BUY"
    
    # Strong top signal
    if top_prox >= 0.6:
        return "🔴", "SELL"
    
    # Moderate bottom
    if bottom_prox >= 0.3:
        return "🟡", "ADD"
    
    # Moderate top
    if top_prox >= 0.3:
        return "🟠", "REDUCE"
    
    # Phase-based signals
    if phase in ("EARLY_BULL", "ACCUMULATION") and bottom_prox >= 0.2:
        return "🟡", "ADD"
    elif phase in ("LATE_BULL", "DISTRIBUTION") and top_prox >= 0.2:
        return "🟠", "REDUCE"
    elif phase == "CAPITULATION":
        return "🟢", "BUY"
    else:
        return "⚪", "HOLD"


def calculate_bottom_top_proximity(result: dict) -> tuple:
    """Calculate bottom and top proximity from engine result."""
    try:
        meta = result.get("metadata", {})
        buckets = result.get("buckets", {})
        risk = result.get("risk", {})
        
        momentum = buckets.get("Momentum", 0)
        sentiment = buckets.get("Sentiment", 0)
        risk_level = risk.get("risk_level", 0)
        
        rsi_data = meta.get("rsi", {})
        rsi_1d = rsi_data.get("rsi_1d", 50) or 50
        
        # Bottom proximity
        bottom_score = 0
        if momentum < -0.5:
            bottom_score += 0.3
        if sentiment < -0.3:
            bottom_score += 0.2
        if rsi_1d < 30:
            bottom_score += 0.3
        elif rsi_1d < 40:
            bottom_score += 0.15
        if risk_level < -0.5:
            bottom_score += 0.2
        
        # Top proximity
        top_score = 0
        if momentum > 0.5:
            top_score += 0.3
        if sentiment > 0.3:
            top_score += 0.2
        if rsi_1d > 70:
            top_score += 0.3
        elif rsi_1d > 60:
            top_score += 0.15
        if risk_level > 0.5:
            top_score += 0.2
        
        return min(1.0, bottom_score), min(1.0, top_score)
    except:
        return 0.0, 0.0


def format_tweet(result: dict) -> str:
    """
    Format engine result as a tweet.
    English only, with hashtags, max 280 chars.
    """
    try:
        regime = result.get("regime", "RANGE")
        probs = result.get("probabilities", {})
        meta = result.get("metadata", {})
        risk = result.get("risk", {})
        conf = result.get("confidence", {})
        
        # Probabilities
        prob_bull = int(probs.get("BULL", 0) * 100)
        prob_bear = int(probs.get("BEAR", 0) * 100)
        
        # Price
        btc_price = meta.get("btc_price")
        price_str = f"${btc_price:,.0f}" if btc_price else ""
        
        # RSI
        rsi_data = meta.get("rsi", {})
        rsi_1d = rsi_data.get("rsi_1d")
        
        # Days and risk
        days = meta.get("days_in_regime", 0)
        risk_level = risk.get("risk_level", 0)
        
        # Confidence
        conf_pct = int(conf.get("quality_adjusted", 0) * 100)
        
        # Phase
        phase = determine_phase(regime, days, risk_level)
        
        # Risk state
        vol_z = meta.get("vol_z", 0)
        if vol_z > 2.5:
            risk_state = "CRISIS"
        elif vol_z > 1.5:
            risk_state = "ELEVATED"
        else:
            risk_state = "NORMAL"
        
        # Bottom/Top proximity
        bottom_prox, top_prox = calculate_bottom_top_proximity(result)
        
        # Action
        action_emoji, action_text = determine_action(phase, bottom_prox, top_prox, risk_state)
        
        # Regime emoji
        regime_emoji = {
            "BULL": "🟢",
            "BEAR": "🔴", 
            "RANGE": "🟡",
            "TRANSITION": "🔄"
        }.get(regime, "⚪")
        
        # Build tweet
        lines = []
        
        # Line 1: Regime + Price
        if price_str:
            lines.append(f"{regime_emoji} #BTC {regime} | {price_str}")
        else:
            lines.append(f"{regime_emoji} #BTC Market: {regime}")
        
        # Line 2: Probabilities
        lines.append(f"📊 Bull {prob_bull}% · Bear {prob_bear}%")
        
        # Line 3: Phase
        phase_display = phase.replace("_", " ").title()
        lines.append(f"📈 {phase_display} (Day {days})")
        
        # Line 4: RSI if notable
        if rsi_1d:
            if rsi_1d > 70:
                lines.append(f"⚠️ RSI {int(rsi_1d)} Overbought")
            elif rsi_1d < 30:
                lines.append(f"💡 RSI {int(rsi_1d)} Oversold")
        
        # Line 5: Action
        action_map = {
            "BUY": "Opportunity zone",
            "SELL": "Take profits",
            "ADD": "Consider adding",
            "REDUCE": "Consider reducing",
            "HOLD": "Stay patient",
            "PROTECT": "Risk management"
        }
        action_desc = action_map.get(action_text, action_text)
        lines.append(f"{action_emoji} {action_desc}")
        
        # Hashtags (optimized for crypto Twitter reach)
        hashtags = "#Bitcoin #Crypto #Trading"
        
        # Build final tweet
        tweet = "\n".join(lines) + f"\n\n{hashtags}"
        
        # Ensure under limit
        if len(tweet) > MAX_TWEET_LENGTH:
            # Remove RSI line if too long
            lines = [l for l in lines if not l.startswith("⚠️ RSI") and not l.startswith("💡 RSI")]
            tweet = "\n".join(lines) + f"\n\n{hashtags}"
        
        if len(tweet) > MAX_TWEET_LENGTH:
            # Remove phase line
            lines = [l for l in lines if not l.startswith("📈")]
            tweet = "\n".join(lines) + f"\n\n{hashtags}"
        
        return tweet[:MAX_TWEET_LENGTH]
        
    except Exception as e:
        logger.error(f"❌ Format error: {e}")
        return "🟢 #BTC Market Update\n\n#Bitcoin #Crypto #Trading"


def post_tweet(tweet_text: str, chart_buf: io.BytesIO = None) -> bool:
    """Post tweet with optional image."""
    if not TWITTER_ENABLED:
        logger.info("ℹ️ Twitter disabled")
        print(f"\n📝 Tweet preview ({len(tweet_text)} chars):\n{tweet_text}")
        return True
    
    twitter = init_twitter_client()
    if not twitter:
        return False
    
    client = twitter["client"]
    api = twitter["api"]
    
    try:
        # Upload image if provided
        media_id = None
        if chart_buf:
            try:
                chart_buf.seek(0)
                media = api.media_upload(filename="btc_chart.png", file=chart_buf)
                media_id = media.media_id
                logger.info("✓ Chart uploaded to Twitter")
            except Exception as e:
                logger.warning(f"⚠️ Image upload failed: {e}")
        
        # Post tweet
        if media_id:
            response = client.create_tweet(text=tweet_text, media_ids=[media_id])
        else:
            response = client.create_tweet(text=tweet_text)
        
        if response and hasattr(response, 'data'):
            try:
                if hasattr(response.data, 'get'):
                    tweet_id = response.data.get('id')
                elif hasattr(response.data, 'id'):
                    tweet_id = response.data.id
                else:
                    tweet_id = response.data['id']
                
                logger.info(f"✓ Tweet posted (ID: {tweet_id})")
                return True
            except:
                logger.info("✓ Tweet posted")
                return True
        else:
            logger.error("❌ Empty response from Twitter")
            return False
            
    except Exception as e:
        error_str = str(e)
        
        if "rate limit" in error_str.lower() or "429" in error_str:
            logger.warning("⚠️ Rate limited - will retry later")
            return False
        
        if "duplicate" in error_str.lower() or "187" in error_str:
            logger.warning("⚠️ Duplicate tweet - skipping")
            return True
        
        logger.error(f"❌ Twitter error: {e}")
        return False


def main():
    """Main function - run engine and post to Twitter."""
    logger.info("🚀 Starting Market Regime Twitter Publisher...")
    logger.info(f"   Timestamp: {datetime.utcnow().isoformat()}")
    
    # Run engine
    result = run_engine()
    if not result:
        logger.error("❌ No engine result - aborting")
        return False
    
    # Log key metrics
    regime = result.get("regime", "UNKNOWN")
    probs = result.get("probabilities", {})
    logger.info(f"📊 Regime: {regime}")
    logger.info(f"   BULL: {int(probs.get('BULL', 0)*100)}%")
    logger.info(f"   BEAR: {int(probs.get('BEAR', 0)*100)}%")
    
    # Generate BTC chart (no ETH)
    logger.info("📈 Generating BTC chart...")
    chart_buf = generate_chart("BTC-USD", days_to_show=365)
    if not chart_buf:
        logger.warning("⚠️ Chart generation failed - posting without image")
    else:
        logger.info("✓ Chart generated")
    
    # Format tweet
    tweet_text = format_tweet(result)
    logger.info(f"📝 Tweet ({len(tweet_text)} chars):\n{tweet_text}")
    
    # Post to Twitter
    success = post_tweet(tweet_text, chart_buf)
    
    if success:
        logger.info("🎉 Successfully posted to Twitter!")
    else:
        logger.error("💥 Failed to post to Twitter")
    
    return success


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
