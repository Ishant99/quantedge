# =============================================================================
# config.py — Master configuration for Trading Agent
# Change TRADING_MODE to switch between paper / live without touching any
# other file. All modules import from here.
# =============================================================================

import os
from dotenv import load_dotenv

load_dotenv()

# -----------------------------------------------------------------------------
# TRADING MODE — the single flag that controls everything
# "paper" → no real orders, virtual portfolio, yfinance data
# "live"  → real Zerodha orders, Kite WebSocket data
# -----------------------------------------------------------------------------
TRADING_MODE = os.getenv("TRADING_MODE", "paper")   # paper | live

# -----------------------------------------------------------------------------
# VIRTUAL PORTFOLIO (paper mode)
# -----------------------------------------------------------------------------
VIRTUAL_CAPITAL = 1_000_000          # ₹10 Lakh starting virtual balance
VIRTUAL_PORTFOLIO_FILE = "logs/virtual_portfolio.json"

# -----------------------------------------------------------------------------
# ZERODHA KITE API (live mode — fill via .env file)
# -----------------------------------------------------------------------------
KITE_API_KEY    = os.getenv("KITE_API_KEY", "")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
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
MIN_TA_SCORE     = 5.0               # Minimum TA score to consider a stock
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
TA_WEIGHT        = 0.50              # Weight of TA score in final signal
SENTIMENT_WEIGHT = 0.30              # Weight of sentiment score
TREND_WEIGHT     = 0.20              # Weight of broader trend
MIN_CONFIDENCE   = 0.60             # Min confidence to generate BUY/SELL
TOP_N_SIGNALS    = 10               # Top N stocks output each morning

# -----------------------------------------------------------------------------
# RISK MANAGEMENT (M5)
# -----------------------------------------------------------------------------
RISK_PER_TRADE_PCT   = 0.02         # 2% of portfolio per trade
MAX_OPEN_POSITIONS   = 5            # Hard cap on concurrent positions
REWARD_RISK_RATIO    = 2.0          # Take profit = 2× stop loss distance
MAX_DRAWDOWN_PCT     = 0.10         # Agent pauses if portfolio drops 10%
ATR_SL_MULTIPLIER    = 1.5          # ATR multiplier for stop-loss distance
MAX_DAILY_LOSS_PCT   = 0.03         # Daily circuit breaker threshold (3%)
MAX_WEEKLY_LOSS_PCT  = 0.07         # Weekly circuit breaker threshold (7%)
TRAIL_PCT            = 0.02         # Trailing stop percentage (2%)
CORRELATION_THRESHOLD= 0.75         # Max allowed correlation between positions
MAX_SAME_SECTOR      = 2            # Max positions in same sector
SECTOR_HOT_MULT      = 1.2          # Position size boost for hot sectors
SECTOR_COLD_MULT     = 0.7          # Position size reduction for cold sectors

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
AGENT_MODE = os.getenv("AGENT_MODE", "copilot")

# -----------------------------------------------------------------------------
# DASHBOARD (M9)
# -----------------------------------------------------------------------------
DASHBOARD_PORT        = 8501
DASHBOARD_REFRESH_SEC = 30
SECTOR_HEATMAP_TOP_N  = 5    # top N stocks shown per sector in heatmap drilldown

# -----------------------------------------------------------------------------
# ALERTS — Telegram (M9)
# -----------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")

# -----------------------------------------------------------------------------
# LOGGING
# -----------------------------------------------------------------------------
LOG_FILE            = "logs/agent.log"
LOG_LEVEL           = "INFO"
