# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A market intelligence platform for Thomas Scientific that uses Gemini 2.5 Flash (with Google Search grounding) to detect sales signals across 237 B2B scientific supply accounts across 7 industry verticals. Runs are parallelized async API calls, checkpointed to JSON, and containerized for deployment.

## Local Setup

```bash
# From the repo root — run once
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Copy and fill in the API key
cp 80s-accounts-market-intelligence/backend/accounts-vertical/.env.sample \
   80s-accounts-market-intelligence/backend/accounts-vertical/.env
```

Activate the venv (`source .venv/bin/activate`) at the start of every session.

## Running the System

### Local (per category)
```bash
cd 80s-accounts-market-intelligence/backend/accounts-vertical

# Run a full category
python run_biopharma.py --output biopharma_results.json

# Run a single signal type
python run_biopharma.py --signal grant

# Run a single company
python run_biopharma.py --company "PFIZER"

# Provide API key directly (overrides .env)
python run_biopharma.py --api-key YOUR_KEY
```

Available runners: `run_biopharma.py`, `run_education.py`, `run_cdmo_cro.py`, `run_clinical_dx.py`, `run_hospital.py`, `run_industrial.py`, `run_government.py`, `run_super80.py`

### Docker
```bash
cd 80s-accounts-market-intelligence/backend/accounts-vertical
docker compose up --build

# Override category at runtime
docker compose run market-intel --category biopharma --limit 10
docker compose run market-intel --category all
```

`entrypoint.sh` routes `--category` to the correct runner script.

### NIH Pipeline
```bash
cd "NIH Pipeline"
python nih_grants.py       # Fetches from NIH RePORTER API → all_nih_grants.json
python nsf_grants.py       # Fetches NSF grants → all_nsf_grants.json
```

### Universities Vertical
```bash
cd backend/Universities-Vertical
python run_all_universities.py
```

## Environment Variables

Copy `.env.sample` to `.env` in the runner directory (or pass `--api-key`):

```
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash   # default
CALL_DELAY=6                    # seconds between calls (~10 RPM free tier)
SEMAPHORE_SIZE=13               # max concurrent API calls
MAX_RETRIES=3
SIGNAL_HARD_TIMEOUT=120         # seconds per signal call
GEMINI_TEMPERATURE=0.2
DAYS_BACK=30                    # lookback window for signals
MIN_CAPEX_M=50                  # minimum capital project value to report ($M)
```

## Architecture

### Core Files (80s pipeline)

| File | Purpose |
|------|---------|
| `accounts.py` | 237 accounts organized by industry (`ACCOUNTS` dict) + `ACCOUNT_ALIASES` mapping |
| `runner.py` | Shared async execution engine — semaphore, retries, checkpointing, color logging |
| `prompts.py` | 21 signal prompts (`build_prompt()`), vertical→signal mapping (`CATEGORY_TRIGGERS`), output schemas (`FIELD_MAPS`) |
| `run_<category>.py` | Thin wrapper that selects accounts + signals for that vertical |

### Execution Flow

1. Runner loads accounts for the target category from `accounts.py` (`ACCOUNTS[category]`)
2. For each account × signal combination, builds a prompt from `prompts.py` (category-aware language via `build_prompt()`)
3. Async semaphore limits concurrent Gemini API calls per account
4. Each call enforced by `asyncio.wait_for()` with `SIGNAL_HARD_TIMEOUT`
5. Results written atomically (`.tmp` → `os.replace()`) to the output JSON after each account
6. On re-run, completed (account, signal) pairs are skipped via checkpoint loaded from the existing output file

### Output Format

Each category run produces a JSON array of account objects:
```json
[
  {
    "account": "PFIZER",
    "category": "BioPharma",
    "timestamp": "2026-04-23T10:30:00",
    "signals": {
      "grant": [
        {
          "summary": "...",
          "recipient": "...",
          "agency": "NIH/BARDA",
          "amount": "$12M",
          "event_date": "April 10, 2026",
          "why_it_matters": "...",
          "source_url": "https://..."
        }
      ],
      "pipeline": [],
      "capital": [...]
    }
  }
]
```

Each signal type has its own field schema defined in `FIELD_MAPS` in `prompts.py`.

### Signal Types (21 total, defined in `prompts.py` → `CATEGORY_TRIGGERS`)

| Signal | Verticals |
|--------|-----------|
| grant | Edu, BioPharma, Clinical, Hospital, Gov |
| faculty | Edu, Hospital |
| capital | All |
| contract | All except Industrial |
| pipeline | BioPharma, CDMO, Clinical, Hospital |
| expansion | All |
| partnership | BioPharma, CDMO, Clinical, Hospital, Industrial |
| funding | Edu, BioPharma, CDMO, Clinical, Hospital, Industrial |
| project | All |
| regulatory | BioPharma, CDMO, Clinical, Hospital |
| hiring | BioPharma, CDMO, Industrial |
| tender | Hospital, Gov |
| breakthrough | Edu |
| ma | BioPharma, CDMO |
| spinoff | BioPharma |
| production | Industrial |
| volume | Clinical |
| competitive | Clinical |
| mandate | Gov |
| legislation | Gov |
| closure | All |

## Key Design Decisions

- **Atomic writes**: results saved as `.tmp` then `os.replace()` to avoid corruption on interrupt
- **Checkpointing**: completed (account, signal) pairs stored in the output JSON; re-runs skip them
- **Category-aware prompts**: same signal type (e.g. "grant") uses different search language for BioPharma vs. Education
- **Account aliases**: `accounts.py` maps internal names to known public/alternate names so Gemini search finds the right company
- **ANSI stripping for file logs**: console gets colored output; file handler strips escape codes

## Dependencies

All dependencies are pinned in `requirements.txt` at the repo root. Install into the `.venv` virtual environment as described in Local Setup above.

| Package | Purpose |
|---------|---------|
| `google-genai` | Gemini API |
| `python-dotenv` | `.env` file loading |
| `openpyxl` | Excel output |
| `requests` | NIH RePORTER REST API |
