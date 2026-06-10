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

    # ── Publish the ladder contract for hl_weekly_planner ──
    # v1.1: one-time percent-of-capital signals on zone transitions, with
    # persistent state (prev zone / spent budget) in state/ladder_state.json.
    try:
        import json as _json
        from cycle_ladder import compute_ladder, compute_signal
        days_above = 0
        if engine:
            days_above = int(
                ((engine.get("risk") or {}).get("days_above_sma200"))
                or ((engine.get("metadata") or {}).get("days_above_sma200"))
                or _find_days_above(engine) or 0)

        state_path = Path(__file__).parent / "state" / "ladder_state.json"
        try:
            lstate = _json.loads(state_path.read_text())
        except Exception:  # noqa: BLE001 — first run / corrupt -> fresh state
            lstate = {}
        prev_zone = lstate.get("prev_zone")

        signal = compute_signal(
            zone=v["zone"], prev_zone=prev_zone,
            drawdown_call=v["drawdown_call"],
            days_above_sma200=days_above,
            state=lstate.get("ladder", {}),
        )

        # persist memory for tomorrow's run
        new_state = {"prev_zone": v["zone"], "ladder": signal["new_state"],
                     "updated": today}
        state_path.write_text(_json.dumps(new_state, ensure_ascii=False, indent=1))

        # contract: keep v1 fields (multiplier) for compatibility + v1.1 signal
        ladder = compute_ladder(zone=v["zone"], drawdown_call=v["drawdown_call"],
                                days_above_sma200=days_above, mvrv=m["mvrv"])
        ladder["signal"] = {k: signal[k] for k in
                            ("policy_version", "action", "fraction_of_capital",
                             "fraction_of_stack", "trigger", "rationale")}
        ladder["date_utc"] = today
        ladder["price"] = m["price"]
        out = Path(__file__).parent / "state" / "cycle_ladder.json"
        out.write_text(_json.dumps(ladder, ensure_ascii=False, indent=1))
        print(f"ladder: {signal['action']} "
              f"cap={signal['fraction_of_capital']:g} "
              f"stack={signal['fraction_of_stack']:g} ({signal['trigger']})")

        # BUY/SELL are rare cycle events — push them to Telegram immediately.
        if signal["action"] in ("BUY", "SELL"):
            _send_signal_telegram(signal, m["price"], v["zone"])
    except Exception as e:  # noqa: BLE001 — ladder must never break the log
        logging.warning("ladder publish failed: %s", e)


def _send_signal_telegram(signal: dict, price: float, zone: str) -> None:
    """One-time cycle signals go straight to Telegram (HOLD stays silent)."""
    import os
    import requests
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("  (no telegram creds — signal printed only)")
        return
    if signal["action"] == "BUY":
        head = f"🟢 ЛЕСТНИЦА: ПОКУПКА на {signal['fraction_of_capital']*100:.0f}% капитала"
    else:
        head = f"🔴 ЛЕСТНИЦА: ФИКСАЦИЯ {signal['fraction_of_stack']*100:.0f}% стэка"
    msg = (f"{head}\nBTC ${price:,.0f} · зона {zone}\n"
           f"{signal['rationale']}\n"
           f"(событие цикла — срабатывает один раз; {signal['policy_version']})")
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": msg},
                      timeout=10).raise_for_status()
        print("  signal sent to telegram")
    except Exception as e:  # noqa: BLE001
        print(f"  telegram send failed: {e}")


def _find_days_above(engine: dict) -> int:
    """days_above_sma200 lives at different depths across engine versions —
    search shallowly rather than hardcoding one path."""
    for key in ("risk", "metadata", "operational_hints"):
        sub = engine.get(key)
        if isinstance(sub, dict) and "days_above_sma200" in sub:
            try:
                return int(sub["days_above_sma200"])
            except (TypeError, ValueError):
                pass
    if "days_above_sma200" in engine:
        try:
            return int(engine["days_above_sma200"])
        except (TypeError, ValueError):
            pass
    return 0


if __name__ == "__main__":
    main()
