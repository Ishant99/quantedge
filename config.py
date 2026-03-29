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
NSE_TOP_200_FILE = "data/nse_top200_symbols.csv"
MARKET_DATA_DIR  = "logs/market_data/"
SCAN_TIME_IST    = "09:00"           # Daily scan trigger time
EXCHANGE         = "NSE"

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

# -----------------------------------------------------------------------------
# SENTIMENT AGENT (M3)
# -----------------------------------------------------------------------------
RSS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/MCtopnews.xml",
]
SENTIMENT_MODEL  = "llama3"          # Ollama local model
OLLAMA_BASE_URL  = "http://localhost:11434"

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
RISK_PER_TRADE_PCT  = 0.02          # 2% of portfolio per trade
MAX_OPEN_POSITIONS  = 5             # Hard cap on concurrent positions
REWARD_RISK_RATIO   = 2.0           # Take profit = 2× stop loss distance
MAX_DRAWDOWN_PCT    = 0.10          # Agent pauses if portfolio drops 10%

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
# DASHBOARD (M9)
# -----------------------------------------------------------------------------
DASHBOARD_PORT      = 8501
DASHBOARD_REFRESH_SEC = 60

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
