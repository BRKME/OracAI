#!/usr/bin/env python3
"""
Chart Generator for Telegram Bot
Daily BTC chart with MA50, MA200, and RSI
"""

import io
import logging
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None

logger = logging.getLogger(__name__)

# Style
plt.style.use('dark_background')


def fetch_btc_data(days: int = 400) -> pd.DataFrame:
    """Fetch BTC daily data."""
    if yf is None:
        logger.error("yfinance not installed")
        return None
    
    end = datetime.now()
    start = end - timedelta(days=days + 250)  # Extra for MA200
    
    try:
        df = yf.download("BTC-USD", start=start, end=end, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        logger.info(f"Fetched {len(df)} days of BTC data")
        return df
    except Exception as e:
        logger.error(f"Failed to fetch BTC data: {e}")
        return None


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add MA50, MA200, RSI."""
    df = df.copy()
    
    # Moving averages
    df['ma50'] = df['close'].rolling(window=50).mean()
    df['ma200'] = df['close'].rolling(window=200).mean()
    
    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    return df


def generate_chart(days_to_show: int = 365) -> io.BytesIO:
    """
    Generate chart PNG and return as BytesIO.
    
    Returns None if failed.
    """
    # Fetch data
    df = fetch_btc_data(days_to_show + 30)
    if df is None or len(df) < 50:
        logger.error("Not enough data for chart")
        return None
    
    # Calculate indicators
    df = calculate_indicators(df)
    
    # Take last N days
    df = df.tail(days_to_show)
    
    # Create figure with 2 subplots (price + RSI)
    fig, (ax1, ax2) = plt.subplots(
        2, 1, 
        figsize=(14, 8),
        gridspec_kw={'height_ratios': [3, 1]},
        sharex=True
    )
    fig.patch.set_facecolor('#1a1a2e')
    ax1.set_facecolor('#1a1a2e')
    ax2.set_facecolor('#1a1a2e')
    
    # === Price Chart ===
    dates = df.index
    
    # Candlesticks (simplified as line + fill)
    ax1.plot(dates, df['close'], color='#00d4ff', linewidth=1.5, label='BTC')
    
    # Fill between open/close for candle effect
    ax1.fill_between(dates, df['low'], df['high'], alpha=0.1, color='#00d4ff')
    
    # MA lines
    ax1.plot(dates, df['ma50'], color='#ffa500', linewidth=1.2, label='MA50', linestyle='--')
    ax1.plot(dates, df['ma200'], color='#ff4444', linewidth=1.2, label='MA200', linestyle='--')
    
    # Current price annotation
    last_price = df['close'].iloc[-1]
    last_date = dates[-1]
    ax1.annotate(
        f'${last_price:,.0f}',
        xy=(last_date, last_price),
        xytext=(10, 0),
        textcoords='offset points',
        fontsize=12,
        fontweight='bold',
        color='#00d4ff',
        va='center'
    )
    
    # MA values
    ma50_val = df['ma50'].iloc[-1]
    ma200_val = df['ma200'].iloc[-1]
    if not np.isnan(ma50_val):
        ax1.annotate(f'MA50: ${ma50_val:,.0f}', xy=(0.02, 0.95), xycoords='axes fraction',
                     fontsize=10, color='#ffa500', va='top')
    if not np.isnan(ma200_val):
        ax1.annotate(f'MA200: ${ma200_val:,.0f}', xy=(0.02, 0.88), xycoords='axes fraction',
                     fontsize=10, color='#ff4444', va='top')
    
    ax1.set_ylabel('Price (USD)', fontsize=11, color='white')
    ax1.set_title('BTC/USD Daily', fontsize=14, fontweight='bold', color='white', pad=10)
    ax1.legend(loc='upper right', fontsize=9, framealpha=0.3)
    ax1.grid(True, alpha=0.2, linestyle='--')
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x/1000:.0f}K'))
    
    # === RSI Chart ===
    rsi = df['rsi']
    
    # RSI line
    ax2.plot(dates, rsi, color='#9966ff', linewidth=1.5)
    
    # Overbought/Oversold zones
    ax2.axhline(y=70, color='#ff4444', linestyle='--', alpha=0.5, linewidth=1)
    ax2.axhline(y=30, color='#00ff88', linestyle='--', alpha=0.5, linewidth=1)
    ax2.axhline(y=50, color='#888888', linestyle='--', alpha=0.3, linewidth=1)
    
    # Fill zones
    ax2.fill_between(dates, 70, 100, alpha=0.1, color='#ff4444')
    ax2.fill_between(dates, 0, 30, alpha=0.1, color='#00ff88')
    
    # Current RSI
    last_rsi = rsi.iloc[-1]
    if not np.isnan(last_rsi):
        rsi_color = '#ff4444' if last_rsi > 70 else '#00ff88' if last_rsi < 30 else '#9966ff'
        ax2.annotate(
            f'RSI: {last_rsi:.0f}',
            xy=(last_date, last_rsi),
            xytext=(10, 0),
            textcoords='offset points',
            fontsize=11,
            fontweight='bold',
            color=rsi_color,
            va='center'
        )
    
    ax2.set_ylabel('RSI (14)', fontsize=11, color='white')
    ax2.set_ylim(0, 100)
    ax2.grid(True, alpha=0.2, linestyle='--')
    
    # X-axis formatting (monthly for year view)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax2.xaxis.set_major_locator(mdates.MonthLocator())
    plt.xticks(rotation=45, ha='right')
    
    # Timestamp
    fig.text(0.99, 0.01, f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M UTC")}',
             fontsize=8, color='#666666', ha='right', va='bottom')
    
    plt.tight_layout()
    
    # Save to BytesIO
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, facecolor='#1a1a2e', edgecolor='none')
    buf.seek(0)
    plt.close(fig)
    
    logger.info("Chart generated successfully")
    return buf


async def send_chart_to_telegram(bot_token: str, chat_id: str, caption: str = "") -> bool:
    """Send chart to Telegram."""
    import aiohttp
    
    chart_buf = generate_chart()
    if chart_buf is None:
        logger.error("Failed to generate chart")
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    
    data = aiohttp.FormData()
    data.add_field('chat_id', chat_id)
    data.add_field('photo', chart_buf, filename='btc_chart.png', content_type='image/png')
    if caption:
        data.add_field('caption', caption)
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, timeout=30) as response:
                if response.status == 200:
                    logger.info("Chart sent to Telegram")
                    return True
                else:
                    error = await response.text()
                    logger.error(f"Failed to send chart: {error}")
                    return False
    except Exception as e:
        logger.error(f"Error sending chart: {e}")
        return False


if __name__ == "__main__":
    # Test: save chart to file
    logging.basicConfig(level=logging.INFO)
    
    buf = generate_chart(90)
    if buf:
        with open("btc_chart.png", "wb") as f:
            f.write(buf.read())
        print("✅ Chart saved to btc_chart.png")
    else:
        print("❌ Failed to generate chart")
