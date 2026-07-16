"""
Production logger for OracAI regime engine.

Writes one CSV row per engine.process() call. The file accumulates over
time and becomes true out-of-sample validation data — things we can
measure against actual forward returns to see if the model's signals
predict what we think they predict.

What we log (all from engine output, nothing user-identifiable):
  - timestamp (UTC)
  - BTC price at the time
  - regime + confidence + probability distribution
  - bucket values (Momentum, Stability, Rotation, Sentiment, Macro)
  - risk_level + risk_state + final exposure_cap
  - SMA200 ratio + days above (Phase 4 fields)
  - data quality (completeness + failed sources)

What we do NOT log:
  - User IDs, Telegram chat data, personal info
  - Actual positions or portfolio data
  - Any authentication credentials

Why we collect this:
  - In-sample backtest (Phase 4) showed +0.4% alpha vs HODL on 5y.
  - But backtest uses historical data that was used to tune settings.
  - The ONLY way to know if the model genuinely has edge is to collect
    live out-of-sample data and measure after-the-fact.
  - 6-12 months of prod logs = real validation for any future Phase 5.
  - See BACKLOG.md §"Phase history" for context.
"""

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOG_FILE = Path("state/prod_log.csv")
RECOVERY_ALERT_STATE = Path("state/recovery_alert_state.json")
LOG_HEADER = [
    "timestamp_utc",
    "btc_price",
    "regime",
    "confidence",
    "P_BULL", "P_BEAR", "P_RANGE", "P_TRANSITION",
    "momentum", "stability", "rotation", "sentiment", "macro",
    "risk_level", "risk_state", "exposure_cap",
    "sma200_ratio", "days_above_sma200",
    "data_completeness", "failed_sources",
    "model_version", "days_in_regime", "vol_z",
    # Phase 4 fields that we want to track hit rate on
    "bear_confirmation_would_fire",
    "recovery_override_would_fire",
]


def _infer_bear_confirmation(output: dict) -> bool:
    """Mirror the bear_confirmation logic from telegram_bot.py — we log
    whether it WOULD fire so we can later measure hit rate on drops.
    """
    regime = output.get("regime", "")
    risk_level = output.get("risk", {}).get("risk_level", 0)
    meta = output.get("metadata", {}) or {}
    dd_from_high = meta.get("drawdown_from_high_90d", 0.0)

    # Fear & Greed from bucket_details
    bucket_details = output.get("bucket_details", {}) or {}
    sent_details = bucket_details.get("sentiment", {}) or {}
    fg_value = sent_details.get("fg_raw")

    rsi_data = meta.get("rsi", {}) or {}
    rsi_1d = rsi_data.get("rsi_1d")
    rsi_for_check = rsi_1d if rsi_1d is not None else 50

    return (
        regime == "BEAR"
        or (rsi_for_check < 50 and risk_level < -0.2)
        or (fg_value is not None and fg_value > 65 and dd_from_high < -15)
    )


def _infer_recovery_override(output: dict) -> bool:
    """Whether the Phase 4 recovery override WOULD fire."""
    meta = output.get("metadata", {}) or {}
    sma200_ratio = meta.get("sma200_ratio")
    days_above = meta.get("days_above_sma200", 0)
    if sma200_ratio is None:
        return False
    return (
        sma200_ratio > 1.0
        and days_above >= 10
        and not _infer_bear_confirmation(output)
    )


def decide_recovery_alert(state: dict, fires_now: bool, today) -> tuple:
    """Алерт на ПЕРЕХОД recovery_override, не на уровень.

    Re-entry — слабейшее место движка (−141пп в walk-forward 2023–24), фикс
    ни разу не работал вживую. Первый True — стартовый выстрел живого
    пре-регистрированного теста (analyze_prod_log.py §2): cap ≥0.95 за
    ≤5 логовых дней, без сброса 14д. Сброс тоже алертим — с числом дней,
    именно ложный сброс <14д проваливает тест.

    Возвращает (message | None, new_state).
    """
    was_active = bool(state.get("active"))
    if fires_now and not was_active:
        d = today.isoformat()
        msg = (
            "🟢 <b>OracAI: recovery_override сработал впервые вживую</b>\n"
            f"BTC ≥10 дней над SMA200 ({d}). Начался live re-entry тест "
            "(pre-registered): exposure_cap должен достичь ≥0.95 за "
            "≤5 логовых дней и не сброситься 14 дней.\n"
            "Следи: state/prod_log.csv · analyze_prod_log.py §2"
        )
        return msg, {"active": True, "since": d}
    if not fires_now and was_active:
        days = ""
        try:
            from datetime import date as _date
            since = _date.fromisoformat(state.get("since") or "")
            days = str((today - since).days)
        except (ValueError, TypeError):
            pass
        msg = (
            "⚠️ <b>OracAI: recovery_override сброшен</b>\n"
            f"BTC ушёл под SMA200 спустя {days} дн. после срабатывания. "
            "Если это <14 дней — ложный сброс, re-entry тест провален "
            "(analyze_prod_log.py §2)."
        )
        return msg, {"active": False, "since": None}
    if fires_now:
        return None, dict(state)
    return None, {"active": False, "since": None}


def _send_recovery_alert(msg: str) -> None:
    """Прямая отправка в Telegram; любой сбой глотается — алерт не имеет
    права ломать крон-поток (как и сам логгер)."""
    import os
    import requests
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("recovery alert: нет Telegram-кредов, алерт в лог: %s", msg)
        return
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
        timeout=10,
    ).raise_for_status()


def _check_recovery_transition(fires_now: bool) -> None:
    """Читает state, решает через decide_recovery_alert, шлёт и сохраняет."""
    import json as _json
    try:
        state = {}
        if RECOVERY_ALERT_STATE.exists():
            state = _json.loads(RECOVERY_ALERT_STATE.read_text()) or {}
        msg, new_state = decide_recovery_alert(
            state, fires_now, datetime.now(timezone.utc).date())
        if msg:
            _send_recovery_alert(msg)
        if new_state != state:
            RECOVERY_ALERT_STATE.parent.mkdir(exist_ok=True)
            RECOVERY_ALERT_STATE.write_text(_json.dumps(new_state))
    except Exception as e:  # noqa: BLE001 — см. контракт log_engine_output
        logger.warning("recovery alert failed: %s", e)


def log_engine_output(output: dict) -> None:
    """Append one row to prod_log.csv. Safe — any exception is swallowed
    so logging failure never breaks the main cron flow.
    """
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)

        meta = output.get("metadata", {}) or {}
        risk = output.get("risk", {}) or {}
        probs = output.get("probabilities", {}) or {}
        buckets = output.get("buckets", {}) or {}
        conf_obj = output.get("confidence", {}) or {}
        flags_meta = meta.get("failed_sources") or []

        row = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "btc_price": meta.get("btc_price"),
            "regime": output.get("regime", ""),
            "confidence": round(conf_obj.get("quality_adjusted", 0), 4),
            "P_BULL": probs.get("BULL"),
            "P_BEAR": probs.get("BEAR"),
            "P_RANGE": probs.get("RANGE"),
            "P_TRANSITION": probs.get("TRANSITION"),
            "momentum": buckets.get("Momentum"),
            "stability": buckets.get("Stability"),
            "rotation": buckets.get("Rotation"),
            "sentiment": buckets.get("Sentiment"),
            "macro": buckets.get("Macro"),
            "risk_level": risk.get("risk_level"),
            "risk_state": risk.get("risk_state", ""),
            "exposure_cap": output.get("exposure_cap"),
            "sma200_ratio": meta.get("sma200_ratio"),
            "days_above_sma200": meta.get("days_above_sma200", 0),
            "data_completeness": meta.get("data_completeness"),
            "failed_sources": ",".join(flags_meta) if flags_meta else "",
            "model_version": meta.get("model_version", ""),
            "days_in_regime": meta.get("days_in_regime", 0),
            "vol_z": meta.get("vol_z"),
            "bear_confirmation_would_fire": _infer_bear_confirmation(output),
            "recovery_override_would_fire": _infer_recovery_override(output),
        }

        # Алерт на переход recovery_override (старт/сброс live re-entry
        # теста) — внутри общего try, сбой не ломает ни лог, ни крон.
        _check_recovery_transition(bool(row["recovery_override_would_fire"]))

        # Create file with header if needed, else append
        file_exists = LOG_FILE.exists() and LOG_FILE.stat().st_size > 0
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_HEADER)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        logger.info(f"Prod log row written to {LOG_FILE.name}")

    except Exception as e:
        # Never let logging break production — just report and move on
        logger.warning(f"Prod logging failed (non-fatal): {e}")
