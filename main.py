"""
Market Regime Engine v3.4 + LP Intelligence v2.0.2 + Asset Allocation v1.6.0

Usage:
  python main.py              # Full run: regime → LP → allocation → Telegram
  python main.py --dry-run    # Compute and print, no Telegram
  python main.py --reset      # Reset state to defaults
  python main.py --no-lp      # Skip LP policy computation
  python main.py --no-aa      # Skip Asset Allocation computation
"""

import sys
import json
import logging
from pathlib import Path

# Load .env if exists (local dev)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from data_pipeline import fetch_all_data
from engine import RegimeEngine, default_state, save_state, STATE_FILE
from telegram_bot import send_telegram_with_chart, format_output
from lp_policy_engine import compute_lp_policy
from asset_allocation import compute_btc_eth_allocation

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def main():
    args = set(sys.argv[1:])

    # Reset state
    if "--reset" in args:
        logger.info("Resetting engine state...")
        save_state(default_state())
        logger.info("Done. State reset to defaults.")
        return

    dry_run = "--dry-run" in args
    skip_lp = "--no-lp" in args
    skip_aa = "--no-aa" in args

    # ── 1. Fetch data ─────────────────────────────────────
    logger.info("=" * 50)
    logger.info("MARKET REGIME ENGINE v3.4")
    logger.info("+ LP INTELLIGENCE v2.0.2")
    logger.info("+ ASSET ALLOCATION v1.6.0")
    logger.info("=" * 50)

    raw_data = fetch_all_data()

    # ── 2. Run regime engine ──────────────────────────────
    engine = RegimeEngine()
    output = engine.process(raw_data)

    # ── 3. Compute LP Policy ──────────────────────────────
    lp_policy = None
    if not skip_lp:
        try:
            lp_policy = compute_lp_policy(output)
            logger.info(f"LP Policy: {lp_policy.lp_regime.value} "
                       f"(risk_lp={lp_policy.risk_lp:+.2f}, "
                       f"quadrant={lp_policy.risk_quadrant.value})")
        except Exception as e:
            logger.error(f"LP Policy computation failed: {e}")
            lp_policy = None

    # ── 4. Compute Asset Allocation ───────────────────────
    allocation = None
    if not skip_aa:
        try:
            allocation = compute_btc_eth_allocation(output)
            
            btc_action = allocation["btc"]["action"]
            eth_action = allocation["eth"]["action"]
            btc_size = allocation["btc"]["size_pct"]
            eth_size = allocation["eth"]["size_pct"]
            
            logger.info(f"Asset Allocation: BTC={btc_action} ({btc_size:+.0%}), "
                       f"ETH={eth_action} ({eth_size:+.0%})")
            
            # Highlight tail risk
            if allocation["meta"].get("tail_risk_active"):
                polarity = allocation["meta"].get("tail_polarity", "downside")
                logger.warning(f"⚠️ TAIL RISK ({polarity.upper()}): Emergency action triggered!")
                
        except Exception as e:
            logger.error(f"Asset Allocation computation failed: {e}")
            allocation = None

    # ── 5. Print output ───────────────────────────────────
    print("\n" + format_output(output, lp_policy, allocation))

    # Save full JSON output for debugging
    output_file = Path("state/last_output.json")
    output_file.parent.mkdir(exist_ok=True)
    
    # Add LP policy to output for JSON
    if lp_policy:
        output["lp_policy"] = {
            "lp_regime": lp_policy.lp_regime.value,
            "risk_lp": lp_policy.risk_lp,
            "risk_quadrant": lp_policy.risk_quadrant.value,
            "fee_variance_ratio": lp_policy.fee_variance_ratio,
            "uncertainty_value": lp_policy.uncertainty_value,
            "trend_persistence": lp_policy.trend_persistence,
            "vol_structure": lp_policy.vol_structure,
            "max_exposure": lp_policy.max_exposure,
            "effective_exposure": lp_policy.effective_exposure,
            "range_width": lp_policy.range_width,
            "rebalance": lp_policy.rebalance,
            "hedge_recommended": lp_policy.hedge_recommended,
            "signals": lp_policy.signals,
            "confidence": lp_policy.confidence,
        }
    
    # Add allocation to output for JSON
    if allocation:
        output["asset_allocation"] = allocation
    
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, default=str)
    logger.info(f"Full output saved to {output_file}")

    # ── 6. Send Telegram ──────────────────────────────────
    if dry_run:
        logger.info("Dry run — skipping Telegram")
    else:
        send_telegram_with_chart(output, lp_policy, allocation)

    # ── 7. Summary ────────────────────────────────────────
    regime = output["regime"]
    risk_dir = output["risk"]["risk_level"]
    conf = output["confidence"]["quality_adjusted"]
    flags = output.get("risk_flags", [])
    
    logger.info("-" * 50)
    logger.info(f"REGIME: {regime} | Risk(dir): {risk_dir:+.2f} | Conf: {conf:.2f}")
    
    if lp_policy:
        logger.info(f"LP: {lp_policy.lp_regime.value} | "
                   f"Risk(lp): {lp_policy.risk_lp:+.2f} | "
                   f"Quadrant: {lp_policy.risk_quadrant.value} | "
                   f"Effective: {int(lp_policy.effective_exposure * 100)}%")
        
        # Highlight Q2 opportunity
        if lp_policy.risk_quadrant.value == "Q2":
            logger.info("💡 Q2 INSIGHT: Directional risk high, but LP opportunity exists!")
            logger.info(f"   LP book: {int(lp_policy.max_exposure * 100)}% → "
                       f"Risk-adjusted: {int(lp_policy.effective_exposure * 100)}%")
    
    if allocation:
        btc = allocation["btc"]
        eth = allocation["eth"]
        logger.info(f"ALLOCATION: BTC={btc['action']} ({btc['size_pct']:+.0%}) | "
                   f"ETH={eth['action']} ({eth['size_pct']:+.0%})")
        
        if btc.get("blocked_by"):
            logger.info(f"   BTC blocked by: {btc['blocked_by']}")
        if eth.get("blocked_by"):
            logger.info(f"   ETH blocked by: {eth['blocked_by']}")
    
    if flags:
        logger.info(f"Flags: {len(flags)}")
        for f in flags:
            logger.warning(f"  FLAG: {f}")
    
    logger.info("Done.")


if __name__ == "__main__":
    main()
