# ── Thomas Scientific Market Intelligence Runner ──────────────────
# Base: Python 3.11 slim (smaller image, faster builds)
FROM python:3.11-slim

LABEL maintainer="Marais Advisory"
LABEL description="Thomas Scientific Market Intelligence Pipeline"

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source — single main.py entrypoint, sink abstraction, shared modules
COPY main.py storage.py engine.py accounts.py prompts.py ./

# Default environment values (override via --env or .env file)
ENV GEMINI_MODEL=gemini-2.5-flash
ENV SEMAPHORE_SIZE=13
ENV MAX_RETRIES=3
ENV API_TIMEOUT_MS=60000
ENV SIGNAL_HARD_TIMEOUT=120
ENV GEMINI_TEMPERATURE=0.2
ENV DAYS_BACK=30
ENV MIN_CAPEX_M=50

# Unbuffered stdout so Azure Container App Logs capture prints immediately
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "main.py"]

# Default: run all categories, 5 accounts each (safe test mode)
CMD ["--category", "all", "--limit", "5"]
