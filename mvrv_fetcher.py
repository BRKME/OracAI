"""
mvrv_fetcher.py — fetch current & historical MVRV (and realized price) from the
CoinMetrics Community API (free, no key, daily granularity).

This is the SAME series already stored in data/btc.csv as CapMVRVCur, so the
cycle layer stays consistent between backtest and production.

CoinMetrics community endpoint:
  https://community-api.coinmetrics.io/v4/timeseries/asset-metrics
  metrics: CapMVRVCur (MVRV), CapRealUSD (realized cap), PriceUSD
Free tier: community metrics, daily frequency, ~no auth.

NOTE: this module makes outbound HTTPS calls to community-api.coinmetrics.io.
Run it where that host is reachable (your VPS / GitHub Actions). It is network-
isolated in some sandboxes, so test live on the host.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import requests

logger = logging.getLogger(__name__)

CM_BASE = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"


def fetch_mvrv_series(asset: str = "btc", days: int = 120) -> Optional[List[Dict]]:
    """Return list of {date: 'YYYY-MM-DD', mvrv: float} oldest→newest, or None."""
    start = (datetime.now(tz=None) - timedelta(days=days + 5)).strftime("%Y-%m-%d")
    params = {
        "assets": asset,
        "metrics": "CapMVRVCur",
        "frequency": "1d",
        "start_time": start,
        "page_size": 10000,
    }
    try:
        resp = requests.get(CM_BASE, params=params, timeout=20)
        if resp.status_code != 200:
            logger.warning("CoinMetrics MVRV error %s: %s", resp.status_code, resp.text[:200])
            return None
        rows = resp.json().get("data", [])
        out = []
        for r in rows:
            v = r.get("CapMVRVCur")
            if v is None:
                continue
            out.append({"date": r["time"][:10], "mvrv": float(v)})
        out.sort(key=lambda x: x["date"])
        return out or None
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to fetch MVRV: %s", e)
        return None


def fetch_mvrv_snapshot(asset: str = "btc") -> Optional[Dict]:
    """Return {'mvrv': latest, 'mvrv_peak_90d': trailing-90d max, 'date': ...}.

    mvrv_peak_90d is what the cycle layer needs to detect a drawdown that began
    from an overheated top even after MVRV has compressed.
    """
    series = fetch_mvrv_series(asset, days=95)
    if not series:
        return None
    vals = [s["mvrv"] for s in series]
    latest = series[-1]
    return {
        "date": latest["date"],
        "mvrv": latest["mvrv"],
        "mvrv_peak_90d": max(vals[-90:]),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    snap = fetch_mvrv_snapshot("btc")
    print("MVRV snapshot:", snap)
