"""
common.py — общие хелперы OracAI.

Создан 08.08.2026 при архитектурном аудите: одни и те же функции жили
копиями в разных модулях и успевали разойтись между собой.

Что здесь:
  • send_telegram_message() — низкоуровневая отправка (была в 3 копиях)
  • calculate_rsi()          — RSI по списку цен (была в 2 копиях)
  • STABLECOIN_SYMBOLS / MAJOR_SYMBOLS — символьные наборы токенов
  • is_stable() / is_major() / token_tier() — классификация токена

Чего здесь НЕТ и почему:
  • telegram_bot.send_telegram() — не дубль: он форматирует output и
    оборачивает в Markdown-блок, это своя логика поверх отправки.
  • calculate_rsi() из backtest_v5 / backtest_honest — работают с
    pd.Series и возвращают Series, это другой контракт (бэктест-домен).
  • lp_config.STABLECOIN_ADDRESSES — лукап по КОНТРАКТНОМУ АДРЕСУ,
    принципиально другая структура. Не путать с символами отсюда.
"""

import logging
import os
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

TELEGRAM_MAX_LEN = 4096


def send_telegram_message(text: str,
                          parse_mode: Optional[str] = None,
                          timeout: int = 10,
                          truncate: bool = True) -> bool:
    """Отправить сообщение в Telegram.

    parse_mode оставлен параметром намеренно: lp_system слал текст БЕЗ
    разметки, а lp_monitor / lp_opportunities — с parse_mode="HTML".
    Насильно включать HTML для всех нельзя: символы < и & в тексте
    сломали бы отправку. Каждый вызывающий сохраняет своё поведение.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        logger.warning("Telegram credentials not set")
        return False

    if truncate and len(text) > TELEGRAM_MAX_LEN:
        text = text[:TELEGRAM_MAX_LEN - 6] + "\n..."

    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        response = requests.post(url, data=payload, timeout=timeout)

        if response.status_code == 200:
            logger.info("✓ Telegram sent")
            return True

        logger.error(f"Telegram error: {response.status_code} - {response.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"Telegram exception: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# ИНДИКАТОРЫ
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_rsi(closes: List[float], period: int = 14) -> float:
    """RSI по списку цен закрытия (простое усреднение за period).

    Контракт сохранён 1-в-1 с прежними реализациями в data_pipeline.py и
    cycle_metrics_collector.py — они были численно идентичны (проверено).
    ВАЖНО: при нехватке данных возвращается 50.0 (нейтраль), а НЕ None —
    вызывающие рассчитывают именно на это.
    """
    if not closes or len(closes) < period + 1:
        return 50.0

    try:
        closes = [float(x) for x in closes if x is not None]
    except (TypeError, ValueError):
        return 50.0

    if len(closes) < period + 1:
        return 50.0

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period or 1e-10

    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


# ═══════════════════════════════════════════════════════════════════════════════
# КЛАССИФИКАЦИЯ ТОКЕНОВ
# ═══════════════════════════════════════════════════════════════════════════════

STABLECOIN_SYMBOLS = {
    "USDT", "USDC", "USD₮0", "BUSD", "DAI", "FRAX", "TUSD", "USDP", "GUSD",
    "USDC.E", "USDT.E", "USDC.e", "USDT.e",
    "USDC-CIRCLE", "USDCE", "USDT-TETHER", "FDUSD",
}

MAJOR_SYMBOLS = {
    "WETH", "ETH", "WBNB", "BNB", "WBTC", "BTC", "BTCB",
    "WMATIC", "MATIC", "WAVAX", "AVAX",
}

# Токены без разлоков — PoW/майнинговая эмиссия, вестинга нет
NO_UNLOCK_SYMBOLS = {"ZEC", "BTC", "WBTC", "BTCB", "LTC", "XMR", "DOGE"}


def is_stable(symbol: str) -> bool:
    return symbol in STABLECOIN_SYMBOLS


def is_major(symbol: str) -> bool:
    return symbol in MAJOR_SYMBOLS


def token_tier(symbol: str) -> int:
    """Ярус риска токена: 0 = стейбл, 1 = мажор (ETH/BNB/BTC), 2 = альт.

    Используется в аллокации: экспозиция пары назначается САМОМУ РИСКОВОМУ
    токену. USDT-ETH → 100% ETH, BNB-ASTER → 100% ASTER, равные ярусы → 50/50.
    """
    if symbol in STABLECOIN_SYMBOLS:
        return 0
    if symbol in MAJOR_SYMBOLS:
        return 1
    return 2
