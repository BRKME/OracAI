"""
LP Position Monitor - Track Uniswap V3 LP Positions
Version: 1.0.0

Функционал:
- Сканирование позиций по 5 кошелькам
- Расчёт баланса, fees, IL
- Сети: Arbitrum, BSC
- JSON state + Telegram отчёт
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from decimal import Decimal

import requests
from web3 import Web3

from lp_config import (
    WALLETS, WALLET_ADDRESSES, CHAINS,
    POSITION_MANAGER_ABI, FACTORY_ABI, POOL_ABI, ERC20_ABI,
    STABLECOINS, WRAPPED_NATIVE,
    MIN_POSITION_VALUE_USD, PRICE_CACHE_TTL, LP_STATE_FILE
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Position:
    """Single LP Position"""
    wallet: str
    wallet_name: str
    chain: str
    token_id: int
    pool_address: str
    token0_symbol: str
    token1_symbol: str
    token0_address: str
    token1_address: str
    fee_tier: float  # e.g., 0.05 for 0.05%
    liquidity: int
    tick_lower: int
    tick_upper: int
    current_tick: int
    in_range: bool
    amount0: float
    amount1: float
    amount0_usd: float
    amount1_usd: float
    balance_usd: float
    uncollected_fees0: float
    uncollected_fees1: float
    uncollected_fees_usd: float
    price0_usd: float
    price1_usd: float
    # Computed metrics
    range_width_pct: float  # How wide is the range relative to current price
    distance_to_lower_pct: float  # Distance from current price to lower bound
    distance_to_upper_pct: float  # Distance from current price to upper bound


@dataclass
class PositionsSummary:
    """Summary of all positions"""
    timestamp: str
    total_positions: int
    positions_in_range: int
    positions_out_of_range: int
    total_balance_usd: float
    total_uncollected_fees_usd: float
    by_chain: Dict[str, Dict[str, float]]
    by_wallet: Dict[str, Dict[str, float]]


# ═══════════════════════════════════════════════════════════════════════════════
# UNISWAP V3 MATH
# ═══════════════════════════════════════════════════════════════════════════════

def get_sqrt_ratio_at_tick(tick: int) -> int:
    """Calculate sqrtPriceX96 from tick"""
    MAX_TICK = 887272
    abs_tick = abs(tick)
    if abs_tick > MAX_TICK:
        raise ValueError(f"Tick {tick} out of range")
    
    ratio = 0xfffcb933bd6fad37aa2d162d1a594001 if (abs_tick & 0x1) != 0 else 0x100000000000000000000000000000000
    
    if (abs_tick & 0x2) != 0:
        ratio = (ratio * 0xfff97272373d413259a46990580e213a) >> 128
    if (abs_tick & 0x4) != 0:
        ratio = (ratio * 0xfff2e50f5f656932ef12357cf3c7fdcc) >> 128
    if (abs_tick & 0x8) != 0:
        ratio = (ratio * 0xffe5caca7e10e4e61c3624eaa0941cd0) >> 128
    if (abs_tick & 0x10) != 0:
        ratio = (ratio * 0xffcb9843d60f6159c9db58835c926644) >> 128
    if (abs_tick & 0x20) != 0:
        ratio = (ratio * 0xff973b41fa98c081472e6896dfb254c0) >> 128
    if (abs_tick & 0x40) != 0:
        ratio = (ratio * 0xff2ea16466c96a3843ec78b326b52861) >> 128
    if (abs_tick & 0x80) != 0:
        ratio = (ratio * 0xfe5dee046a99a2a811c461f1969c3053) >> 128
    if (abs_tick & 0x100) != 0:
        ratio = (ratio * 0xfcbe86c7900a88aedcffc83b479aa3a4) >> 128
    if (abs_tick & 0x200) != 0:
        ratio = (ratio * 0xf987a7253ac413176f2b074cf7815e54) >> 128
    if (abs_tick & 0x400) != 0:
        ratio = (ratio * 0xf3392b0822b70005940c7a398e4b70f3) >> 128
    if (abs_tick & 0x800) != 0:
        ratio = (ratio * 0xe7159475a2c29b7443b29c7fa6e889d9) >> 128
    if (abs_tick & 0x1000) != 0:
        ratio = (ratio * 0xd097f3bdfd2022b8845ad8f792aa5825) >> 128
    if (abs_tick & 0x2000) != 0:
        ratio = (ratio * 0xa9f746462d870fdf8a65dc1f90e061e5) >> 128
    if (abs_tick & 0x4000) != 0:
        ratio = (ratio * 0x70d869a156d2a1b890bb3df62baf32f7) >> 128
    if (abs_tick & 0x8000) != 0:
        ratio = (ratio * 0x31be135f97d08fd981231505542fcfa6) >> 128
    if (abs_tick & 0x10000) != 0:
        ratio = (ratio * 0x9aa508b5b7a84e1c677de54f3e99bc9) >> 128
    if (abs_tick & 0x20000) != 0:
        ratio = (ratio * 0x5d6af8dedb81196699c329225ee604) >> 128
    if (abs_tick & 0x40000) != 0:
        ratio = (ratio * 0x2216e584f5fa1ea926041bedfe98) >> 128
    if (abs_tick & 0x80000) != 0:
        ratio = (ratio * 0x48a170391f7dc42444e8fa2) >> 128

    if tick > 0:
        ratio = ((1 << 256) - 1) // ratio

    return ratio >> 32


def get_amounts_for_liquidity(
    sqrt_price_x96: int,
    sqrt_ratio_a: int,
    sqrt_ratio_b: int,
    liquidity: int
) -> Tuple[int, int]:
    """Calculate token amounts from liquidity"""
    if sqrt_ratio_a > sqrt_ratio_b:
        sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a
    
    if sqrt_price_x96 <= sqrt_ratio_a:
        # Current price below range - all token0
        amount0 = get_amount0_for_liquidity(sqrt_ratio_a, sqrt_ratio_b, liquidity)
        amount1 = 0
    elif sqrt_price_x96 < sqrt_ratio_b:
        # Current price in range
        amount0 = get_amount0_for_liquidity(sqrt_price_x96, sqrt_ratio_b, liquidity)
        amount1 = get_amount1_for_liquidity(sqrt_ratio_a, sqrt_price_x96, liquidity)
    else:
        # Current price above range - all token1
        amount0 = 0
        amount1 = get_amount1_for_liquidity(sqrt_ratio_a, sqrt_ratio_b, liquidity)
    
    return amount0, amount1


def get_amount0_for_liquidity(sqrt_ratio_a: int, sqrt_ratio_b: int, liquidity: int) -> int:
    if sqrt_ratio_a > sqrt_ratio_b:
        sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a
    return (((liquidity << 96) * (sqrt_ratio_b - sqrt_ratio_a)) // sqrt_ratio_b) // sqrt_ratio_a


def get_amount1_for_liquidity(sqrt_ratio_a: int, sqrt_ratio_b: int, liquidity: int) -> int:
    if sqrt_ratio_a > sqrt_ratio_b:
        sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a
    return liquidity * (sqrt_ratio_b - sqrt_ratio_a) // (1 << 96)


def tick_to_price(tick: int, decimals0: int, decimals1: int) -> float:
    """Convert tick to human-readable price"""
    return (1.0001 ** tick) * (10 ** (decimals0 - decimals1))


def get_fee_growth_inside(
    pool_contract,
    tick_lower: int,
    tick_upper: int,
    current_tick: int,
    fee_growth_global0: int,
    fee_growth_global1: int
) -> Tuple[int, int]:
    """Calculate fee growth inside the position's range"""
    Q128 = 2 ** 128
    
    try:
        tick_lower_data = pool_contract.functions.ticks(tick_lower).call()
        tick_upper_data = pool_contract.functions.ticks(tick_upper).call()
    except Exception as e:
        logger.warning(f"Error getting tick data: {e}")
        return 0, 0
    
    fee_growth_outside0_lower = tick_lower_data[2]
    fee_growth_outside1_lower = tick_lower_data[3]
    fee_growth_outside0_upper = tick_upper_data[2]
    fee_growth_outside1_upper = tick_upper_data[3]
    
    # Calculate fee growth below
    if current_tick >= tick_lower:
        fee_growth_below0 = fee_growth_outside0_lower
        fee_growth_below1 = fee_growth_outside1_lower
    else:
        fee_growth_below0 = (fee_growth_global0 - fee_growth_outside0_lower) % Q128
        fee_growth_below1 = (fee_growth_global1 - fee_growth_outside1_lower) % Q128
    
    # Calculate fee growth above
    if current_tick < tick_upper:
        fee_growth_above0 = fee_growth_outside0_upper
        fee_growth_above1 = fee_growth_outside1_upper
    else:
        fee_growth_above0 = (fee_growth_global0 - fee_growth_outside0_upper) % Q128
        fee_growth_above1 = (fee_growth_global1 - fee_growth_outside1_upper) % Q128
    
    # Fee growth inside
    fee_growth_inside0 = (fee_growth_global0 - fee_growth_below0 - fee_growth_above0) % Q128
    fee_growth_inside1 = (fee_growth_global1 - fee_growth_below1 - fee_growth_above1) % Q128
    
    return fee_growth_inside0, fee_growth_inside1


def calculate_uncollected_fees(
    liquidity: int,
    fee_growth_inside0: int,
    fee_growth_inside1: int,
    fee_growth_inside0_last: int,
    fee_growth_inside1_last: int,
    tokens_owed0: int,
    tokens_owed1: int,
    decimals0: int,
    decimals1: int
) -> Tuple[float, float]:
    """Calculate uncollected fees for a position"""
    Q128 = 2 ** 128
    
    # Accrued fees since last update
    accrued0 = (liquidity * ((fee_growth_inside0 - fee_growth_inside0_last) % Q128)) // Q128
    accrued1 = (liquidity * ((fee_growth_inside1 - fee_growth_inside1_last) % Q128)) // Q128
    
    # Total uncollected = owed + accrued
    total0 = (tokens_owed0 + accrued0) / (10 ** decimals0)
    total1 = (tokens_owed1 + accrued1) / (10 ** decimals1)
    
    return total0, total1


# ═══════════════════════════════════════════════════════════════════════════════
# PRICE SERVICE
# ═══════════════════════════════════════════════════════════════════════════════

class PriceService:
    """Token price fetcher with caching"""
    
    def __init__(self, cache_ttl: int = PRICE_CACHE_TTL):
        self.cache: Dict[str, Tuple[float, float]] = {}  # address -> (price, timestamp)
        self.cache_ttl = cache_ttl
    
    def get_price(self, platform: str, token_address: str) -> float:
        """Get token price in USD"""
        cache_key = f"{platform}:{token_address.lower()}"
        
        # Check cache
        if cache_key in self.cache:
            price, ts = self.cache[cache_key]
            if time.time() - ts < self.cache_ttl:
                return price
        
        # Check if stablecoin
        if token_address.lower() in STABLECOINS:
            return 1.0
        
        # Fetch from CoinGecko
        try:
            url = f"https://api.coingecko.com/api/v3/simple/token_price/{platform}"
            params = {
                "contract_addresses": token_address.lower(),
                "vs_currencies": "usd"
            }
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                price = data.get(token_address.lower(), {}).get("usd", 0)
                self.cache[cache_key] = (price, time.time())
                return price
            else:
                logger.warning(f"CoinGecko error {response.status_code} for {token_address}")
                return 0
                
        except Exception as e:
            logger.warning(f"Price fetch error for {token_address}: {e}")
            return 0
    
    def get_prices_batch(self, platform: str, addresses: List[str]) -> Dict[str, float]:
        """Get prices for multiple tokens"""
        # Filter out cached and stablecoins
        to_fetch = []
        results = {}
        
        for addr in addresses:
            addr_lower = addr.lower()
            cache_key = f"{platform}:{addr_lower}"
            
            if addr_lower in STABLECOINS:
                results[addr_lower] = 1.0
            elif cache_key in self.cache:
                price, ts = self.cache[cache_key]
                if time.time() - ts < self.cache_ttl:
                    results[addr_lower] = price
                else:
                    to_fetch.append(addr_lower)
            else:
                to_fetch.append(addr_lower)
        
        if not to_fetch:
            return results
        
        # Batch fetch
        try:
            url = f"https://api.coingecko.com/api/v3/simple/token_price/{platform}"
            params = {
                "contract_addresses": ",".join(to_fetch),
                "vs_currencies": "usd"
            }
            response = requests.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                for addr in to_fetch:
                    price = data.get(addr, {}).get("usd", 0)
                    results[addr] = price
                    self.cache[f"{platform}:{addr}"] = (price, time.time())
            else:
                logger.warning(f"Batch price fetch error: {response.status_code}")
                for addr in to_fetch:
                    results[addr] = 0
                    
        except Exception as e:
            logger.warning(f"Batch price fetch exception: {e}")
            for addr in to_fetch:
                results[addr] = 0
        
        return results


# ═══════════════════════════════════════════════════════════════════════════════
# LP MONITOR
# ═══════════════════════════════════════════════════════════════════════════════

class LPMonitor:
    """Main LP Position Monitor"""
    
    def __init__(self):
        self.price_service = PriceService()
        self.web3_clients: Dict[str, Web3] = {}
        self.positions: List[Position] = []
        
        # Initialize Web3 clients with fallback RPCs
        for chain_name, config in CHAINS.items():
            # Try main RPC first, then fallbacks
            rpcs_to_try = [config["rpc"]] + config.get("rpc_fallbacks", [])
            
            for rpc_url in rpcs_to_try:
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 10}))
                    if w3.is_connected():
                        self.web3_clients[chain_name] = w3
                        logger.info(f"✓ Connected to {chain_name} via {rpc_url[:40]}...")
                        break
                except Exception as e:
                    logger.debug(f"  Failed {rpc_url}: {e}")
                    continue
            
            if chain_name not in self.web3_clients:
                logger.warning(f"✗ Failed to connect to {chain_name} (tried {len(rpcs_to_try)} RPCs)")
    
    def scan_all_positions(self) -> List[Position]:
        """Scan all wallets on all chains"""
        self.positions = []
        self.failed_wallets = []
        
        for chain_name, w3 in self.web3_clients.items():
            config = CHAINS[chain_name]
            logger.info(f"\n{'='*50}")
            logger.info(f"Scanning {chain_name.upper()}")
            logger.info(f"{'='*50}")
            
            chain_positions, chain_failed = self._scan_chain(w3, chain_name, config)
            self.positions.extend(chain_positions)
            self.failed_wallets.extend(chain_failed)
        
        if self.failed_wallets:
            logger.warning(f"⚠️ Failed wallets: {self.failed_wallets}")
        
        return self.positions
    
    def _scan_chain(self, w3: Web3, chain_name: str, config: dict) -> List[Position]:
        """Scan all wallets on a specific chain"""
        positions = []
        failed_wallets = []
        
        pm_address = w3.to_checksum_address(config["position_manager"])
        factory_address = w3.to_checksum_address(config["factory"])
        
        pm_contract = w3.eth.contract(address=pm_address, abi=POSITION_MANAGER_ABI)
        factory_contract = w3.eth.contract(address=factory_address, abi=FACTORY_ABI)
        
        for wallet in WALLET_ADDRESSES:
            wallet_name = WALLETS.get(wallet.lower(), "Unknown")
            wallet_loaded = False
            
            for attempt in range(3):  # 3 attempts per wallet
                try:
                    wallet_checksum = w3.to_checksum_address(wallet)
                    num_positions = pm_contract.functions.balanceOf(wallet_checksum).call()
                    
                    if num_positions == 0:
                        wallet_loaded = True
                        break
                    
                    logger.info(f"\n{wallet_name}: {num_positions} positions")
                    
                    for i in range(num_positions):
                        try:
                            position = self._get_position(
                                w3, chain_name, config,
                                pm_contract, factory_contract,
                                wallet_checksum, wallet_name, i
                            )
                            if position:
                                positions.append(position)
                                status = "🟢" if position.in_range else "🔴"
                                logger.info(f"  {status} {position.token0_symbol}-{position.token1_symbol}: ${position.balance_usd:.0f} (fees: ${position.uncollected_fees_usd:.2f})")
                        except Exception as e:
                            logger.warning(f"  Error getting position {i}: {e}")
                    
                    wallet_loaded = True
                    break
                        
                except Exception as e:
                    logger.warning(f"Error scanning {wallet_name} on {chain_name} (attempt {attempt+1}/3): {e}")
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))  # 2s, 4s backoff
            
            if not wallet_loaded:
                logger.error(f"⚠️ FAILED to load {wallet_name} on {chain_name} after 3 attempts")
                failed_wallets.append(f"{wallet_name}/{chain_name}")
        
        return positions, failed_wallets
    
    def _get_position(
        self,
        w3: Web3,
        chain_name: str,
        config: dict,
        pm_contract,
        factory_contract,
        wallet: str,
        wallet_name: str,
        index: int
    ) -> Optional[Position]:
        """Get details for a single position"""
        
        # Get token ID
        token_id = pm_contract.functions.tokenOfOwnerByIndex(wallet, index).call()
        
        # Get position data
        pos_data = pm_contract.functions.positions(token_id).call()
        liquidity = pos_data[7]
        
        # Skip empty positions
        if liquidity == 0:
            return None
        
        token0 = pos_data[2]
        token1 = pos_data[3]
        fee = pos_data[4]
        tick_lower = pos_data[5]
        tick_upper = pos_data[6]
        fee_growth_inside0_last = pos_data[8]
        fee_growth_inside1_last = pos_data[9]
        tokens_owed0 = pos_data[10]
        tokens_owed1 = pos_data[11]
        
        # Get pool
        token0_checksum = w3.to_checksum_address(token0)
        token1_checksum = w3.to_checksum_address(token1)
        
        pool_address = factory_contract.functions.getPool(
            token0_checksum, token1_checksum, fee
        ).call()
        
        if pool_address == "0x0000000000000000000000000000000000000000":
            return None
        
        pool_checksum = w3.to_checksum_address(pool_address)
        pool_contract = w3.eth.contract(address=pool_checksum, abi=POOL_ABI)
        
        # Get current pool state
        slot0 = pool_contract.functions.slot0().call()
        sqrt_price_x96 = slot0[0]
        current_tick = slot0[1]
        
        # Get token info
        token0_contract = w3.eth.contract(address=token0_checksum, abi=ERC20_ABI)
        token1_contract = w3.eth.contract(address=token1_checksum, abi=ERC20_ABI)
        
        decimals0 = token0_contract.functions.decimals().call()
        decimals1 = token1_contract.functions.decimals().call()
        symbol0 = token0_contract.functions.symbol().call()
        symbol1 = token1_contract.functions.symbol().call()
        
        # Calculate amounts
        sqrt_lower = get_sqrt_ratio_at_tick(tick_lower)
        sqrt_upper = get_sqrt_ratio_at_tick(tick_upper)
        
        amount0_raw, amount1_raw = get_amounts_for_liquidity(
            sqrt_price_x96, sqrt_lower, sqrt_upper, liquidity
        )
        
        amount0 = amount0_raw / (10 ** decimals0)
        amount1 = amount1_raw / (10 ** decimals1)
        
        # Calculate uncollected fees
        fee_growth_global0 = pool_contract.functions.feeGrowthGlobal0X128().call()
        fee_growth_global1 = pool_contract.functions.feeGrowthGlobal1X128().call()
        
        fee_growth_inside0, fee_growth_inside1 = get_fee_growth_inside(
            pool_contract, tick_lower, tick_upper, current_tick,
            fee_growth_global0, fee_growth_global1
        )
        
        uncollected0, uncollected1 = calculate_uncollected_fees(
            liquidity,
            fee_growth_inside0, fee_growth_inside1,
            fee_growth_inside0_last, fee_growth_inside1_last,
            tokens_owed0, tokens_owed1,
            decimals0, decimals1
        )
        
        # Get prices
        price0 = self.price_service.get_price(config["platform"], token0)
        price1 = self.price_service.get_price(config["platform"], token1)
        
        # Calculate USD values
        amount0_usd = amount0 * price0
        amount1_usd = amount1 * price1
        balance_usd = amount0_usd + amount1_usd
        uncollected_fees_usd = uncollected0 * price0 + uncollected1 * price1
        
        # Skip tiny positions
        if balance_usd < MIN_POSITION_VALUE_USD and uncollected_fees_usd < 1:
            return None
        
        # Calculate range metrics
        in_range = tick_lower <= current_tick < tick_upper
        
        price_lower = tick_to_price(tick_lower, decimals0, decimals1)
        price_upper = tick_to_price(tick_upper, decimals0, decimals1)
        price_current = tick_to_price(current_tick, decimals0, decimals1)
        
        range_width_pct = ((price_upper - price_lower) / price_current) * 100 if price_current > 0 else 0
        distance_to_lower_pct = ((price_current - price_lower) / price_current) * 100 if price_current > 0 else 0
        distance_to_upper_pct = ((price_upper - price_current) / price_current) * 100 if price_current > 0 else 0
        
        return Position(
            wallet=wallet,
            wallet_name=wallet_name,
            chain=chain_name,
            token_id=token_id,
            pool_address=pool_address,
            token0_symbol=symbol0,
            token1_symbol=symbol1,
            token0_address=token0,
            token1_address=token1,
            fee_tier=fee / 10000,  # Convert to percentage
            liquidity=liquidity,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            current_tick=current_tick,
            in_range=in_range,
            amount0=amount0,
            amount1=amount1,
            amount0_usd=amount0_usd,
            amount1_usd=amount1_usd,
            balance_usd=balance_usd,
            uncollected_fees0=uncollected0,
            uncollected_fees1=uncollected1,
            uncollected_fees_usd=uncollected_fees_usd,
            price0_usd=price0,
            price1_usd=price1,
            range_width_pct=range_width_pct,
            distance_to_lower_pct=distance_to_lower_pct,
            distance_to_upper_pct=distance_to_upper_pct,
        )
    
    def get_summary(self) -> PositionsSummary:
        """Get summary of all positions"""
        if not self.positions:
            return PositionsSummary(
                timestamp=datetime.now(timezone.utc).isoformat(),
                total_positions=0,
                positions_in_range=0,
                positions_out_of_range=0,
                total_balance_usd=0,
                total_uncollected_fees_usd=0,
                by_chain={},
                by_wallet={},
            )
        
        total_balance = sum(p.balance_usd for p in self.positions)
        total_fees = sum(p.uncollected_fees_usd for p in self.positions)
        in_range = sum(1 for p in self.positions if p.in_range)
        
        # By chain
        by_chain: Dict[str, Dict[str, float]] = {}
        for p in self.positions:
            if p.chain not in by_chain:
                by_chain[p.chain] = {"balance_usd": 0, "fees_usd": 0, "count": 0}
            by_chain[p.chain]["balance_usd"] += p.balance_usd
            by_chain[p.chain]["fees_usd"] += p.uncollected_fees_usd
            by_chain[p.chain]["count"] += 1
        
        # By wallet
        by_wallet: Dict[str, Dict[str, float]] = {}
        for p in self.positions:
            if p.wallet_name not in by_wallet:
                by_wallet[p.wallet_name] = {"balance_usd": 0, "fees_usd": 0, "count": 0}
            by_wallet[p.wallet_name]["balance_usd"] += p.balance_usd
            by_wallet[p.wallet_name]["fees_usd"] += p.uncollected_fees_usd
            by_wallet[p.wallet_name]["count"] += 1
        
        return PositionsSummary(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_positions=len(self.positions),
            positions_in_range=in_range,
            positions_out_of_range=len(self.positions) - in_range,
            total_balance_usd=total_balance,
            total_uncollected_fees_usd=total_fees,
            by_chain=by_chain,
            by_wallet=by_wallet,
        )
    
    def save_state(self, filepath: str = LP_STATE_FILE):
        """Save current state to JSON"""
        summary = self.get_summary()
        
        state = {
            "timestamp": summary.timestamp,
            "summary": asdict(summary),
            "positions": [asdict(p) for p in self.positions]
        }
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        
        logger.info(f"✓ State saved to {filepath}")
        return state
    
    def format_telegram_report(self) -> str:
        """Format positions for Telegram"""
        summary = self.get_summary()
        
        # Header
        now = datetime.now(timezone.utc)
        days_ru = {
            'Monday': 'Пн', 'Tuesday': 'Вт', 'Wednesday': 'Ср',
            'Thursday': 'Чт', 'Friday': 'Пт', 'Saturday': 'Сб', 'Sunday': 'Вс'
        }
        day_name = days_ru.get(now.strftime('%A'), '')
        
        lines = [
            "#LP #Uniswap",
            f"📊 LP Monitor | {day_name} {now.strftime('%d.%m %H:%M')} UTC",
            ""
        ]
        
        # Summary
        in_range_pct = (summary.positions_in_range / summary.total_positions * 100) if summary.total_positions > 0 else 0
        lines.append(f"TVL: ${summary.total_balance_usd:,.0f}")
        lines.append(f"Fees: ${summary.total_uncollected_fees_usd:,.2f}")
        lines.append(f"In Range: {summary.positions_in_range}/{summary.total_positions} ({in_range_pct:.0f}%)")
        lines.append("")
        
        # Positions grouped by wallet - details only on problems
        from collections import defaultdict
        by_wallet = defaultdict(list)
        for p in self.positions:
            by_wallet[p.wallet_name].append(p)
        
        all_in_range = all(p.in_range for p in self.positions)
        
        if all_in_range:
            # Compact: one line with wallet totals
            wallet_parts = []
            for wallet_name in sorted(by_wallet.keys()):
                w_total = sum(p.balance_usd for p in by_wallet[wallet_name])
                wallet_parts.append(f"{wallet_name}: ${w_total:,.0f}")
            lines.append(" | ".join(wallet_parts))
            lines.append("")
        else:
            for wallet_name in sorted(by_wallet.keys()):
                wallet_positions = sorted(by_wallet[wallet_name], key=lambda x: x.balance_usd, reverse=True)
                has_problems = any(not p.in_range for p in wallet_positions)
                
                if not has_problems:
                    wallet_total = sum(p.balance_usd for p in wallet_positions)
                    lines.append(f"{wallet_name}: ${wallet_total:,.0f} ✓")
                else:
                    wallet_total = sum(p.balance_usd for p in wallet_positions)
                    wallet_fees = sum(p.uncollected_fees_usd for p in wallet_positions)
                    lines.append(f"{wallet_name}: ${wallet_total:,.0f} (fees: ${wallet_fees:.2f})")
                    
                    for p in wallet_positions:
                        status = "🟢" if p.in_range else "🔴"
                        lines.append(f"  {status} {p.token0_symbol}-{p.token1_symbol} ${p.balance_usd:,.0f}")
                        if not p.in_range:
                            if p.current_tick < p.tick_lower:
                                lines.append(f"    Below range {abs(p.distance_to_lower_pct):.1f}%")
                            else:
                                lines.append(f"    Above range {abs(p.distance_to_upper_pct):.1f}%")
                
                lines.append("")
        
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram_message(message: str) -> bool:
    """Send message to Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not set")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=payload, timeout=10)
        
        if response.status_code == 200:
            logger.info("✓ Telegram message sent")
            return True
        else:
            logger.error(f"Telegram error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram exception: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """Main entry point"""
    logger.info("=" * 60)
    logger.info("LP POSITION MONITOR v1.0.0")
    logger.info("=" * 60)
    
    monitor = LPMonitor()
    
    # Check if any chains connected
    if not monitor.web3_clients:
        logger.error("No chains connected! Check RPC endpoints.")
        return None
    
    # Scan all positions
    positions = monitor.scan_all_positions()
    
    if not positions:
        logger.warning("No positions found!")
        return None
    
    # Get summary
    summary = monitor.get_summary()
    
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total Positions: {summary.total_positions}")
    logger.info(f"In Range: {summary.positions_in_range}")
    logger.info(f"Out of Range: {summary.positions_out_of_range}")
    logger.info(f"Total Balance: ${summary.total_balance_usd:,.2f}")
    logger.info(f"Total Fees: ${summary.total_uncollected_fees_usd:,.2f}")
    
    # Save state
    state = monitor.save_state()
    
    # Format Telegram report
    report = monitor.format_telegram_report()
    logger.info("\n" + "=" * 60)
    logger.info("TELEGRAM REPORT")
    logger.info("=" * 60)
    print(report)
    
    # Send to Telegram
    send_telegram_message(report)
    
    return state


if __name__ == "__main__":
    main()
