# Deployment — Azure Container Apps Job

How to ship new code from git into the Azure **Container Apps Job** that runs the
pipeline. Read this end-to-end the first time; the **Gotchas** section is where the
time gets lost.

---

## Azure resources

| Thing | Value |
|---|---|
| Subscription | `Thomas Data Hub` (`d0fb2aac-3e96-49cc-8b7f-a84c8caf4973`) |
| Resource group | `marias_advisory_ai_rg` |
| Container Apps Job | `thomas-intel-job` |
| Container Registry (ACR) | `thomasscientificintel` → `thomasscientificintel.azurecr.io` |
| Image repository | `ts-market-intel` (tags are UTC timestamps, e.g. `20260530142937`) |
| Storage (Blob output) | `adlsacctmarias`, container `market-intel-output` |
| Key Vault | `thomas-intel-kv` (`https://thomas-intel-kv.vault.azure.net/`) |
| Managed Identity | `thomas-intel-identity` — clientId `fe24b379-e8b0-4527-b149-e98b95d26dd5`, principalId `2abae8d1-838c-4d55-ac26-a8a0b439fcc1` |
| Azure SQL | `msql-datahub-server.database.windows.net` / `msql_datahub` |
| Git repo (source) | `https://github.com/paris-dfs-datascience/TS-Market-Intel.git` (public, `main`) |

### Job environment variables (must stay intact)

The job's container carries these — they wire up Key Vault, Blob output, and SQL.
**Never wipe them** (see env-wipe gotcha):

```
AZURE_CLIENT_ID            # the user-assigned MI; DefaultAzureCredential picks it
AZURE_KEY_VAULT_URL        # GEMINI_API_KEY is fetched from here at startup
AZURE_STORAGE_ACCOUNT_URL  # Blob output target
AZURE_STORAGE_CONTAINER    # market-intel-output
AZURE_SQL_SERVER           # only used when --from-sql is passed
AZURE_SQL_DATABASE
```

There is **no** `GEMINI_API_KEY` env var in the job — it comes from Key Vault via the
Managed Identity. That's by design.

---

## Two operator environments

There are two people deploying this, from different setups. Use the right path or the
commands won't exist on your machine:

| Operator | Environment | Path to use |
|---|---|---|
| `matt.paris@thomassci.com` | Mac with Azure CLI + Docker Desktop | the sections below (note `--platform linux/amd64`) |
| `neha.mazumdar@thomassci.com` | **GitHub Codespace only** — no local Azure CLI, no Docker Desktop | "Creating them — from a GitHub Codespace" under [Scheduled monthly runs](#scheduled-monthly-runs). Install `az` first (not in the Codespaces image), then `az login --use-device-code`; Codespaces is amd64 so no `--platform` flag |

Both paths use `az acr login` + `docker build` + `docker push`; neither can use
`az acr build` (see below). Everything after the image push — `job update`, `job start`,
logs — is identical in both.

## Permissions: which build path you can use

The deploy account (`matt.paris@thomassci.com`) holds **AcrPush**, **Container Apps
Jobs Contributor**, **Storage Blob Data Contributor**, **Key Vault Secrets User**,
**Log Analytics Reader**, **Monitoring Contributor** — all at the resource-group scope.

What this means for building the image:

- **Cloud Shell `az acr build` does NOT work** with this account. A cloud build needs
  `Microsoft.ContainerRegistry/registries/scheduleRun/action`, which lives only in the
  generic **Contributor**/**Owner** role on the registry. AcrPush is push-only. You'll
  get `(AuthorizationFailed) ... scheduleRun/action ... not authorized`.
  - To use `az acr build`, someone with Owner must grant Contributor on the registry,
    **or** activate it via PIM if it's an eligible role. Then:
    ```powershell
    az role assignment create --assignee 9ff6065d-f1d0-4ab3-ad5c-e9b568460190 --role Contributor --scope /subscriptions/d0fb2aac-3e96-49cc-8b7f-a84c8caf4973/resourceGroups/marias_advisory_ai_rg/providers/Microsoft.ContainerRegistry/registries/thomasscientificintel
    ```
- **Building on a Mac/laptop with Docker DOES work** with AcrPush — Docker builds
  locally, `docker push` uploads. **This is the supported path below.**

> Cloud Shell has **no Docker daemon** (`docker info` fails), so you cannot
> `docker build` there. Cloud Shell can only build via `az acr build` (needs the role).

---

## Deploy (the working path): build on Mac, push, update job

### 1. Push your code to git

```bash
git push          # to paris-dfs-datascience/TS-Market-Intel, main
```

### 2. Log in to the client Azure env (from the Mac)

```bash
az login                                              # sign in as matt.paris@thomassci.com
az account set --subscription d0fb2aac-3e96-49cc-8b7f-a84c8caf4973
az account show --query "{name:name, user:user.name}" -o table   # expect Thomas Data Hub
```

`az` holds multiple logins at once — this won't disturb your other (DFS) subscription;
switch back later with `az account set --subscription <your-other-sub>`.

### 3. Build + push the image

**Must** pass `--platform linux/amd64` — Apple Silicon builds arm64 by default, which
fails on Azure with an exec-format error.

```bash
az acr login -n thomasscientificintel                 # uses AcrPush
TAG=$(date -u +%Y%m%d%H%M%S)
docker build --platform linux/amd64 -t "thomasscientificintel.azurecr.io/ts-market-intel:$TAG" .
docker push "thomasscientificintel.azurecr.io/ts-market-intel:$TAG"
echo "NEW TAG: $TAG"
```

### 4. Point the job at the new image

`az containerapp job update` does a GET-merge-PUT, so it **preserves env + identity**.

```bash
az containerapp job update \
  -n thomas-intel-job -g marias_advisory_ai_rg \
  --image "thomasscientificintel.azurecr.io/ts-market-intel:<TAG>"
```

> The two scheduled jobs carry their **own** image field — this command does not touch
> them. Re-run `bash deploy/create_scheduled_jobs.sh` after every image update or the
> monthly runs keep executing the old code. See "Scheduled monthly runs" below.

### 5. Make sure the container runs the pipeline (command fix)

The job's stored `command` may be stale (it was once
`["python","test_sql_connection.py"]`, a diagnostic that was later renamed). The
container must run `python main.py`. Set it **explicitly** — do not rely on clearing:

```bash
az containerapp job update \
  -n thomas-intel-job -g marias_advisory_ai_rg \
  --command "python" "main.py"
```

`main.py` defaults to `--category all`, so with empty `args` it runs the full 482-account
pipeline and auto-exports the CSV at the end.

### 6. Verify before starting (env intact, command/args correct)

```bash
az containerapp job show -n thomas-intel-job -g marias_advisory_ai_rg \
  --query "{image:properties.template.containers[0].image, command:properties.template.containers[0].command, args:properties.template.containers[0].args, env:properties.template.containers[0].env[].name}" -o json
```

Expect:
- `image` → your new `<TAG>`
- `command` → `["python","main.py"]`  (NOT `[""]`, NOT `["python test_sql_connection.py"]`)
- `args` → `[]` for a full run
- `env` → all six names listed above. **If any are missing, STOP** and re-add before starting.

### 7. Start and watch

```bash
az containerapp job start -n thomas-intel-job -g marias_advisory_ai_rg

az containerapp job execution list -n thomas-intel-job -g marias_advisory_ai_rg \
  --query "[0].{name:name, status:properties.status, start:properties.startTime}" -o json

az containerapp job logs show -n thomas-intel-job -g marias_advisory_ai_rg \
  --container thomas-intel-job --execution <EXECUTION_NAME> --follow --tail 100
```

Healthy startup signs in the first ~30s of logs:
- **no** `No Gemini API key found` → Key Vault fetch worked
- **no** `can't open file '...'` → command is correct
- account-processing lines scrolling → new code is running

---

## Running a smaller test (N accounts)

`--total-limit` has no env-var equivalent, so it must be passed as a container arg —
and dash-prefixed args are painful through the CLI (see gotcha). Use the **`=` form**
so it's a single token:

```bash
az containerapp job update -n thomas-intel-job -g marias_advisory_ai_rg --args="--total-limit=10"
# verify: args should be ["--total-limit=10"], command still ["python","main.py"]
az containerapp job start -n thomas-intel-job -g marias_advisory_ai_rg
```

Container then runs `python main.py --total-limit=10` (category defaults to `all`,
capped at 10 total). To go back to a full run, set `--args=""` and confirm `args` is
empty before starting.

To pull accounts from Azure SQL instead of the baked-in list, add `--from-sql` the same
way: `--args="--from-sql"` (or set env `ACCOUNTS_SOURCE=sql`, which is dash-free).

---

## Scheduled monthly runs

Two **additional** Container Apps Jobs run the pipeline on a cron. `thomas-intel-job`
itself is untouched — it stays Manual-triggered for ad-hoc runs.

| Job | Cron (UTC) | Fires | Verticals | Accounts |
|---|---|---|---|---|
| `thomas-intel-job-m1` | `0 6 1 * *` | 1st, 06:00 UTC (02:00 EDT / 01:00 EST) | BioPharma, CDMO / CRO, Education & Research, Hospital & Health Systems, Industrial | 399 |
| `thomas-intel-job-m15` | `0 6 15 * *` | 15th, 06:00 UTC | Education & Research, Clinical / Molecular Diagnostics, Government | 143 |

A job can hold only one cron and one fixed arg list, which is why this is two jobs rather
than one with `1,15`. The scope of each run lives in its `args`:

```
--categories=biopharma,cdmo_cro,education,hospital,industrial      # m1
--categories=education,clinical_dx,government                      # m15
```

`--categories` runs the listed verticals in order and then finalizes exactly like
`--category all` — URL repair, then the SF export CSV at
`_export/market_intel_export_<DATE>.csv` for that run's UTC date. Between the two jobs
every vertical is covered, and Education & Research is the only one run twice (by
design). `tests/test_scheduled_categories.py` fails if either stops being true.

**Nothing about the output changes.** Both jobs are cloned from `thomas-intel-job` and
carry the same image, the same `thomas-intel-identity`, and the same env block — so they
write to the same `adlsacctmarias` / `market-intel-output` container, the same
`<COMPANY>/results_<DATE>.json` layout, and fetch the Gemini key from the same Key Vault.

### Permissions

Creating a job needs `Microsoft.App/jobs/write` on the resource group **and**
`Microsoft.App/managedEnvironments/join/action` on the Container Apps environment.
**AcrPush is irrelevant here** — that role only governs pushing images to the registry.

`neha.mazumdar@thomassci.com` has already PATCHed this job's template successfully, which
*is* `Microsoft.App/jobs/write` — so the create action is almost certainly available. The
open question is only the environment-join action. Check it before planning around it:

```bash
bash deploy/create_scheduled_jobs.sh --check    # prints your Microsoft.App actions, creates nothing
```

A create attempt without the role fails instantly with `AuthorizationFailed` and leaves
nothing behind, so trying is cheap. If it is genuinely blocked, use the single-job
fallback below — it needs only the job-*update* permission that's already proven.

### Creating them — from a GitHub Codespace

> Run everything in this section from a **GitHub Codespace** on this repo — not from the
> local Windows terminal, which has no `az` and no Docker. Codespaces ships `docker`
> (running) and `jq`, but **not the Azure CLI** — install it per step 0. The
> Mac/Docker-Desktop path in the sections above is the other operator's setup.

**0. Install the Azure CLI (once per Codespace).** It is not in the default Codespaces
image, so `az` will be "command not found" on a fresh one. Codespaces are ephemeral —
rebuild or create a new one and this has to be repeated:

```bash
az version 2>/dev/null || curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
az version          # confirm before continuing
jq --version        # should already be present
docker info >/dev/null && echo "docker OK"
```

To avoid repeating it, add a `.devcontainer/devcontainer.json` with the
`ghcr.io/devcontainers/features/azure-cli` feature — but note that introducing a
devcontainer to a repo that currently has none changes how every future Codespace is
built, so verify Python 3.11 and docker still work before relying on it.

**1. Log in.** A Codespace has no local browser, so device code is required:

```bash
az login --use-device-code                        # neha.mazumdar@thomassci.com
az account set --subscription d0fb2aac-3e96-49cc-8b7f-a84c8caf4973
```

**2. Deploy the code first — this is not optional.** `--categories` is new code, and the
script clones whatever image tag `thomas-intel-job` currently runs. Clone a
pre-`--categories` image and both scheduled jobs fail on the 1st with
`unrecognized arguments: --categories=...`.

Build and push from the Codespace (`az acr build` fails for this account — it lacks the
registry `listBuildSourceUploadUrl` action; `docker build` + `docker push` is the working
path, and the Codespace is already amd64 so no `--platform` flag is needed):

```bash
git pull                                          # make sure the Codespace has the new code
az acr login -n thomasscientificintel
TAG=$(date -u +%Y%m%d%H%M%S)
docker build -t "thomasscientificintel.azurecr.io/ts-market-intel:$TAG" -f Dockerfile .
docker push "thomasscientificintel.azurecr.io/ts-market-intel:$TAG"
az containerapp job update -n thomas-intel-job -g marias_advisory_ai_rg \
  --image "thomasscientificintel.azurecr.io/ts-market-intel:$TAG"
echo "NEW TAG: $TAG"
```

**3. Create the scheduled jobs.**

```bash
bash -n deploy/create_scheduled_jobs.sh           # syntax check, no side effects
bash deploy/create_scheduled_jobs.sh --check      # permission precheck, creates nothing
bash deploy/create_scheduled_jobs.sh              # create + verify
```

Confirm the image the script reports in its step 3 is the `$TAG` you just pushed — that's
the check that stops both monthly jobs from running pre-`--categories` code.

The script reads image, CPU/memory, `replicaTimeout`, registry config, managed identity
and the full env block off the **live** `thomas-intel-job` and clones them, so none of it
is hand-typed. It is idempotent — re-run it after a new image build to re-point both
scheduled jobs. It finishes by printing a fingerprint (image + env + identity) for all
three jobs and asserting the two new ones match the source.

Two things it copies that are easy to miss by hand, and that silently break a run:

- **`replicaTimeout`** — a brand-new job defaults to 1800s (30 min). The 399-account run
  takes hours and would be killed mid-flight.
- **the user-assigned identity** — without `thomas-intel-identity` attached (and
  `AZURE_CLIENT_ID` in env pointing at it), the job gets a system-assigned identity with
  no blob or Key Vault access and dies on startup.

### Smoke-test before the 1st

Don't wait for the cron to find out. A Schedule-triggered job can still be started by
hand, so run one cheaply with the scope temporarily narrowed to two accounts:

```bash
az containerapp job update -n thomas-intel-job-m15 -g marias_advisory_ai_rg --args="--total-limit=2"
az containerapp job start  -n thomas-intel-job-m15 -g marias_advisory_ai_rg
```

`args` has to stay a **single** token (gotcha #5), so it's one flag or the other —
`--total-limit=2` alone is the better test: category defaults to `all`, it caps at 2
accounts, and it still exercises the whole path end to end (image → Key Vault fetch →
Gemini call → blob write → export CSV). Watch it with the same log commands as a manual
run, and confirm no `No Gemini API key found` and no `AuthorizationPermissionMismatch`.

**Then put the real args back and confirm**, or the 15th runs a 2-account month:

```bash
az containerapp job update -n thomas-intel-job-m15 -g marias_advisory_ai_rg \
  --args="--categories=education,clinical_dx,government"
az containerapp job show -n thomas-intel-job-m15 -g marias_advisory_ai_rg \
  --query "properties.template.containers[0].args" -o json
```

`args` must read as **one** token: `["--categories=education,clinical_dx,government"]`.
Two tokens or a split-on-comma list means the CLI mangled it — see gotcha #5.

Note the smoke test also regenerates `_export/market_intel_export_<TODAY>.csv`, so don't
run it on a day a real run's CSV matters.

### Did it fire?

```bash
for J in thomas-intel-job-m1 thomas-intel-job-m15; do
  az containerapp job execution list -n $J -g marias_advisory_ai_rg \
    --query "[0].{job:'$J', status:properties.status, start:properties.startTime, end:properties.endTime}" -o json
done
```

Logs are per-execution, same as a manual run (see RUN_AZURE_VSCODE.md §5). Container Apps
does not notify on failure — if you want to know without checking, add an alert on the
job's `Failed` executions.

### Changing the scope or the schedule later

The category sets live in **two** places that must agree: each job's `--categories` arg,
and `MONTHLY_SCHEDULE` in `main.py` (used by the fallback below). `tests/test_scheduled_categories.py`
pins both — update the constants there too, and it will tell you if a newly added vertical
is covered by neither run.

```bash
# change the time (e.g. to 09:00 UTC)
az containerapp job update -n thomas-intel-job-m1 -g marias_advisory_ai_rg --cron-expression "0 9 1 * *"
# change the scope
az containerapp job update -n thomas-intel-job-m1 -g marias_advisory_ai_rg --args="--categories=biopharma,industrial"
```

### Fallback: one job, day-aware (no new resources)

If job creation is blocked, convert `thomas-intel-job` itself to a `0 6 1,15 * *`
schedule and let the code pick the scope from the UTC day-of-month:

```bash
az containerapp job update -n thomas-intel-job -g marias_advisory_ai_rg --args="--monthly-schedule"
```

`--monthly-schedule` reads `MONTHLY_SCHEDULE` in `main.py`: day 1 → the five-vertical
sweep, day 15 → the three-vertical sweep, any other day → logs why and exits 0 without
running. Then flip the trigger to Schedule. `az containerapp job update` cannot change
`triggerType`, so this needs a raw PATCH — and per gotcha #6 a PATCH of
`properties.configuration` **replaces that whole object**, so send the existing
configuration back with only the trigger changed:

```bash
JOB=thomas-intel-job; RG=marias_advisory_ai_rg
SUB=d0fb2aac-3e96-49cc-8b7f-a84c8caf4973
BODY=$(mktemp /tmp/jobpatch.XXXXXX.json)
az containerapp job show -n $JOB -g $RG -o json | jq '{properties:{configuration:(.properties.configuration
  | .triggerType="Schedule"
  | .scheduleTriggerConfig={cronExpression:"0 6 1,15 * *", parallelism:1, replicaCompletionCount:1}
  | del(.manualTriggerConfig))}}' > "$BODY"
jq . "$BODY"          # eyeball it: registries + replicaTimeout must still be there
az rest --method PATCH \
  --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.App/jobs/$JOB?api-version=2024-03-01" \
  --headers "Content-Type=application/json" --body @"$BODY"
```

Use a real temp file for `--body`, not process substitution — `--body @<(...)` fails with
`'u' is an invalid start of a value`.

The costs of this route: both runs share one execution history, the scope of a run is only
visible in Python rather than in the job spec, and `thomas-intel-job` is no longer a
free-form ad-hoc job (its args are now `--monthly-schedule`).

### Operational notes

- **A failed run resumes on the same UTC day.** Checkpointing is per-date, so restarting a
  scheduled job by hand on the 1st skips the accounts already done. Start it on the 2nd and
  it begins a fresh date and re-runs everything.
- **Gemini quota.** The two runs total ~542 account-runs a month. Ordinary 429s are
  retried with backoff (8 attempts, capped at 60s each), so a per-minute limit just slows
  the run down. A sustained quota wall — daily cap hit, or prepaid credits gone — aborts
  the run instead of skipping signals, because a skipped signal would checkpoint the
  account with empty data that a later re-run would never revisit.
  **The export still runs**: the CSV at `_export/market_intel_export_<DATE>.csv` covers
  every account that completed before the abort, so a partial month is still usable. The
  job exits non-zero and shows **Failed** in Azure. The cron won't retry before next
  month, so top up and start the job by hand **on the same UTC date** to resume from the
  checkpoint (a later date starts the whole vertical over).
- **The 15th intentionally re-runs Education & Research** against a new date, producing a
  second set of `results_<DATE>.json` files for those 61 accounts.

---

## Rollback

Re-point the job at the previous image tag (same env-safe update):

```bash
az containerapp job update -n thomas-intel-job -g marias_advisory_ai_rg \
  --image "thomasscientificintel.azurecr.io/ts-market-intel:20260529-142939"
```

List recent tags to pick a known-good one:

```powershell
az acr repository show-tags -n thomasscientificintel --repository ts-market-intel --orderby time_desc --top 10 -o table
```

---

## Gotchas (the expensive ones)

1. **`az acr build` needs registry Contributor/Owner, not AcrPush.** The action is
   `scheduleRun`. AcrPush only lets you *push* a pre-built image. Build on the Mac
   instead, or get the role / activate PIM.

2. **Cloud Shell has no Docker daemon.** You cannot `docker build` there. Only
   `az acr build` (cloud) works in Cloud Shell — and that needs the role above.

3. **Apple Silicon → always `--platform linux/amd64`.** Without it the image is arm64
   and the job crashes with an exec-format error.

4. **`--command ""` does NOT clear the command — it stores `[""]`**, and the container
   then tries to exec an empty string and fails. Always set the command explicitly:
   `--command "python" "main.py"`.

5. **Dash-prefixed args break the CLI.** `--args "--total-limit" "10"` fails with
   `unrecognized arguments`. Use the single-token equals form: `--args="--total-limit=10"`.
   `argparse` in `main.py` accepts `--flag=value` natively.

6. **Env-wipe via raw PATCH.** A raw `az rest PATCH` of
   `properties.template.containers[]` *replaces the whole container object* — if your
   body omits `env`, the vars are silently dropped and the next run fails with
   `No Gemini API key found`. Prefer `az containerapp job update` (it merges). If you
   must PATCH, send the **full** container spec including the complete `env` block.

7. **Managed Identity needs AcrPull to pull the image.** Already configured here (the
   job has pulled before). If the MI or registry is ever recreated, re-grant:
   ```bash
   ACR_ID=$(az acr show -n thomasscientificintel --query id -o tsv)
   az role assignment create --assignee 2abae8d1-838c-4d55-ac26-a8a0b439fcc1 --role AcrPull --scope "$ACR_ID"
   ```

8. **Checkpointing means re-runs are safe.** Completed accounts live in their
   `results_<DATE>.json` in Blob; a re-run skips them. If a run dies (e.g. Gemini
   `429 RESOURCE_EXHAUSTED`), top up credits and just start the job again.
