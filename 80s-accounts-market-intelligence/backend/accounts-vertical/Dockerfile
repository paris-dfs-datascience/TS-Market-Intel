# ── Thomas Scientific Market Intelligence Runner ──────────────────
# Base: Python 3.11 slim (smaller image, faster builds)
FROM python:3.11-slim

# Metadata
LABEL maintainer="Marais Advisory"
LABEL description="Thomas Scientific Market Intelligence Pipeline"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all source files
COPY accounts.py .
COPY prompts.py .
COPY runner.py .
COPY run_biopharma.py .
COPY run_clinical_dx.py .
COPY run_cdmo_cro.py .
COPY run_education.py .
COPY run_hospital.py .
COPY run_industrial.py .
COPY run_government.py .
COPY run_all_accounts.py .
COPY rerun_timeouts.py .
COPY entrypoint.sh .

# Output directory — mount a volume here to persist results
RUN mkdir -p /app/output

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Default environment values (override via --env or .env file)
ENV GEMINI_MODEL=gemini-2.5-flash
ENV CALL_DELAY=2
ENV SEMAPHORE_SIZE=13
ENV MAX_RETRIES=3
ENV SAVE_FREQUENCY=1
ENV API_TIMEOUT_MS=60000
ENV SIGNAL_HARD_TIMEOUT=120
ENV GEMINI_TEMPERATURE=0.2
ENV DAYS_BACK=30
ENV MIN_CAPEX_M=50

# Entry point — controls which category to run
ENTRYPOINT ["/app/entrypoint.sh"]

# Default: run all categories, 5 accounts each (safe test mode)
CMD ["--category", "all", "--limit", "5"]
