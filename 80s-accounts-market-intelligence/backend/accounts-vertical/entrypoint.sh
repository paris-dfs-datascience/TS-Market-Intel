#!/bin/bash
# ── Thomas Scientific Market Intelligence — Container Entrypoint ──
# Usage examples:
#   docker run ... --category biopharma
#   docker run ... --category clinical_dx --limit 10
#   docker run ... --category all
#   docker run ... --category biopharma --company "ABBOTT"

set -e

CATEGORY=""
LIMIT=""
COMPANY=""
SIGNAL=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --category)  CATEGORY="$2";  shift 2 ;;
        --limit)     LIMIT="$2";     shift 2 ;;
        --company)   COMPANY="$2";   shift 2 ;;
        --signal)    SIGNAL="$2";    shift 2 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# Validate GEMINI_API_KEY
if [ -z "$GEMINI_API_KEY" ]; then
    echo "ERROR: GEMINI_API_KEY is not set. Pass it via --env GEMINI_API_KEY=your_key"
    exit 1
fi

# Build optional args
EXTRA_ARGS=""
[ -n "$LIMIT" ]   && EXTRA_ARGS="$EXTRA_ARGS --limit $LIMIT"
[ -n "$COMPANY" ] && EXTRA_ARGS="$EXTRA_ARGS --company \"$COMPANY\""
[ -n "$SIGNAL" ]  && EXTRA_ARGS="$EXTRA_ARGS --signal $SIGNAL"

# Route to correct runner
case "$CATEGORY" in
    biopharma)    eval python run_biopharma.py   --output /app/output/biopharma_results.json    $EXTRA_ARGS ;;
    clinical_dx)  eval python run_clinical_dx.py --output /app/output/clinical_dx_results.json  $EXTRA_ARGS ;;
    cdmo_cro)     eval python run_cdmo_cro.py    --output /app/output/cdmo_cro_results.json     $EXTRA_ARGS ;;
    education)    eval python run_education.py   --output /app/output/education_results.json    $EXTRA_ARGS ;;
    hospital)     eval python run_hospital.py    --output /app/output/hospital_results.json     $EXTRA_ARGS ;;
    industrial)   eval python run_industrial.py  --output /app/output/industrial_results.json   $EXTRA_ARGS ;;
    government)   eval python run_government.py  --output /app/output/government_results.json   $EXTRA_ARGS ;;
    all)          eval python run_all_accounts.py --output /app/output/all_results.json         $EXTRA_ARGS ;;
    *)
        echo "ERROR: Unknown category '$CATEGORY'"
        echo "Valid categories: biopharma, clinical_dx, cdmo_cro, education, hospital, industrial, government, all"
        exit 1
        ;;
esac
