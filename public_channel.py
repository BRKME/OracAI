#!/usr/bin/env python3
"""
OracAI Public Channel Publisher v2.0
Minimalist design with BTC chart and clear signals.

Features:
- BTC chart with EMA/RSI
- Clear signal: BUY / SELL / WAIT
- Minimalist format
- Global cooldown (4h)
"""

import os
import sys
import json
import logging
import io
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import engine and chart
try:
    from engine import RegimeEngine
    from data_pipeline import fetch_all_data
    from chart_generator import generate_chart
    ENGINE_AVAILABLE = True
except ImportError as e:
    ENGINE_AVAILABLE = False
    logger.error(f"Engine import failed: {e}")

# OpenAI (optional)
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# State file
STATE_FILE = "state/public_channel_state.json"

# Global cooldown (hours)
GLOBAL_COOLDOWN_HOURS = 4

# Trigger thresholds
TRIGGER_24H_CHANGE = 5.0
ROUND_LEVEL_STEP = 5000


class PublicChannelPublisher:
    """Publisher for public Telegram channel."""
    
    def __init__(self):
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.public_channel_id = os.getenv('TELEGRAM_PUBLIC_CHANNEL_ID')
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        
        # OpenAI client
        self.openai_client = None
        if OPENAI_AVAILABLE and self.openai_api_key:
            try:
                self.openai_client = OpenAI(api_key=self.openai_api_key)
            except Exception as e:
                logger.warning(f"OpenAI init failed: {e}")
        
        self.state = self._load_state()
    
    def _load_state(self) -> Dict:
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"State load error: {e}")
        return {
            "last_publish": None,
            "last_regime": None,
            "last_round_level": None,
        }
    
    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"State save error: {e}")
    
    def _check_global_cooldown(self) -> Tuple[bool, float]:
        """Check if global cooldown has passed."""
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
        except:
            return True, 0
    
    def _get_round_level(self, price: float) -> int:
        return int(price // ROUND_LEVEL_STEP) * ROUND_LEVEL_STEP
    
    def check_triggers(self, engine_result: dict) -> Tuple[bool, str]:
        """Check publication triggers with global cooldown."""
        
        # Global cooldown first
        cooldown_ok, hours_remaining = self._check_global_cooldown()
        if not cooldown_ok:
            logger.info(f"⏳ Cooldown: {hours_remaining:.1f}h remaining")
            return False, ""
        
        triggers = []
        
        regime = engine_result.get("regime", "TRANSITION")
        meta = engine_result.get("metadata", {})
        
        btc_price = meta.get("btc_price") or 0
        vol_z = meta.get("vol_z") or 0
        
        # 1. Regime change
        last_regime = self.state.get("last_regime")
        if last_regime and regime != last_regime:
            triggers.append(f"{last_regime} → {regime}")
            logger.info(f"✓ Trigger: Regime change")
        else:
            logger.info(f"   Regime: {regime} (no change)")
        
        # 2. Round level breakout
        if btc_price and btc_price > 0:
            current_level = self._get_round_level(btc_price)
            last_level = self.state.get("last_round_level")
            
            if last_level is None:
                self.state["last_round_level"] = current_level
                logger.info(f"   Level: ${current_level:,} (init)")
            elif current_level != last_level:
                direction = "↗" if current_level > last_level else "↘"
                triggers.append(f"{direction} ${current_level:,}")
                self.state["last_round_level"] = current_level
                logger.info(f"✓ Trigger: Level ${current_level:,}")
            else:
                logger.info(f"   Level: ${current_level:,} (no change, next at ${current_level + ROUND_LEVEL_STEP:,} or ${current_level - ROUND_LEVEL_STEP:,})")
        
        # 3. High volatility
        if vol_z and vol_z > 2.0:
            triggers.append("High volatility")
            logger.info(f"✓ Trigger: Volatility ({vol_z:.2f})")
        else:
            logger.info(f"   Volatility: {vol_z:.2f} (need >2.0)")
        
        return len(triggers) > 0, " | ".join(triggers)
    
    def determine_signal(self, engine_result: dict) -> Tuple[str, str, str]:
        """
        Determine clear signal: BUY / SELL / WAIT
        Returns (signal, emoji, color_emoji)
        """
        regime = engine_result.get("regime", "TRANSITION")
        probs = engine_result.get("probabilities", {})
        meta = engine_result.get("metadata", {})
        risk = engine_result.get("risk", {})
        conf = engine_result.get("confidence", {})
        
        prob_bull = probs.get("BULL") or 0
        prob_bear = probs.get("BEAR") or 0
        risk_level = risk.get("risk_level") or 0
        confidence = conf.get("quality_adjusted") or 0
        vol_z = meta.get("vol_z") or 0
        
        rsi_data = meta.get("rsi") or {}
        rsi = rsi_data.get("rsi_1d") or 50
        
        # High volatility = WAIT
        if vol_z > 2.0:
            return "WAIT", "⏸️", "🟡"
        
        # Strong BULL signals
        if regime == "BULL" and prob_bull > 0.6 and confidence > 0.4:
            if rsi < 35:
                return "BUY", "🎯", "🟢"  # Oversold in bull
            elif rsi > 70:
                return "WAIT", "⏸️", "🟡"  # Overbought
            else:
                return "BUY", "📈", "🟢"
        
        # Strong BEAR signals
        if regime == "BEAR" and prob_bear > 0.6 and confidence > 0.4:
            if rsi > 65:
                return "SELL", "🎯", "🔴"  # Overbought in bear
            elif rsi < 30:
                return "WAIT", "⏸️", "🟡"  # Oversold
            else:
                return "SELL", "📉", "🔴"
        
        # Transition / Unclear
        return "WAIT", "⏸️", "🟡"
    
    def generate_short_analysis(self, engine_result: dict, signal: str) -> str:
        """Generate 1-2 sentence analysis."""
        if not self.openai_client:
            return self._fallback_short_analysis(engine_result, signal)
        
        try:
            regime = engine_result.get("regime", "TRANSITION")
            probs = engine_result.get("probabilities", {})
            meta = engine_result.get("metadata", {})
            
            prob_bull = int((probs.get("BULL") or 0) * 100)
            prob_bear = int((probs.get("BEAR") or 0) * 100)
            btc_price = meta.get("btc_price") or 0
            rsi_data = meta.get("rsi") or {}
            rsi = rsi_data.get("rsi_1d") or 50
            
            prompt = f"""Regime: {regime}, Bull: {prob_bull}%, Bear: {prob_bear}%, RSI: {rsi:.0f}, Signal: {signal}

Write exactly 2 short sentences:
1. What's happening (simple language)
2. What to do

Example: "Market consolidating near $70k. Wait for clear breakout."

No emojis. Under 20 words total."""

            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=50
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"AI error: {e}")
            return self._fallback_short_analysis(engine_result, signal)
    
    def _fallback_short_analysis(self, engine_result: dict, signal: str) -> str:
        regime = engine_result.get("regime", "TRANSITION")
        
        if signal == "BUY":
            return "Bulls in control. Good entry opportunity."
        elif signal == "SELL":
            return "Bears dominating. Consider taking profits."
        else:
            if regime == "BULL":
                return "Bullish but extended. Wait for pullback."
            elif regime == "BEAR":
                return "Bearish but oversold. Wait for confirmation."
            else:
                return "Market consolidating. No clear direction yet."
    
    def format_message(self, engine_result: dict) -> str:
        """Format minimalist message."""
        meta = engine_result.get("metadata", {})
        probs = engine_result.get("probabilities", {})
        
        btc_price = meta.get("btc_price") or 0
        eth_price = meta.get("eth_price") or 0
        
        # Get signal
        signal, signal_emoji, color = self.determine_signal(engine_result)
        
        # Short analysis
        analysis = self.generate_short_analysis(engine_result, signal)
        
        # Probabilities
        prob_bull = int((probs.get("BULL") or 0) * 100)
        prob_bear = int((probs.get("BEAR") or 0) * 100)
        
        # Levels (handle zero price)
        if btc_price > 0:
            support = int((btc_price * 0.92) // 1000) * 1000
            resistance = int((btc_price * 1.08) // 1000) * 1000
            current_k = int(btc_price) // 1000
        else:
            support = 0
            resistance = 0
            current_k = 0
        
        # Timestamp
        now = datetime.now(timezone.utc)
        timestamp = now.strftime('%d %b %H:%M UTC')
        
        # ETH line (only if price available)
        eth_line = f"\nETH ${eth_price:,.0f}" if eth_price and eth_price > 0 else ""
        
        # Build message
        message = f"""<b>BTC ${btc_price:,.0f}</b>

{color} <b>{signal}</b> {signal_emoji}

{analysis}

━━━━━━━━━━━━━━
${support//1000}k ▽ support
<b>${current_k}k</b> ◆ current
${resistance//1000}k △ resistance
━━━━━━━━━━━━━━

Bull {prob_bull}% · Bear {prob_bear}%{eth_line}

<i>OracAI · {timestamp}</i>"""
        
        return message
    
    def publish_with_chart(self, message: str) -> bool:
        """Publish photo with caption to Telegram."""
        if not self.telegram_token or not self.public_channel_id:
            logger.error("❌ Telegram credentials missing")
            return False
        
        logger.info(f"📍 Target channel: {self.public_channel_id}")
        
        # Generate chart
        logger.info("📈 Generating chart...")
        chart_buf = generate_chart("BTC-USD", days_to_show=90)
        
        if not chart_buf:
            logger.warning("⚠️ Chart failed, sending text only")
            return self._publish_text_only(message)
        
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendPhoto"
            
            chart_buf.seek(0)
            files = {'photo': ('btc_chart.png', chart_buf, 'image/png')}
            data = {
                'chat_id': self.public_channel_id,
                'caption': message,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, files=files, data=data, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                chat_info = result.get('result', {}).get('chat', {})
                logger.info(f"✓ Published to: {chat_info.get('title', 'unknown')} (ID: {chat_info.get('id')})")
                return True
            else:
                logger.error(f"Telegram error: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Publish error: {e}")
            return False
    
    def _publish_text_only(self, message: str) -> bool:
        """Fallback: text only."""
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            response = requests.post(url, json={
                'chat_id': self.public_channel_id,
                'text': message,
                'parse_mode': 'HTML'
            }, timeout=10)
            return response.status_code == 200
        except:
            return False
    
    def run(self, force: bool = False) -> bool:
        """Main run method."""
        logger.info("=" * 50)
        logger.info("OracAI Public Channel v2.0")
        logger.info("=" * 50)
        
        if not ENGINE_AVAILABLE:
            logger.error("❌ Engine not available")
            return False
        
        # 1. Run engine
        logger.info("📡 Running engine...")
        try:
            raw_data = fetch_all_data()
            engine = RegimeEngine()
            result = engine.process(raw_data)
            logger.info(f"✓ Regime: {result.get('regime')}")
        except Exception as e:
            logger.error(f"❌ Engine error: {e}")
            return False
        
        # 2. Check triggers (skip if force)
        if force:
            logger.info("⚡ FORCE mode - skipping trigger check")
            trigger = "Manual"
        else:
            logger.info("🎯 Checking triggers...")
            should_publish, trigger = self.check_triggers(result)
            
            if not should_publish:
                logger.info("ℹ️ No triggers - skipping")
                self.state["last_regime"] = result.get("regime")
                self._save_state()
                return True
        
        logger.info(f"✓ Trigger: {trigger}")
        
        # 3. Format message
        logger.info("📝 Formatting...")
        message = self.format_message(result)
        
        # Preview
        print("\n" + "="*40)
        print(message)
        print("="*40 + "\n")
        
        # 4. Publish with chart
        logger.info("📤 Publishing...")
        success = self.publish_with_chart(message)
        
        if success:
            self.state["last_publish"] = datetime.now(timezone.utc).isoformat()
            self.state["last_regime"] = result.get("regime")
            self._save_state()
            logger.info("🎉 Done!")
        
        return success


def main():
    import argparse
    parser = argparse.ArgumentParser(description='OracAI Public Channel Publisher')
    parser.add_argument('--force', action='store_true', help='Force publish (bypass triggers)')
    args = parser.parse_args()
    
    publisher = PublicChannelPublisher()
    success = publisher.run(force=args.force)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
