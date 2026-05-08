"""
LP Configuration - Wallets, RPC, Contracts
Version: 1.0.0

Поддерживаемые сети: Arbitrum, BSC
Протокол: Uniswap V3 (и форки)
"""

import os

# ═══════════════════════════════════════════════════════════════════════════════
# WALLETS
# ═══════════════════════════════════════════════════════════════════════════════

WALLETS = {
    "0x17e6d71d30d260e30bb7721c63539694ab02b036": "MMA_1",
    "0x91dad140af2800b2d660e530b9f42500eee474a0": "MMA_2",
    "0x3c2c34b9bb0b00145142ffee68475e1ac01c92ba": "MMA_3",
    "0x4e7240952c21c811d9e1237a328b927685a21418": "MMA_4",
    "0x5a51f62d86f5ccb8c7470cea2ac982762049c53c": "MMA_5",
}

WALLET_ADDRESSES = list(WALLETS.keys())

# ═══════════════════════════════════════════════════════════════════════════════
# CHAINS CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

CHAINS = {
    "arbitrum": {
        "name": "Arbitrum One",
        "chain_id": 42161,
        "rpc": os.getenv("ARB_RPC", "https://arbitrum.llamarpc.com"),
        "rpc_fallbacks": [
            "https://arbitrum.drpc.org",
            "https://arb1.arbitrum.io/rpc",
            "https://arbitrum-one.public.blastapi.io",
            "https://rpc.ankr.com/arbitrum",
            "https://arbitrum-one-rpc.publicnode.com",
            "https://1rpc.io/arb",
        ],
        "position_manager": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",  # Uniswap V3
        "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "quoter": "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
        "platform": "arbitrum-one",  # CoinGecko platform ID
        "explorer": "https://arbiscan.io",
        "native_token": "ETH",
    },
    "bsc": {
        "name": "BNB Smart Chain",
        "chain_id": 56,
        "rpc": os.getenv("BSC_RPC", "https://bsc.llamarpc.com"),
        "rpc_fallbacks": [
            "https://bsc.drpc.org",
            "https://binance.llamarpc.com",
            "https://rpc.ankr.com/bsc",
            "https://bsc-rpc.publicnode.com",
            "https://1rpc.io/bnb",
            # Binance public — последним резервом, они жёстко лимитят:
            "https://bsc-dataseed1.binance.org",
        ],
        # Uniswap V3 официальные контракты на BSC
        "position_manager": "0x7b8A01B39D58278b5DE7e48c8449c9f4F5170613",
        "factory": "0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7",
        "quoter": "0x78D78E420Da98ad378D7799bE8f4AF69033EB077",
        "platform": "binance-smart-chain",  # CoinGecko platform ID
        "explorer": "https://bscscan.com",
        "native_token": "BNB",
    },
}

# ═══════════════════════════════════════════════════════════════════════════════
# TOKEN WHITELIST (Stablecoins and major tokens for price reference)
# ═══════════════════════════════════════════════════════════════════════════════

STABLECOINS = {
    # Arbitrum
    "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": ("USDC.e", 6),
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831": ("USDC", 6),
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": ("USDT", 6),
    "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": ("DAI", 18),
    # BSC
    "0x55d398326f99059ff775485246999027b3197955": ("USDT", 18),
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": ("USDC", 18),
    "0xe9e7cea3dedca5984780bafc599bd69add087d56": ("BUSD", 18),
    "0x1af3f329e8be154074d8769d1ffa4ee058b1dbc3": ("DAI", 18),
}

# Native wrapped tokens
WRAPPED_NATIVE = {
    "arbitrum": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",  # WETH
    "bsc": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
}

# ═══════════════════════════════════════════════════════════════════════════════
# ABIs
# ═══════════════════════════════════════════════════════════════════════════════

POSITION_MANAGER_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "balance", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "uint256", "name": "index", "type": "uint256"}
        ],
        "name": "tokenOfOwnerByIndex",
        "outputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "positions",
        "outputs": [
            {"internalType": "uint96", "name": "nonce", "type": "uint96"},
            {"internalType": "address", "name": "operator", "type": "address"},
            {"internalType": "address", "name": "token0", "type": "address"},
            {"internalType": "address", "name": "token1", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
            {"internalType": "int24", "name": "tickLower", "type": "int24"},
            {"internalType": "int24", "name": "tickUpper", "type": "int24"},
            {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
            {"internalType": "uint256", "name": "feeGrowthInside0LastX128", "type": "uint256"},
            {"internalType": "uint256", "name": "feeGrowthInside1LastX128", "type": "uint256"},
            {"internalType": "uint128", "name": "tokensOwed0", "type": "uint128"},
            {"internalType": "uint128", "name": "tokensOwed1", "type": "uint128"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"}
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]

POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "feeGrowthGlobal0X128",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "feeGrowthGlobal1X128",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "int24", "name": "tick", "type": "int24"}],
        "name": "ticks",
        "outputs": [
            {"internalType": "uint128", "name": "liquidityGross", "type": "uint128"},
            {"internalType": "int128", "name": "liquidityNet", "type": "int128"},
            {"internalType": "uint256", "name": "feeGrowthOutside0X128", "type": "uint256"},
            {"internalType": "uint256", "name": "feeGrowthOutside1X128", "type": "uint256"},
            {"internalType": "int56", "name": "tickCumulativeOutside", "type": "int56"},
            {"internalType": "uint160", "name": "secondsPerLiquidityOutsideX128", "type": "uint160"},
            {"internalType": "uint32", "name": "secondsOutside", "type": "uint32"},
            {"internalType": "bool", "name": "initialized", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "fee",
        "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function"
    }
]

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    }
]

# ═══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

# Minimum position value to track (USD)
MIN_POSITION_VALUE_USD = 10.0

# Price cache TTL (seconds)
PRICE_CACHE_TTL = 300  # 5 minutes

# State file path
LP_STATE_FILE = "state/lp_positions.json"
LP_OPPORTUNITIES_FILE = "state/lp_opportunities.json"

# History retention (days)
HISTORY_RETENTION_DAYS = 30

# ═══════════════════════════════════════════════════════════════════════════════
# OPPORTUNITIES SCANNER SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

# DeFiLlama API
DEFILLAMA_POOLS_URL = "https://yields.llama.fi/pools"

# Chains to scan
SCAN_CHAINS = ["Arbitrum", "BSC"]

# Protocols to include (только Uniswap V3)
SCAN_PROTOCOLS = ["uniswap-v3"]

# Minimum thresholds
MIN_TVL_USD = 100_000          # $100K minimum TVL
MIN_VOLUME_24H_USD = 50_000    # $50K minimum daily volume
MIN_APY = 1.0                   # 1% minimum APY

# Token categories
STABLECOINS = {
    "USDC", "USDT", "DAI", "BUSD", "FRAX", "TUSD", "USDP", "GUSD",
    "USDC.E", "USDT.E", "USDC.e", "USDT.e",
    "USDC-CIRCLE", "USDCE", "USDT-TETHER", "FDUSD",
}

MAJOR_TOKENS = {
    # Native / Wrapped
    "WETH", "ETH", "WBTC", "BTC", "BTCB",
    "WBNB", "BNB",
    # L2 / Layer 1
    "ARB", "OP", "MATIC", "BASE", "SOL", "AVAX",
    # Blue chips DeFi
    "LINK", "UNI", "AAVE", "MKR", "SNX", "CRV", "LDO",
    "GMX", "PENDLE", "RDNT", "GNS", "CAKE",
    # LST/LRT
    "WSTETH", "STETH", "RETH", "CBETH", "FRXETH", "SFRXETH",
    "WEETH", "EZETH", "RSETH",
    # Privacy / Store of value
    "ZEC", "XMR",
    # Gaming / Metaverse (quality only)
    "IMX", "SAND", "MANA",
    # Infrastructure
    "GRT", "FIL", "AR",
    # DeFi BSC
    "XVS", "ALPACA", "BIFI",
    # New quality tokens
    "ZRO", "ASTER", "JUP", "PYTH", "W", "ENA",
    "EIGEN", "ETHFI",
}

# Токены исключённые из Top pools рекомендаций (но позиции в них мониторим)
EXCLUDED_FROM_TOP_POOLS = {"ARB"}

# Combined whitelist (stables + majors)
TOKEN_WHITELIST = STABLECOINS | MAJOR_TOKENS

# ═══════════════════════════════════════════════════════════════════════════════
# IL RISK MATRIX
# ═══════════════════════════════════════════════════════════════════════════════

# IL Risk scores based on token pair type
IL_RISK_MATRIX = {
    ("stable", "stable"): 0.00,   # USDC-USDT
    ("stable", "major"):  0.30,   # USDC-WETH
    ("major", "stable"):  0.30,   # WETH-USDC
    ("major", "major"):   0.50,   # WETH-WBTC
    ("stable", "alt"):    0.70,   # USDC-ALT
    ("alt", "stable"):    0.70,   # ALT-USDC
    ("major", "alt"):     0.80,   # WETH-ALT
    ("alt", "major"):     0.80,   # ALT-WETH
    ("alt", "alt"):       0.95,   # ALT-ALT
}

# ═══════════════════════════════════════════════════════════════════════════════
# REGIME PENALTIES
# ═══════════════════════════════════════════════════════════════════════════════

# How much to penalize IL risk based on market regime
REGIME_IL_PENALTY = {
    # LP-friendly regimes (low penalty)
    "HARVEST":       0.15,
    "RANGE":         0.20,
    "MEAN_REVERT":   0.25,
    
    # Neutral
    "VOLATILE_CHOP": 0.35,
    "TRANSITION":    0.35,
    "UNKNOWN":       0.40,
    
    # Risky regimes (high penalty)
    "BREAKOUT":      0.50,
    "GAP_RISK":      0.55,
    "TRENDING":      0.60,
    "BEAR":          0.55,  # Bearish trend = IL risk
    "BULL":          0.45,  # Bullish trend = moderate IL risk
    "CHURN":         0.70,
    "AVOID":         0.90,
}

