# =============================================================================
# Dockerfile — NSE Trading Agent
# Optimized for Oracle Free Tier (1GB RAM, 1 OCPU)
# =============================================================================

FROM python:3.11-slim

# Metadata
LABEL maintainer="Ishant99" \
      description="NSE Trading Agent — paper/live trading with Streamlit dashboard"

# Set working directory
WORKDIR /app

# Install system dependencies (minimal for slim image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .

# Install Python dependencies
# --no-cache-dir saves ~50MB on 1GB RAM VM
RUN pip install --upgrade pip --no-cache-dir \
    && pip install --no-cache-dir -r requirements.txt

# Copy project source
COPY . .

# Create logs directory (will be mounted as volume in production)
RUN mkdir -p logs/market_data logs/backtest_results logs/chromadb \
    && touch logs/.gitkeep

# Non-root user for security
RUN useradd -m -u 1000 trader \
    && chown -R trader:trader /app
USER trader

# Expose Streamlit port
EXPOSE 8501

# Default: run the scheduler (overridden in docker-compose for dashboard service)
CMD ["python", "scheduler/scheduler.py"]
