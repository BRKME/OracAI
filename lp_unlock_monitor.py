"""
LP Token Unlock Monitor v1.0

Checks LP portfolio tokens for upcoming unlock events.
Runs weekly (Saturday) — gives time to adjust positions before unlocks.

Uses OpenAI with web search to find unlock schedules.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import List, Set

import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

POSITIONS_FILE = "state/lp_positions.json"

# Stablecoins and wrapped assets — no unlock risk
SKIP_TOKENS = {
    "USDT", "USDC", "USD₮0", "BUSD", "DAI", "FRAX", "TUSD", "USDP",
    "WETH", "WBNB", "WBTC", "WMATIC", "WAVAX",
    "ETH", "BNB", "BTC", "MATIC", "AVAX",
    "ZEC",  # PoW coin, no unlocks
}


def get_portfolio_tokens() -> List[dict]:
    """Get alt tokens from LP positions with their TVL."""
    if not os.path.exists(POSITIONS_FILE):
        logger.warning(f"No positions file: {POSITIONS_FILE}")
        return []
    
    with open(POSITIONS_FILE) as f:
        positions = json.load(f).get("positions", [])
    
    token_exposure = {}  # token -> total USD exposure
    
    for p in positions:
        balance = p.get("balance_usd", 0)
        for key in ("token0_symbol", "token1_symbol"):
            token = p.get(key, "")
            if token and token not in SKIP_TOKENS:
                if token not in token_exposure:
                    token_exposure[token] = 0
                token_exposure[token] += balance / 2  # ~50% of pair
    
    tokens = [
        {"symbol": symbol, "exposure_usd": exposure}
        for symbol, exposure in sorted(token_exposure.items(), key=lambda x: -x[1])
    ]
    
    logger.info(f"Alt tokens in portfolio: {[t['symbol'] for t in tokens]}")
    return tokens


def check_unlocks_ai(tokens: List[dict]) -> str:
    """Use OpenAI with web search to check for upcoming unlocks."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY not set")
        return ""
    
    token_list = ", ".join([f"{t['symbol']} (${t['exposure_usd']:,.0f} exposure)" for t in tokens])
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    next_week = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
    
    prompt = f"""Check for upcoming token unlock events for these crypto tokens: {token_list}

Today is {today}. Check for any unlocks scheduled in the next 14 days (until {next_week} and the week after).

For each token, search for:
1. Token unlock schedule (vesting, team, investor unlocks)
2. Any large unlock events coming up
3. Cliff unlocks or significant vesting releases

Respond in this exact format:

🔓 TOKEN UNLOCK CHECK | {today}
Tokens checked: [list]

For each token with upcoming unlocks:
⚠️ TOKEN_SYMBOL
  Date: YYYY-MM-DD
  Amount: X% of circulating supply (or $Xm)
  Type: team/investor/ecosystem/etc
  Risk: HIGH/MEDIUM/LOW

If no unlocks found for a token:
✅ TOKEN_SYMBOL — no unlocks in next 14 days

End with a one-line summary recommendation.
Keep it concise — this goes to Telegram."""

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o-mini-search-preview",
                "messages": [
                    {"role": "system", "content": "You are a crypto research assistant. Search the web for token unlock schedules. Be factual and concise."},
                    {"role": "user", "content": prompt}
                ],
                "web_search_options": {"search_context_size": "medium"}
            },
            timeout=60
        )
        
        if response.status_code != 200:
            logger.error(f"OpenAI API error: {response.status_code} - {response.text[:200]}")
            return ""
        
        data = response.json()
        result = data["choices"][0]["message"]["content"]
        logger.info(f"AI response received ({len(result)} chars)")
        return result
        
    except Exception as e:
        logger.error(f"OpenAI API exception: {e}")
        return ""


def send_telegram(message: str) -> bool:
    """Send message to Telegram."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        logger.warning("Telegram credentials not set")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        response = requests.post(url, data={
            "chat_id": chat_id,
            "text": message,
        }, timeout=10)
        
        if response.status_code == 200:
            logger.info("✓ Telegram sent")
            return True
        else:
            logger.error(f"Telegram error: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Telegram exception: {e}")
        return False


def main():
    logger.info("=" * 50)
    logger.info("LP TOKEN UNLOCK MONITOR v1.0")
    logger.info("=" * 50)
    
    # Get tokens
    tokens = get_portfolio_tokens()
    
    if not tokens:
        logger.info("No alt tokens in portfolio — nothing to check")
        return 0
    
    # Check unlocks via AI
    report = check_unlocks_ai(tokens)
    
    if not report:
        logger.error("Failed to get unlock data")
        return 1
    
    print(f"\n{report}\n")
    
    # Send to Telegram
    send_telegram(report)
    
    logger.info("Done!")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
