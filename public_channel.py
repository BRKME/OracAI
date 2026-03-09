#!/usr/bin/env python3
"""
OracAI Public Channel Publisher v1.0
Adaptive trigger-based publication to public Telegram channel.

Uses engine.py data + OpenAI for analysis.
Global cooldown prevents spam (min 4 hours between posts).
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple

import requests
from openai import OpenAI

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import engine
try:
    from engine import RegimeEngine
    from data_pipeline import fetch_all_data
    ENGINE_AVAILABLE = True
except ImportError as e:
    ENGINE_AVAILABLE = False
    logger.error(f"Engine import failed: {e}")

# State file
STATE_FILE = "state/public_channel_state.json"

# Global cooldown (hours)
GLOBAL_COOLDOWN_HOURS = 4

# Trigger thresholds
TRIGGER_24H_CHANGE = 5.0      # % change in 24h
TRIGGER_7D_CHANGE = 10.0      # % change in 7d
ROUND_LEVEL_STEP = 5000       # $5000 steps


class PublicChannelPublisher:
    """Publisher for public Telegram channel with trigger logic."""
    
    def __init__(self):
        # Credentials
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.public_channel_id = os.getenv('TELEGRAM_PUBLIC_CHANNEL_ID')
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        
        # Validate
        if not self.telegram_token:
            logger.error("❌ TELEGRAM_BOT_TOKEN not set")
        if not self.public_channel_id:
            logger.error("❌ TELEGRAM_PUBLIC_CHANNEL_ID not set")
        if not self.openai_api_key:
            logger.warning("⚠️ OPENAI_API_KEY not set - will use fallback")
        
        # OpenAI client
        self.openai_client = None
        if self.openai_api_key:
            try:
                self.openai_client = OpenAI(api_key=self.openai_api_key)
            except Exception as e:
                logger.warning(f"OpenAI init failed: {e}")
        
        # Load state
        self.state = self._load_state()
    
    def _load_state(self) -> Dict:
        """Load state from file."""
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"State load error: {e}")
        
        return {
            "last_publish": None,           # Global cooldown tracker
            "last_regime": None,
            "last_round_level": None,
        }
    
    def _save_state(self):
        """Save state to file."""
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"State save error: {e}")
    
    def _check_global_cooldown(self) -> Tuple[bool, float]:
        """
        Check if global cooldown has passed.
        Returns (cooldown_ok, hours_remaining).
        """
        last_publish = self.state.get("last_publish")
        if not last_publish:
            return True, 0
        
        try:
            last_dt = datetime.fromisoformat(last_publish.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            elapsed = (now - last_dt).total_seconds() / 3600
            
            if elapsed >= GLOBAL_COOLDOWN_HOURS:
                return True, 0
            else:
                return False, GLOBAL_COOLDOWN_HOURS - elapsed
        except Exception as e:
            logger.warning(f"Cooldown parse error: {e}")
            return True, 0
    
    def _get_round_level(self, price: float) -> int:
        """Get nearest round level ($5000 steps)."""
        return int(price // ROUND_LEVEL_STEP) * ROUND_LEVEL_STEP
    
    def check_triggers(self, engine_result: dict) -> Tuple[bool, str]:
        """
        Check publication triggers.
        
        Returns (should_publish, trigger_reason).
        
        Triggers:
        1. Regime change
        2. Significant 24h move (>5%)
        3. Round level breakout ($5k steps)
        4. Tail risk active
        """
        triggers = []
        
        # === GLOBAL COOLDOWN CHECK FIRST ===
        cooldown_ok, hours_remaining = self._check_global_cooldown()
        if not cooldown_ok:
            logger.info(f"⏳ Global cooldown active: {hours_remaining:.1f}h remaining")
            return False, ""
        
        # Extract data
        regime = engine_result.get("regime", "TRANSITION")
        meta = engine_result.get("metadata", {})
        risk = engine_result.get("risk", {})
        
        btc_price = meta.get("btc_price", 0)
        returns_30d = meta.get("returns_30d", 0)
        
        # Approximate 24h change from bucket details if available
        bucket_details = engine_result.get("bucket_details", {})
        momentum = bucket_details.get("momentum", {})
        btc_change_24h = momentum.get("btc_24h", 0) if momentum else 0
        
        # Risk level for tail detection
        risk_level = risk.get("risk_level", 0)
        vol_z = meta.get("vol_z", 0)
        
        logger.info(f"Checking triggers: regime={regime}, price=${btc_price:,.0f}")
        
        # 1. REGIME CHANGE
        last_regime = self.state.get("last_regime")
        if last_regime and regime != last_regime:
            triggers.append(f"Regime: {last_regime} → {regime}")
            logger.info(f"✓ Trigger: Regime change {last_regime} → {regime}")
        
        # 2. SIGNIFICANT PRICE MOVE (approximate from 30d returns scaled)
        # If we don't have precise 24h, use momentum signals
        if abs(btc_change_24h) > TRIGGER_24H_CHANGE:
            triggers.append(f"BTC {btc_change_24h:+.1f}% in 24h")
            logger.info(f"✓ Trigger: 24h change {btc_change_24h:+.1f}%")
        
        # 3. ROUND LEVEL BREAKOUT
        if btc_price > 0:
            current_level = self._get_round_level(btc_price)
            last_level = self.state.get("last_round_level")
            
            if last_level is None:
                self.state["last_round_level"] = current_level
                last_level = current_level
            
            if current_level != last_level:
                direction = "above" if current_level > last_level else "below"
                levels_crossed = abs(current_level - last_level) // ROUND_LEVEL_STEP
                
                if levels_crossed > 1:
                    triggers.append(f"BTC broke {direction} ${current_level:,} ({int(levels_crossed)} levels)")
                else:
                    triggers.append(f"BTC broke {direction} ${current_level:,}")
                
                logger.info(f"✓ Trigger: Round level ${last_level:,} → ${current_level:,}")
                self.state["last_round_level"] = current_level
        
        # 4. TAIL RISK / HIGH VOLATILITY
        if vol_z > 2.0 or abs(risk_level) > 0.7:
            triggers.append("Elevated volatility")
            logger.info(f"✓ Trigger: High volatility (vol_z={vol_z:.1f})")
        
        logger.info(f"Total triggers: {len(triggers)}")
        return len(triggers) > 0, " | ".join(triggers)
    
    def generate_ai_analysis(self, engine_result: dict) -> str:
        """Generate AI analysis using OpenAI."""
        if not self.openai_client:
            return self._fallback_analysis(engine_result)
        
        try:
            regime = engine_result.get("regime", "TRANSITION")
            probs = engine_result.get("probabilities", {})
            meta = engine_result.get("metadata", {})
            conf = engine_result.get("confidence", {})
            
            btc_price = meta.get("btc_price", 0)
            rsi_data = meta.get("rsi", {})
            rsi = rsi_data.get("rsi_1d", 50) or 50
            days_in_regime = meta.get("days_in_regime", 0)
            
            prob_bull = int(probs.get("BULL", 0) * 100)
            prob_bear = int(probs.get("BEAR", 0) * 100)
            confidence = int(conf.get("quality_adjusted", 0) * 100)
            
            system_prompt = """You are a crypto market analyst. Write for a general audience.

Output EXACTLY this format:

◼️ [1-2 sentences: What's happening and what to do. Simple language.]

<b>Positioning</b>
🟢 New longs: [low risk / moderate risk / high risk]
⚠️ Aggressive buying: [encouraged / neutral / discouraged]
🛡️ Defensive stance: [preferred / neutral / not needed]

<b>What Would Change This</b>
• [Simple condition 1]
• [Simple condition 2]

RULES:
- NO technical jargon
- Under 80 words total
- Focus on ACTION, not analysis
- Use the exact emoji and formatting shown"""

            user_prompt = f"""Regime: {regime}
Bull probability: {prob_bull}%
Bear probability: {prob_bear}%
Confidence: {confidence}%
BTC: ${btc_price:,.0f}
RSI: {rsi:.0f}
Days in regime: {days_in_regime}

Generate compact analysis."""

            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=300
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"AI generation error: {e}")
            return self._fallback_analysis(engine_result)
    
    def _fallback_analysis(self, engine_result: dict) -> str:
        """Fallback analysis without AI."""
        regime = engine_result.get("regime", "TRANSITION")
        
        if regime == "BEAR":
            return """◼️ Market is weak. Selling pressure dominates. Wait for clearer signals before buying.

<b>Positioning</b>
🟢 New longs: high risk
⚠️ Aggressive buying: discouraged
🛡️ Defensive stance: preferred

<b>What Would Change This</b>
• Sustained price recovery
• Shift in market sentiment"""
        elif regime == "BULL":
            return """◼️ Market is strong. Buyers are in control. Dips may offer opportunities.

<b>Positioning</b>
🟢 New longs: low risk
⚠️ Aggressive buying: neutral
🛡️ Defensive stance: not needed

<b>What Would Change This</b>
• Significant price breakdown
• Loss of buying momentum"""
        else:
            return """◼️ Market is undecided. Neither buyers nor sellers dominate. Best to wait for direction.

<b>Positioning</b>
🟢 New longs: moderate risk
⚠️ Aggressive buying: discouraged
🛡️ Defensive stance: preferred

<b>What Would Change This</b>
• Clear trend formation
• Decisive breakout in either direction"""
    
    def format_message(self, engine_result: dict, trigger_reason: str, ai_analysis: str) -> str:
        """Format message for Telegram."""
        regime = engine_result.get("regime", "TRANSITION")
        probs = engine_result.get("probabilities", {})
        meta = engine_result.get("metadata", {})
        conf = engine_result.get("confidence", {})
        
        btc_price = meta.get("btc_price", 0)
        eth_price = meta.get("eth_price", 0)
        confidence = int(conf.get("quality_adjusted", 0) * 100)
        
        # Timestamp
        now = datetime.now(timezone.utc)
        timestamp = now.strftime('%d %b %Y · %H:%M UTC')
        
        # Regime display
        if regime == "BULL":
            regime_emoji = "🟢"
            regime_name = "Bullish"
        elif regime == "BEAR":
            regime_emoji = "🔴"
            regime_name = "Bearish"
        else:
            regime_emoji = "🟡"
            regime_name = "Transition"
        
        # Confidence bar
        filled = int(confidence / 10)
        empty = 10 - filled
        conf_bar = '█' * filled + '░' * empty
        
        # Key levels
        support = int((btc_price * 0.92) // 1000) * 1000
        resistance = int((btc_price * 1.08) // 1000) * 1000
        current_k = int(btc_price // 1000)
        price_scale = f"📉 ${support//1000}k ····· ${current_k}k ····· ${resistance//1000}k 📈"
        
        message = f"""<b>BTC/USD</b> · {timestamp}

{regime_emoji} <b>{regime_name}</b>
[{conf_bar}] {confidence}%

<b>Prices</b>
BTC ${btc_price:,.0f}
ETH ${eth_price:,.0f}

{ai_analysis}

<b>Key Levels</b>
{price_scale}

<i>OracAI Radar</i>"""
        
        return message
    
    def publish_telegram(self, message: str) -> bool:
        """Publish to Telegram public channel."""
        if not self.telegram_token or not self.public_channel_id:
            logger.error("❌ Telegram credentials missing")
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                'chat_id': self.public_channel_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                logger.info("✓ Published to Telegram")
                return True
            else:
                logger.error(f"Telegram error: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Telegram exception: {e}")
            return False
    
    def run(self) -> bool:
        """Main run method."""
        logger.info("=" * 50)
        logger.info("OracAI Public Channel Publisher v1.0")
        logger.info("=" * 50)
        
        if not ENGINE_AVAILABLE:
            logger.error("❌ Engine not available")
            return False
        
        # 1. Run engine
        logger.info("📡 Fetching data and running engine...")
        try:
            raw_data = fetch_all_data()
            engine = RegimeEngine()
            result = engine.process(raw_data)
            logger.info(f"✓ Engine: regime={result.get('regime')}")
        except Exception as e:
            logger.error(f"❌ Engine error: {e}")
            return False
        
        # 2. Check triggers
        logger.info("🎯 Checking triggers...")
        should_publish, trigger_reason = self.check_triggers(result)
        
        if not should_publish:
            logger.info("ℹ️ No triggers met - skipping publication")
            # Update state anyway
            self.state["last_regime"] = result.get("regime")
            self._save_state()
            return True
        
        logger.info(f"✓ Trigger: {trigger_reason}")
        
        # 3. Generate AI analysis
        logger.info("🤖 Generating AI analysis...")
        ai_analysis = self.generate_ai_analysis(result)
        
        # 4. Format message
        logger.info("📝 Formatting message...")
        message = self.format_message(result, trigger_reason, ai_analysis)
        
        # 5. Publish
        logger.info("📤 Publishing to channel...")
        success = self.publish_telegram(message)
        
        if success:
            # Update state with global cooldown timestamp
            self.state["last_publish"] = datetime.now(timezone.utc).isoformat()
            self.state["last_regime"] = result.get("regime")
            self._save_state()
            logger.info("🎉 Successfully published!")
        else:
            logger.error("💥 Publication failed")
        
        return success


def main():
    publisher = PublicChannelPublisher()
    success = publisher.run()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
