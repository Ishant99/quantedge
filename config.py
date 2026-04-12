# =============================================================================
# config.py — Master configuration for Trading Agent
# Change TRADING_MODE to switch between paper / live without touching any
# other file. All modules import from here.
# =============================================================================

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Helper: read from user_settings.json first, then env var, then default.
# This lets the dashboard configure everything without touching .env or SSH.
# ---------------------------------------------------------------------------
def _S(key: str, env_key: str = None, default=None):
    """Priority: user_settings.json > env var > hardcoded default."""
    try:
        from settings.manager import get as _get
        v = _get(key)
        if v is not None and v != "":
            return v
    except Exception:
        pass
    return os.getenv(env_key or key, default)

# -----------------------------------------------------------------------------
# TRADING MODE — the single flag that controls everything
# "paper" → no real orders, virtual portfolio, yfinance data
# "live"  → real Zerodha orders, Kite WebSocket data
# -----------------------------------------------------------------------------
TRADING_MODE = _S("TRADING_MODE", "TRADING_MODE", "paper")   # paper | live

# -----------------------------------------------------------------------------
# VIRTUAL PORTFOLIO (paper mode)
# -----------------------------------------------------------------------------
VIRTUAL_CAPITAL = int(_S("VIRTUAL_CAPITAL", "VIRTUAL_CAPITAL", 1_000_000))
VIRTUAL_PORTFOLIO_FILE = "logs/virtual_portfolio.json"
PAPER_MAX_ALLOC_NSE_PCT    = float(_S("PAPER_MAX_ALLOC_NSE_PCT", default=0.40))
PAPER_MAX_ALLOC_FNO_PCT    = float(_S("PAPER_MAX_ALLOC_FNO_PCT", default=0.30))
PAPER_MAX_ALLOC_US_PCT     = float(_S("PAPER_MAX_ALLOC_US_PCT", default=0.20))
PAPER_MAX_ALLOC_CRYPTO_PCT = float(_S("PAPER_MAX_ALLOC_CRYPTO_PCT", default=0.10))

# -----------------------------------------------------------------------------
# ZERODHA KITE API (live mode — fill via .env file)
# -----------------------------------------------------------------------------
KITE_API_KEY    = _S("KITE_API_KEY",    "KITE_API_KEY",    "")
KITE_API_SECRET = _S("KITE_API_SECRET", "KITE_API_SECRET", "")
KITE_ACCESS_TOKEN_FILE = "logs/kite_access_token.txt"

# -----------------------------------------------------------------------------
# MARKET SCANNER (M1)
# -----------------------------------------------------------------------------
NSE_TOP_200_FILE  = "data/nse_top200_symbols.csv"   # legacy fallback
NSE_500_FILE      = "data/nse500_symbols.csv"        # primary watchlist (500 stocks)
NSE_WATCHLIST_ADDITIONS_FILE = "data/nse_watchlist_additions.csv"
MARKET_DATA_DIR   = "logs/market_data/"
SCAN_TIME_IST     = "09:00"
EXCHANGE          = "NSE"
SCANNER_BATCH_SIZE  = 50     # stocks per yfinance batch call
SCANNER_WORKERS     = 20     # ThreadPoolExecutor max_workers
SCANNER_RETRY_MAX   = 3      # retries on yfinance failure
SCANNER_RETRY_DELAY = 2.0    # seconds between retries (exponential base)
CACHE_STALE_HOURS   = 24     # hours before cached CSV is considered stale

# -----------------------------------------------------------------------------
# TECHNICAL ANALYSIS (M2)
# -----------------------------------------------------------------------------
RSI_PERIOD       = 14
MACD_FAST        = 12
MACD_SLOW        = 26
MACD_SIGNAL      = 9
BB_PERIOD        = 20
BB_STD           = 2
SMA_SHORT        = 20
SMA_MID          = 50
SMA_LONG         = 200
VOLUME_AVG_DAYS  = 20
MIN_TA_SCORE     = float(_S("MIN_TA_SCORE", "MIN_TA_SCORE", 5.0))
# Thresholds for bullish/bearish signal classification in TechnicalAgent
TA_SIGNAL_BULLISH = float(_S("TA_SIGNAL_BULLISH", "TA_SIGNAL_BULLISH", 6.5))
TA_SIGNAL_BEARISH = float(_S("TA_SIGNAL_BEARISH", "TA_SIGNAL_BEARISH", 4.0))
# New indicators (ADX, Stochastic, OBV)
ADX_PERIOD          = 14
STOCH_K_PERIOD      = 14
STOCH_D_PERIOD      = 3
STOCH_OVERBOUGHT    = 80
STOCH_OVERSOLD      = 20
OBV_TREND_LOOKBACK  = 10
TA_MIN_TREND_ADX    = float(_S("TA_MIN_TREND_ADX", default=18.0))
TA_MAX_BUY_STOCH    = float(_S("TA_MAX_BUY_STOCH", default=88.0))

# -----------------------------------------------------------------------------
# SENTIMENT AGENT (M3)
# -----------------------------------------------------------------------------
RSS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/MCtopnews.xml",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://feeds.feedburner.com/ndtvprofit-latest",
    "https://www.livemint.com/rss/markets",
]
SENTIMENT_MODEL           = "llama3"   # Ollama local model
OLLAMA_BASE_URL           = "http://localhost:11434"
SENTIMENT_FRESHNESS_HOURS = 6          # headlines newer than this = full weight
SENTIMENT_DECAY_FACTOR    = 0.5        # weight halved for each extra 6h window

# -----------------------------------------------------------------------------
# STRATEGY ENGINE (M4)
# -----------------------------------------------------------------------------
TA_WEIGHT        = float(_S("TA_WEIGHT",        "TA_WEIGHT",        0.50))
SENTIMENT_WEIGHT = float(_S("SENTIMENT_WEIGHT", "SENTIMENT_WEIGHT", 0.30))
TREND_WEIGHT     = 0.20
MIN_CONFIDENCE       = float(_S("MIN_CONFIDENCE",       "MIN_CONFIDENCE",       0.60))
SELL_CONFIDENCE      = float(_S("SELL_CONFIDENCE",      default=0.45))     # emit SELL when confidence <= this
THESIS_DROP_SELL_PCT = float(_S("THESIS_DROP_SELL_PCT", default=0.30))     # sell held position if confidence drops 30%+
TOP_N_SIGNALS        = int(  _S("TOP_N_SIGNALS",        "TOP_N_SIGNALS",    10))
STRATEGY_QUALITY_MIN_RESOLVED = int(_S("STRATEGY_QUALITY_MIN_RESOLVED", default=3))
STRATEGY_QUALITY_WEAK_SYMBOL_TP_PCT = float(_S("STRATEGY_QUALITY_WEAK_SYMBOL_TP_PCT", default=35.0))
STRATEGY_QUALITY_STRONG_SYMBOL_TP_PCT = float(_S("STRATEGY_QUALITY_STRONG_SYMBOL_TP_PCT", default=60.0))
STRATEGY_QUALITY_SETUP_WEIGHT = float(_S("STRATEGY_QUALITY_SETUP_WEIGHT", default=0.20))
STRATEGY_QUALITY_SYMBOL_WEIGHT = float(_S("STRATEGY_QUALITY_SYMBOL_WEIGHT", default=0.20))
STRATEGY_QUALITY_CONF_BUCKET_WEIGHT = float(_S("STRATEGY_QUALITY_CONF_BUCKET_WEIGHT", default=0.10))
STRATEGY_QUALITY_REGIME_WEIGHT = float(_S("STRATEGY_QUALITY_REGIME_WEIGHT", default=0.10))
STRATEGY_QUALITY_BLOCK_WEAK_SYMBOLS = bool(_S("STRATEGY_QUALITY_BLOCK_WEAK_SYMBOLS", default=True))
STRATEGY_QUALITY_MAX_PENALTY = float(_S("STRATEGY_QUALITY_MAX_PENALTY", default=0.20))
STRATEGY_QUALITY_MAX_BOOST = float(_S("STRATEGY_QUALITY_MAX_BOOST", default=0.12))

# -----------------------------------------------------------------------------
# RISK MANAGEMENT (M5)
# -----------------------------------------------------------------------------
RISK_PER_TRADE_PCT   = float(_S("RISK_PER_TRADE_PCT",   default=0.02))
MAX_OPEN_POSITIONS   = int(  _S("MAX_OPEN_POSITIONS",   default=5))
REWARD_RISK_RATIO    = float(_S("REWARD_RISK_RATIO",    default=2.0))
MAX_DRAWDOWN_PCT     = 0.10
ATR_SL_MULTIPLIER    = float(_S("ATR_SL_MULTIPLIER",   default=1.5))
MAX_DAILY_LOSS_PCT   = float(_S("MAX_DAILY_LOSS_PCT",   default=0.03))
MAX_WEEKLY_LOSS_PCT  = float(_S("MAX_WEEKLY_LOSS_PCT",  default=0.07))
TRAIL_PCT            = float(_S("TRAIL_PCT",            default=0.02))
CORRELATION_THRESHOLD= float(_S("CORRELATION_THRESHOLD",default=0.75))
MAX_SAME_SECTOR      = int(  _S("MAX_SAME_SECTOR",      default=2))
SECTOR_HOT_MULT      = float(_S("SECTOR_HOT_MULT",      default=1.2))
SECTOR_COLD_MULT     = float(_S("SECTOR_COLD_MULT",     default=0.7))

# -----------------------------------------------------------------------------
# PORTFOLIO MEMORY — ChromaDB (M6)
# -----------------------------------------------------------------------------
CHROMA_PERSIST_DIR  = "logs/chromadb/"
CHROMA_COLLECTION   = "trade_history"
SQLITE_DB_FILE      = "logs/trades.db"

# -----------------------------------------------------------------------------
# BACKTESTING (M8)
# -----------------------------------------------------------------------------
BACKTEST_START_DATE = "2020-01-01"
BACKTEST_END_DATE   = "2024-12-31"
BACKTEST_CAPITAL    = 1_000_000
BACKTEST_RESULTS_DIR = "logs/backtest_results/"

# -----------------------------------------------------------------------------
# AGENT MODE
# -----------------------------------------------------------------------------
# "copilot"   → signals shown, user manually executes via dashboard
# "autopilot" → scheduler auto-executes on schedule
AGENT_MODE = _S("AGENT_MODE", "AGENT_MODE", "copilot")

# -----------------------------------------------------------------------------
# DASHBOARD (M9)
# -----------------------------------------------------------------------------
DASHBOARD_PORT        = 8501
DASHBOARD_REFRESH_SEC = int(_S("DASHBOARD_REFRESH_SEC", default=30))
SECTOR_HEATMAP_TOP_N  = 5

# Scheduler scan times (IST) — configurable from dashboard
SCAN_TIME_1 = _S("SCAN_TIME_1", default="09:15")
SCAN_TIME_2 = _S("SCAN_TIME_2", default="15:00")

# -----------------------------------------------------------------------------
# F&O LOT SIZES (NSE — updated Nov 2024)
# -----------------------------------------------------------------------------
NIFTY_LOT_SIZE     = 75
BANKNIFTY_LOT_SIZE = 15
FNO_LOT_SIZES      = {"NIFTY": NIFTY_LOT_SIZE, "BANKNIFTY": BANKNIFTY_LOT_SIZE}

# F&O exit rules
FNO_TP_MULT        = float(_S("FNO_TP_MULT", default=2.0))   # exit when premium 2x
FNO_SL_MULT        = float(_S("FNO_SL_MULT", default=0.70))  # exit when premium -30% (was 0.50 → -50%, too wide)
FNO_MAX_POSITIONS  = int(  _S("FNO_MAX_POSITIONS", default=6))  # max concurrent F&O positions

# Options selling thresholds
FNO_HV_STRADDLE    = float(_S("FNO_HV_STRADDLE", default=18.0))  # HV% above → straddle
FNO_HV_STRANGLE    = float(_S("FNO_HV_STRANGLE", default=12.0))  # HV% above → strangle
FNO_SELL_DAYS      = _S("FNO_SELL_DAYS", default="tue,wed,thu")   # days to sell options
FNO_CHAIN_CACHE_MIN= int(  _S("FNO_CHAIN_CACHE_MIN", default=5))  # options chain cache TTL

# Futures
FUTURES_RISK_FREE_RATE = float(_S("FUTURES_RISK_FREE_RATE", default=0.065))  # 6.5%
FUTURES_DEFAULT_DTE    = int(  _S("FUTURES_DEFAULT_DTE",    default=15))     # mid-month
FUTURES_SL_PCT         = float(_S("FUTURES_SL_PCT",         default=0.02))   # 2% SL
FUTURES_TP_PCT         = float(_S("FUTURES_TP_PCT",         default=0.03))   # 3% TP
FNO_FUT_MARGIN_PCT     = float(_S("FNO_FUT_MARGIN_PCT",     default=0.15))
FNO_SELL_RESERVE_MULT  = float(_S("FNO_SELL_RESERVE_MULT",  default=2.5))
FNO_MAX_STRUCTURES_PER_UNDERLYING = int(_S("FNO_MAX_STRUCTURES_PER_UNDERLYING", default=2))
FNO_MAX_UNDERLYING_EXPOSURE_NIFTY_PCT = float(_S("FNO_MAX_UNDERLYING_EXPOSURE_NIFTY_PCT", default=0.15))
FNO_MAX_UNDERLYING_EXPOSURE_BANKNIFTY_PCT = float(_S("FNO_MAX_UNDERLYING_EXPOSURE_BANKNIFTY_PCT", default=0.15))
FNO_BLOCK_DUPLICATE_FUT_SHORT_WITH_STRADDLE = bool(_S("FNO_BLOCK_DUPLICATE_FUT_SHORT_WITH_STRADDLE", default=True))

# INR conversion rate for combined P&L display
INR_PER_USD  = float(_S("INR_PER_USD",  default=83.0))
INR_PER_USDT = float(_S("INR_PER_USDT", default=83.0))

# -----------------------------------------------------------------------------
# CRYPTO PAPER TRADING
# -----------------------------------------------------------------------------
CRYPTO_USDT_PER_TRADE = float(_S("CRYPTO_USDT_PER_TRADE", default=100.0))  # USDT per position
CRYPTO_TP_PCT         = float(_S("CRYPTO_TP_PCT",         default=0.08))   # 8% target
CRYPTO_SL_PCT         = float(_S("CRYPTO_SL_PCT",         default=0.04))   # 4% stop loss

# -----------------------------------------------------------------------------
# US STOCKS PAPER TRADING
# -----------------------------------------------------------------------------
US_USD_PER_TRADE      = float(_S("US_USD_PER_TRADE",      default=500.0))  # USD per position
US_TP_PCT             = float(_S("US_TP_PCT",             default=0.06))   # 6% target
US_SL_PCT             = float(_S("US_SL_PCT",             default=0.03))   # 3% stop loss

# -----------------------------------------------------------------------------
# INTRADAY TRADING
# -----------------------------------------------------------------------------
INTRADAY_MAX_POSITIONS = int(  _S("INTRADAY_MAX_POSITIONS", default=4))
INTRADAY_RISK_MULT     = float(_S("INTRADAY_RISK_MULT",     default=0.50))  # fraction of normal swing risk
INTRADAY_RR            = float(_S("INTRADAY_RR",            default=1.5))   # reward:risk ratio
INTRADAY_MIN_VOL_SPIKE = float(_S("INTRADAY_MIN_VOL_SPIKE", default=1.5))   # current bar vol vs 20-bar avg
INTRADAY_RSI_LO        = int(  _S("INTRADAY_RSI_LO",        default=40))
INTRADAY_RSI_HI        = int(  _S("INTRADAY_RSI_HI",        default=65))
INTRADAY_MIN_CRITERIA  = int(  _S("INTRADAY_MIN_CRITERIA",  default=3))     # must meet N of 5 criteria

# -----------------------------------------------------------------------------
# PIPELINE PENALTIES (Phase 3 — soften hard-blocks)
# -----------------------------------------------------------------------------
MTF_COUNTER_PENALTY    = float(_S("MTF_COUNTER_PENALTY",    default=0.08))  # penalty for counter-trend MTF
SR_SELL_ZONE_PENALTY   = float(_S("SR_SELL_ZONE_PENALTY",   default=0.10))  # penalty for S/R sell zone

# -----------------------------------------------------------------------------
# ALERTS — Telegram
# -----------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN  = _S("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = _S("TELEGRAM_CHAT_ID",   "TELEGRAM_CHAT_ID",   "")

# -----------------------------------------------------------------------------
# ALERTS — Discord
# -----------------------------------------------------------------------------
DISCORD_BOT_TOKEN   = _S("DISCORD_BOT_TOKEN",  "DISCORD_BOT_TOKEN",  "")
DISCORD_CHANNEL_ID  = _S("DISCORD_CHANNEL_ID", "DISCORD_CHANNEL_ID", "")

# -----------------------------------------------------------------------------
# LOGGING
# -----------------------------------------------------------------------------
LOG_FILE            = "logs/agent.log"
LOG_LEVEL           = "INFO"
