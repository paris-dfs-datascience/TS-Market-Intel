#!/usr/bin/env bash
#
# create_scheduled_jobs.sh — stand up the two monthly scheduled Container Apps Jobs.
#
#   thomas-intel-job-m1   1st  of each month, 06:00 UTC — all verticals EXCEPT
#                         Clinical / Molecular Diagnostics and Government
#   thomas-intel-job-m15  15th of each month, 06:00 UTC — Education & Research,
#                         Clinical / Molecular Diagnostics, Government
#
# The existing `thomas-intel-job` is READ ONLY here: it is the template the two new
# jobs are cloned from, and it stays Manual-triggered for ad-hoc runs. This script
# never updates it.
#
# Everything that decides WHERE output lands (env vars, managed identity, image,
# registry) is read off the live job, so the new jobs write to the same blob
# container, fetch the same Key Vault secret, and run the same code. Only the
# trigger and the --categories arg differ. Step 5 proves that.
#
# Run this from a GitHub Codespace on this repo — `az` and `jq` are preinstalled there and
# no local Azure CLI or Docker Desktop is needed. Log in first with device code (a
# Codespace has no local browser):
#
#   az login --use-device-code
#
# Usage:
#   bash deploy/create_scheduled_jobs.sh            # precheck + create + verify
#   bash deploy/create_scheduled_jobs.sh --check    # precheck only, creates nothing
#
set -euo pipefail

SUB="d0fb2aac-3e96-49cc-8b7f-a84c8caf4973"
RG="marias_advisory_ai_rg"
SRC_JOB="thomas-intel-job"

# name | cron (UTC) | --categories value
JOB_SPECS=(
  "thomas-intel-job-m1|0 6 1 * *|biopharma,cdmo_cro,education,hospital,industrial"
  "thomas-intel-job-m15|0 6 15 * *|education,clinical_dx,government"
)

# Plain `[ ... ] && VAR=1` would abort the script under `set -e` when the test is false.
CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then CHECK_ONLY=1; fi

for tool in az jq; do
  if ! command -v "$tool" >/dev/null; then
    echo "ERROR: '$tool' not found on PATH. Both ship with GitHub Codespaces; if this is a"
    echo "slimmer image, install with:"
    echo "  az:  curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash"
    echo "  jq:  sudo apt-get update && sudo apt-get install -y jq"
    exit 1
  fi
done

echo "── 1. Subscription ────────────────────────────────────────────────"
if ! az account show -o none 2>/dev/null; then
  echo "Not logged in to Azure. In a Codespace there is no local browser, so use:"
  echo "  az login --use-device-code"
  exit 1
fi
az account set --subscription "$SUB"
az account show --query "{subscription:name, user:user.name}" -o table

echo
echo "── 2. Permission precheck ─────────────────────────────────────────"
# Creating a job needs Microsoft.App/jobs/write on the RG *and*
# Microsoft.App/managedEnvironments/join/action on the environment. AcrPush is
# irrelevant — that role only governs pushing images to the registry.
ACTIONS=$(az rest --method GET \
  --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Authorization/permissions?api-version=2022-04-01" \
  --query "value[].actions[]" -o tsv)

can_create=0
can_join=0
while IFS= read -r action; do
  case "$action" in
    "*"|"Microsoft.App/*"|"Microsoft.App/jobs/*"|"Microsoft.App/jobs/write") can_create=1 ;;
  esac
  case "$action" in
    "*"|"Microsoft.App/*"|"Microsoft.App/managedEnvironments/*"|"Microsoft.App/managedEnvironments/join/action") can_join=1 ;;
  esac
done <<< "$ACTIONS"

echo "Microsoft.App actions granted to you on $RG:"
echo "$ACTIONS" | grep -i '^Microsoft\.App' || echo "  (none — you have no Microsoft.App actions here)"
echo
echo "  create a job (Microsoft.App/jobs/write) ................. $([ $can_create = 1 ] && echo YES || echo 'NOT FOUND')"
echo "  join the environment (managedEnvironments/join/action) .. $([ $can_join = 1 ] && echo YES || echo 'NOT FOUND')"

if [ $can_create = 0 ] || [ $can_join = 0 ]; then
  echo
  echo "One or both permissions are missing, so 'az containerapp job create' will fail"
  echo "with AuthorizationFailed. Nothing is created on a failure — it is safe to try"
  echo "anyway if you think the role list above is incomplete (deny assignments and"
  echo "PIM-eligible roles do not always show up here)."
  echo
  echo "To proceed you need either:"
  echo "  - 'Container Apps Contributor' (or Contributor) on $RG, or"
  echo "  - the no-new-resources fallback in DEPLOYMENT.md, which only needs the"
  echo "    job-update permission you already have."
  if [ $CHECK_ONLY = 0 ]; then
    printf "\nAttempt creation anyway? [y/N] "
    read -r reply
    case "$reply" in [yY]*) ;; *) echo "Stopped. Nothing changed."; exit 0 ;; esac
  fi
fi

[ $CHECK_ONLY = 1 ] && { echo; echo "--check: stopping before any change."; exit 0; }

echo
echo "── 3. Reading the live job spec (source of truth) ─────────────────"
SPEC=$(az containerapp job show -n "$SRC_JOB" -g "$RG" -o json)

ENV_ID=$(jq -r '.properties.environmentId' <<<"$SPEC")
IMAGE=$(jq -r '.properties.template.containers[0].image' <<<"$SPEC")
CPU=$(jq -r '.properties.template.containers[0].resources.cpu' <<<"$SPEC")
MEM=$(jq -r '.properties.template.containers[0].resources.memory' <<<"$SPEC")
TIMEOUT=$(jq -r '.properties.configuration.replicaTimeout // 1800' <<<"$SPEC")
RETRY=$(jq -r '.properties.configuration.replicaRetryLimit // 0' <<<"$SPEC")
MI_ID=$(jq -r '.identity.userAssignedIdentities // {} | keys[0] // empty' <<<"$SPEC")
REG_SERVER=$(jq -r '.properties.configuration.registries[0].server // empty' <<<"$SPEC")
REG_IDENTITY=$(jq -r '.properties.configuration.registries[0].identity // empty' <<<"$SPEC")

# A secretRef env var cannot be reproduced with --env-vars; bail rather than create a
# job that silently loses it. (Expected: all 7 vars are plain values — the Gemini key
# is fetched from Key Vault at runtime, not injected as a secret.)
if jq -e '[.properties.template.containers[0].env[]? | select(has("secretRef"))] | length > 0' >/dev/null <<<"$SPEC"; then
  echo "ERROR: $SRC_JOB has secretRef-backed env vars. Clone it via YAML instead:"
  echo "  az containerapp job show -n $SRC_JOB -g $RG -o yaml > job.yaml   # then edit + 'job create --yaml'"
  exit 1
fi

ENV_ARGS=()
while IFS= read -r kv; do
  if [ -n "$kv" ]; then ENV_ARGS+=("$kv"); fi
done < <(jq -r '.properties.template.containers[0].env[]? | "\(.name)=\(.value)"' <<<"$SPEC")

# replicaTimeout defaults to 1800s (30 min) on a brand-new job, which would kill a
# multi-hour run mid-flight. Copying the source value is the whole point of this block.
echo "  environment ...... $ENV_ID"
echo "  image ............ $IMAGE"
echo "  cpu / memory ..... $CPU / $MEM"
echo "  replica timeout .. ${TIMEOUT}s  ($((TIMEOUT / 3600))h $(((TIMEOUT % 3600) / 60))m)"
echo "  retry limit ...... $RETRY"
echo "  identity ......... ${MI_ID:-NONE}"
echo "  registry ......... ${REG_SERVER:-none} (pull identity: ${REG_IDENTITY:-none})"
echo "  env vars ......... ${#ENV_ARGS[@]} carried over: $(jq -r '[.properties.template.containers[0].env[]?.name] | join(", ")' <<<"$SPEC")"

if [ -z "$MI_ID" ]; then
  echo "ERROR: no user-assigned identity on $SRC_JOB. Without thomas-intel-identity the"
  echo "new jobs get no blob/Key Vault access. Stopping."
  exit 1
fi
if [ "${#ENV_ARGS[@]}" -eq 0 ]; then
  echo "ERROR: read zero env vars off $SRC_JOB. Expected AZURE_CLIENT_ID,"
  echo "AZURE_KEY_VAULT_URL, AZURE_STORAGE_ACCOUNT_URL, AZURE_STORAGE_CONTAINER, ..."
  echo "Creating jobs without them would fail with 'No Gemini API key found'. Stopping."
  exit 1
fi
if [ "$TIMEOUT" -lt 7200 ]; then
  echo "NOTE: source replicaTimeout is only ${TIMEOUT}s — the 1st-of-month run covers 399"
  echo "accounts and may need longer. Raise it on all three jobs if runs get cut off."
fi

echo
echo "── 4. Creating the scheduled jobs ─────────────────────────────────"
for spec in "${JOB_SPECS[@]}"; do
  NAME="${spec%%|*}"
  rest="${spec#*|}"
  CRON="${rest%%|*}"
  CATS="${rest##*|}"

  # Idempotent re-run: if the job is already there, only its cron + args need syncing.
  # 'job update' is a GET-merge-PUT, so env and identity survive it. Deliberately NOT
  # converting the trigger here — a raw PATCH of properties.configuration replaces that
  # whole object and would drop registries/replicaTimeout, the same way a container
  # PATCH drops env (DEPLOYMENT.md gotcha #6). Step 5 flags a wrong trigger instead.
  if EXIST=$(az containerapp job show -n "$NAME" -g "$RG" -o json 2>/dev/null); then
    EXIST_TRIGGER=$(jq -r '.properties.configuration.triggerType' <<<"$EXIST")
    if [ "$EXIST_TRIGGER" != "Schedule" ]; then
      echo "  ✗ $NAME already exists but is $EXIST_TRIGGER-triggered, not Schedule."
      echo "    Converting a trigger in place is the risky PATCH described in"
      echo "    DEPLOYMENT.md. Delete it and re-run this script instead:"
      echo "      az containerapp job delete -n $NAME -g $RG --yes"
      exit 1
    fi
    echo "  $NAME already exists — syncing image, cron and args."
    az containerapp job update -n "$NAME" -g "$RG" \
      --image "$IMAGE" \
      --replica-timeout "$TIMEOUT" \
      --cron-expression "$CRON" \
      --args="--categories=$CATS" \
      -o none
    echo "  ✓ $NAME updated  ($CRON)"
    continue
  fi

  # Empty-array expansion under `set -u` is an error in bash 3.2 (macOS default), hence
  # the ${arr[@]+...} guard.
  REG_FLAGS=()
  if [ -n "$REG_SERVER" ]; then
    REG_FLAGS+=(--registry-server "$REG_SERVER")
    if [ -n "$REG_IDENTITY" ]; then REG_FLAGS+=(--registry-identity "$REG_IDENTITY"); fi
  fi

  echo "  creating $NAME  —  cron '$CRON'  —  --categories=$CATS"
  az containerapp job create \
    -n "$NAME" -g "$RG" \
    --environment "$ENV_ID" \
    --trigger-type Schedule \
    --cron-expression "$CRON" \
    --image "$IMAGE" \
    --cpu "$CPU" --memory "$MEM" \
    --replica-timeout "$TIMEOUT" \
    --replica-retry-limit "$RETRY" \
    --parallelism 1 \
    --replica-completion-count 1 \
    --mi-user-assigned "$MI_ID" \
    ${REG_FLAGS[@]+"${REG_FLAGS[@]}"} \
    --env-vars "${ENV_ARGS[@]}" \
    --command python main.py \
    --args="--categories=$CATS" \
    -o none
  echo "  ✓ $NAME created"
done

echo
echo "── 5. Verify — the three jobs must differ ONLY in trigger and args ─"
for J in "$SRC_JOB" "thomas-intel-job-m1" "thomas-intel-job-m15"; do
  az containerapp job show -n "$J" -g "$RG" --query "{
      job: name,
      trigger: properties.configuration.triggerType,
      cron: properties.configuration.scheduleTriggerConfig.cronExpression,
      command: properties.template.containers[0].command,
      args: properties.template.containers[0].args,
      timeoutSec: properties.configuration.replicaTimeout,
      image: properties.template.containers[0].image
    }" -o json
done

echo "Output-destination fingerprint (image + env + identity). All three must match:"
fingerprint() {
  az containerapp job show -n "$1" -g "$RG" -o json | jq -Sc '{
    image: .properties.template.containers[0].image,
    env: ([.properties.template.containers[0].env[]? | "\(.name)=\(.value)"] | sort),
    identity: (.identity.userAssignedIdentities // {} | keys | sort)
  }'
}
SRC_FP=$(fingerprint "$SRC_JOB")
ok=1
for J in "thomas-intel-job-m1" "thomas-intel-job-m15"; do
  if [ "$(fingerprint "$J")" = "$SRC_FP" ]; then
    echo "  ✓ $J — identical to $SRC_JOB (same blob container, same Key Vault, same image)"
  else
    echo "  ✗ $J — DIFFERS from $SRC_JOB. Do not rely on it; diff below:"
    diff <(echo "$SRC_FP" | jq .) <(fingerprint "$J" | jq .) || true
    ok=0
  fi
done

echo
if [ $ok = 1 ]; then
  echo "Done. Both scheduled jobs write to the same place as $SRC_JOB."
  echo "Next scheduled fire: 1st and 15th at 06:00 UTC (02:00 EDT / 01:00 EST)."
  echo "Smoke-test one cheaply before the 1st — see DEPLOYMENT.md 'Scheduled monthly runs'."
else
  echo "One or more jobs do not match the source. Fix before the 1st."
  exit 1
fi
