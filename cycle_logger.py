"""
cycle_logger.py — append one daily row of the cycle layer's reading to
state/cycle_log.csv. Run by the cycle_layer_log workflow on a daily cron.

No Telegram, no publishing. Pure observation data collection so the cycle
layer can be watched on live data before it's ever wired into the bot.

Idempotent per day: if today's date is already logged, it overwrites that row
instead of appending a duplicate (safe to re-run / manual dispatch).
"""
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from cycle_layer import build_cycle_card

logging.basicConfig(level=logging.INFO)
LOG = Path(__file__).parent / "state" / "cycle_log.csv"
FIELDS = ["date_utc", "price", "mvrv", "mvrv_peak_90d", "mayer",
          "dd_from_ath_pct", "zone", "drawdown_call", "confidence",
          "engine_regime", "engine_risk_state", "mvrv_source"]


def _load_closes():
    """Daily closes oldest→newest from data/external/btc_ohlcv.csv (live-updated
    by data_refresh), falling back to data/btc.csv."""
    ext = Path(__file__).parent / "data" / "external" / "btc_ohlcv.csv"
    if ext.exists():
        df = pd.read_csv(ext, parse_dates=["date"]).sort_values("date")
        return df["close"].values
    df = pd.read_csv(Path(__file__).parent / "data" / "btc.csv", parse_dates=["time"])
    df = df[df["PriceUSD"].notna() & (df["PriceUSD"] > 0)].sort_values("time")
    return df["PriceUSD"].values


def _load_engine_output():
    """Pull the engine's latest regime from state/last_output.json if present."""
    import json
    p = Path(__file__).parent / "state" / "last_output.json"
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return None


def main():
    closes = _load_closes()
    engine = _load_engine_output()
    card = build_cycle_card(closes, engine)
    v = card["valuation"]
    m = v["metrics"]
    d = card.get("direction") or {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    row = {
        "date_utc": today,
        "price": m["price"],
        "mvrv": m["mvrv"],
        "mvrv_peak_90d": m["mvrv_peak_90d"],
        "mayer": m["mayer_multiple"],
        "dd_from_ath_pct": m["drawdown_from_ath_pct"],
        "zone": v["zone"],
        "drawdown_call": v["drawdown_call"],
        "confidence": v["confidence"],
        "engine_regime": d.get("regime"),
        "engine_risk_state": d.get("risk_state"),
        "mvrv_source": v["mvrv_source"],
    }

    rows = []
    if LOG.exists():
        with open(LOG, newline="") as f:
            rows = [r for r in csv.DictReader(f) if r.get("date_utc") != today]
    rows.append(row)
    rows.sort(key=lambda r: r["date_utc"])

    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    logging.info("Logged %s: zone=%s call=%s mvrv=%s (%s)",
                 today, v["zone"], v["drawdown_call"], m["mvrv"], v["mvrv_source"])
    print(f"OK {today}: {v['zone']} / {v['drawdown_call']} / MVRV {m['mvrv']}")


if __name__ == "__main__":
    main()
