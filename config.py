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
MIN_CONFIDENCE   = float(_S("MIN_CONFIDENCE",   "MIN_CONFIDENCE",   0.60))
TOP_N_SIGNALS    = int(  _S("TOP_N_SIGNALS",    "TOP_N_SIGNALS",    10))

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
# ALERTS — Telegram (M9)
# -----------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN  = _S("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = _S("TELEGRAM_CHAT_ID",   "TELEGRAM_CHAT_ID",   "")

# -----------------------------------------------------------------------------
# LOGGING
# -----------------------------------------------------------------------------
LOG_FILE            = "logs/agent.log"
LOG_LEVEL           = "INFO"
