# Thomas Scientific — Market Intelligence

Automated pipeline that identifies sales opportunities for Thomas Scientific, a B2B scientific supply distributor. The system monitors key accounts across 7 industry verticals for **signals** — events that indicate upcoming demand for lab supplies, reagents, consumables, and equipment. Examples of signals include a university receiving a large NIH grant (which means new lab spending), a pharma company opening a new manufacturing site, or a hospital issuing a procurement tender for lab equipment.

It uses **Gemini 2.5 Flash with Google Search grounding** to surface real-time intelligence from the web — press releases, SEC filings, news articles, and official publications.

---

## What's in this repo

| Folder | What it does |
|--------|-------------|
| `80s-accounts-market-intelligence/` | Main pipeline — scans 237 named accounts across 7 industry verticals |
| `backend/Universities-Vertical/` | Lighter pipeline for 14 priority research universities |
| `NIH Pipeline/` | Fetches structured grant data directly from the NIH RePORTER API (no AI) |

---

## Prerequisites

- Python 3.11 (`brew install python@3.11` on macOS)
- A Google AI Studio API key with access to Gemini 2.5 Flash and Google Search grounding

---

## Setup

```bash
# 1. Create and activate a virtual environment (run once, from the repo root)
python3.11 -m venv .venv
source .venv/bin/activate

# 2. Install all dependencies
pip install -r requirements.txt

# 3. Create a .env file from the sample and fill in your API key
cp 80s-accounts-market-intelligence/backend/accounts-vertical/.env.sample \
   80s-accounts-market-intelligence/backend/accounts-vertical/.env
# then edit .env and set GEMINI_API_KEY
```

Activate the venv (`source .venv/bin/activate`) at the start of every session before running any pipeline script.

---

## Pipeline 1 — 80s Accounts (main pipeline)

Scans **237 accounts** across 7 industry verticals for up to 21 signal types. "80s Accounts" refers to two named account tiers:

- **Super80** — 7 highest-priority cross-vertical accounts (LABCORP, TAKEDA, TEMPUS, IQVIA, IVF STORE, LABCORP, AMAZON MARKET PLACE, DEFENSE LOGISTICS)
- **Customer80** — the remaining ~230 accounts across all verticals

All scripts live in `80s-accounts-market-intelligence/backend/accounts-vertical/`.

### Method 1 — Local

Each script targets one industry segment — a group of accounts that share similar buying patterns and signal types. For example, running `run_biopharma.py` searches all 51 BioPharma accounts (Pfizer, Moderna, AstraZeneca, etc.) for signals like drug pipeline updates, regulatory approvals, and hiring announcements that indicate upcoming lab supply purchases.

- **Accounts per vertical** are defined in [`accounts.py`](80s-accounts-market-intelligence/backend/accounts-vertical/accounts.py) under the `ACCOUNTS` dict.
- **Which signals run for each vertical** are defined in [`prompts.py`](80s-accounts-market-intelligence/backend/accounts-vertical/prompts.py) under `CATEGORY_TRIGGERS`.

```bash
cd 80s-accounts-market-intelligence/backend/accounts-vertical

python run_biopharma.py          # BioPharma — 51 accounts, 13 signals
python run_education.py          # Education & Research — 46 accounts, 9 signals
python run_cdmo_cro.py           # CDMO / CRO — 17 accounts, 11 signals
python run_clinical_dx.py        # Clinical / Mol Dx — 42 accounts, 12 signals
python run_hospital.py           # Hospital & Health Systems — 15 accounts, 12 signals
python run_industrial.py         # Industrial — 49 accounts, 8 signals
python run_government.py         # Government — 14 accounts, 9 signals
python run_super80.py            # Super80 priority accounts (cross-vertical)
```

#### Flags (all scripts support the same options)

```bash
# Run a single signal type only
python run_biopharma.py --signal pipeline
python run_government.py --signal grant

# Run for a single account
python run_biopharma.py --company "PFIZER"
python run_clinical_dx.py --company "LABCORP"

# Save to a custom output file
python run_biopharma.py --output biopharma_7days.json

# Limit to first N accounts (useful for testing)
python run_biopharma.py --limit 5

# Pass API key directly (overrides .env)
python run_biopharma.py --api-key YOUR_KEY
```

#### Default output filenames

Each script writes to a JSON file in the same directory:

| Script | Default output |
|--------|---------------|
| `run_biopharma.py` | `biopharma_results.json` |
| `run_education.py` | `education_results.json` |
| `run_cdmo_cro.py` | `cdmo_cro_results.json` |
| `run_clinical_dx.py` | `clinical_dx_results.json` |
| `run_hospital.py` | `hospital_results.json` |
| `run_industrial.py` | `industrial_results.json` |
| `run_government.py` | `government_results.json` |
| `run_super80.py` | `super80_results.json` |

A usage sidecar file (`*_usage.json`) with cost and performance stats, and a plain-text log (`*.log`), are written alongside each output.

#### Resuming an interrupted run

Just re-run the same command. The checkpoint system tracks completed (account, signal) pairs and skips them automatically:

```bash
python run_biopharma.py   # resumes from where it stopped
```

#### Handling timeouts

If a run produces timeouts (visible in the `.log` file), retry those specific pairs with a higher timeout:

```bash
python rerun_timeouts.py biopharma_results.log
python rerun_timeouts.py biopharma_results.log --timeout 180 --concurrency 3
```

Successful rerun results are merged back into the original results JSON automatically.

### Method 2 — Docker

Runs the same pipeline inside a container — no local Python setup required beyond Docker itself. The Universities Vertical and NIH Pipeline do not have Docker support and must be run locally.

The container requires `GEMINI_API_KEY` set in `.env` (loaded automatically by `docker-compose.yml`). Results are written to `./output/` which is mounted as a volume so they persist after the container stops.

The two commands below are **alternatives** — pick one depending on what you want to run:

```bash
cd 80s-accounts-market-intelligence/backend/accounts-vertical

# Option A — run with defaults (all categories, limit 5 accounts each)
docker compose up --build

# Option B — run with a specific category (add --build on first run to build the image)
# --rm automatically removes the container when the pipeline finishes
docker compose run --rm --build market-intel --category biopharma
docker compose run --rm --build market-intel --category biopharma --limit 10
docker compose run --rm --build market-intel --category clinical_dx --company "LABCORP"
docker compose run --rm --build market-intel --category all
```

Once the image is built, subsequent `docker compose run` calls can omit `--build`.

Valid `--category` values: `biopharma`, `clinical_dx`, `cdmo_cro`, `education`, `hospital`, `industrial`, `government`, `all`.

#### Cleanup

To remove the stopped container and the built image when you're done:

```bash
docker compose down --rmi all --remove-orphans
```

This does **not** touch the `./output/` folder — your results files remain on disk.

---

## Pipeline 2 — Universities Vertical

A focused pipeline targeting 14 priority research universities and hospital research institutions, running 4 signal types (grant, faculty, capital, contract). The full list of universities is defined in the `UNIVERSITIES` list in [`backend/Universities-Vertical/run_all_universities.py`](backend/Universities-Vertical/run_all_universities.py).

```bash
cd backend/Universities-Vertical

python run_all_universities.py                          # all universities, all triggers
python run_all_universities.py --trigger grant          # single trigger across all universities
python run_all_universities.py --output results.json    # save results to JSON
```

This pipeline runs synchronously (one university at a time, one trigger at a time) unlike the async parallel architecture of the 80s Accounts pipeline. Its output is a standalone JSON and is not currently included in the merged `unified_signals.json`.

---

## Pipeline 3 — NIH Grant Fetch

Pulls structured grant data directly from the NIH RePORTER API — no AI involved, so results are exact and structured. Covers Education & Research and Hospital accounts. The NIH fiscal year runs October–September; the fetch covers the current and 2 prior fiscal years (up to 100 grants per account).

```bash
cd "NIH Pipeline"
pip install requests openpyxl     # if not already installed

python nih_grants.py              # fetches grants → all_nih_grants.json
python nsf_grants.py              # fetches NSF grants → all_nsf_grants.json
python build_excel.py             # converts JSON output to Excel
```

---

## Merging all sources into a unified output (80s Accounts + NIH/NSF)

After running the 80s Accounts category scripts and the NIH/NSF fetch, merge everything into a single file:

```bash
cd 80s-accounts-market-intelligence/backend/accounts-vertical
python merge_signals.py                          # writes unified_signals.json
python merge_signals.py --output my_output.json  # custom path
```

`merge_signals.py` reads all `*_results.json` files from the 80s Accounts pipeline and the NIH/NSF JSON files, deduplicates, and produces `unified_signals.json`.

> The Universities Vertical output is not currently included in the merge step — it uses a different JSON schema (`"university"` / `"triggers"` keys) compared to the 80s Accounts format (`"account"` / `"signals"` keys). University results are distributed as a standalone file.

To convert the merged output into a formatted Excel workbook:

```bash
python build_excel.py    # reads unified_signals.json → unified_signals.xlsx
```

---

## Configuration

All settings can be overridden via environment variables (in `.env` or your shell):

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *(required)* | Google AI Studio API key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model to use |
| `DAYS_BACK` | `30` | Lookback window for signals (days) — set to match your run cadence |
| `CALL_DELAY` | `6` | Seconds between calls. Default of 6s ≈ 10 RPM, calibrated for the free tier. Reduce on a paid plan. |
| `SEMAPHORE_SIZE` | `13` | Max concurrent API calls per account |
| `MAX_RETRIES` | `3` | Attempts per signal before giving up |
| `SIGNAL_HARD_TIMEOUT` | `120` | Wall-clock kill per signal call (seconds) |
| `GEMINI_TEMPERATURE` | `0.2` | Model temperature |
| `MIN_CAPEX_M` | `50` | Minimum capital project value to report ($M) |
| `SAVE_FREQUENCY` | `1` | Checkpoint every N accounts |

---

## Signal types

A **signal** is a real-world business event that indicates an account is likely to increase spending on lab supplies. For example, a pharma company receiving FDA approval for a new drug signals a manufacturing scale-up — and therefore new demand for reagents and consumables. A hospital issuing a procurement tender is a direct purchasing opportunity.

21 signals are defined in [`prompts.py`](80s-accounts-market-intelligence/backend/accounts-vertical/prompts.py) — `CATEGORY_TRIGGERS` maps each vertical to its signals, `build_prompt()` contains the search logic for each signal, and `FIELD_MAPS` defines the output schema. Distributed across verticals based on relevance:

| Signal | What it detects | Verticals |
|--------|----------------|-----------|
| `grant` | NIH/NSF/BARDA/government grant awards | Edu, BioPharma, Clinical, Hospital, Gov |
| `faculty` | New faculty or research leadership hires | Edu, Hospital |
| `capital` | New lab/facility/manufacturing construction (≥$50M) | All |
| `contract` | Open RFPs, procurement bids, expiring supply contracts | All except Industrial |
| `pipeline` | New drugs, IND filings, clinical trials, diagnostic assay launches | BioPharma, CDMO, Clinical, Hospital |
| `expansion` | New sites, geographic entry, capacity expansion (≥$5M or ≥25 hires) | All |
| `partnership` | Licensing deals, co-development agreements, joint ventures (≥$5M) | BioPharma, CDMO, Clinical, Hospital, Industrial |
| `funding` | VC rounds, government program awards, strategic investments | Edu, BioPharma, CDMO, Clinical, Hospital, Industrial |
| `project` | New multi-year programs with disclosed budgets | All |
| `regulatory` | FDA approvals, warning letters, GMP inspections, accreditation changes | BioPharma, CDMO, Clinical, Hospital |
| `hiring` | Bulk hiring announcements (≥50 net new hires) | BioPharma, CDMO, Industrial |
| `tender` | Public procurement tenders, GPO bids | Hospital, Gov |
| `breakthrough` | Nobel prizes, landmark publications, major science awards | Edu |
| `ma` | Mergers & acquisitions | BioPharma, CDMO |
| `spinoff` | New company formations and carve-outs | BioPharma |
| `production` | Production line changes, plant retooling | Industrial |
| `volume` | Lab test volume increases, new test menu additions | Clinical |
| `competitive` | Competitor wins on lab supply contracts | Clinical |
| `mandate` | Government mandates triggering lab spending | Gov |
| `legislation` | Budget appropriations for lab infrastructure | Gov |
| `closure` | Facility closures, lab shutdowns, programme terminations | All |

---

## Output format

Each 80s Accounts category run produces a JSON array of account objects:

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

Each signal type has its own field schema (defined in `prompts.py` → `FIELD_MAPS`). All include `summary`, `event_date`, `why_it_matters`, and `source_url`.

---

## Adding or modifying accounts

Accounts are defined in `80s-accounts-market-intelligence/backend/accounts-vertical/accounts.py`:

- `ACCOUNTS` — full dict keyed by category
- `SUPER80` — list of 7 highest-priority accounts
- `ACCOUNT_ALIASES` — maps internal names to public/alternate names used in Gemini search prompts

When adding a new account: add it to `ACCOUNTS[category]` and, if the internal name differs from the public name (e.g. `"MASSACHUSETTS INSTITUTE OF TEC"` → `["MIT", "Massachusetts Institute of Technology"]`), add an entry to `ACCOUNT_ALIASES`.

---

## Typical full-run workflow

```
1. Run all 7 category scripts (can run in parallel on separate machines/terminals)
2. Run run_all_universities.py to produce the university signals JSON
3. Run nih_grants.py and nsf_grants.py to pull structured NIH/NSF grant data
4. Run merge_signals.py to produce unified_signals.json (80s Accounts + NIH/NSF)
5. Run build_excel.py to produce the formatted Excel deliverable
```
