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
| `neha.mazumdar@thomassci.com` | **GitHub Codespace only** — no local Azure CLI, no Docker Desktop | "Deploying — from a GitHub Codespace" under [Scheduled monthly runs](#scheduled-monthly-runs). Install `az` first (not in the Codespaces image), then `az login --use-device-code`; Codespaces is amd64 so no `--platform` flag |

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

> This is also how the scheduled monthly runs pick up new code — they run on this same job,
> so a `job update --image` is all it takes. It does not disturb `args` or the cron. See
> "Scheduled monthly runs" below.

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

The pipeline runs itself on the **1st and 15th of every month at 06:00 UTC** (02:00 EDT /
01:00 EST).

| Run | Verticals | Accounts |
|---|---|---|
| 1st | BioPharma, CDMO / CRO, Education & Research, Hospital & Health Systems, Industrial | 399 |
| 15th | Education & Research, Clinical / Molecular Diagnostics, Government | 143 |

Between them every vertical is covered, and Education & Research is the only one run twice
(by design). `tests/test_scheduled_categories.py` fails if either stops being true.

### How it works

There is **one** job — `thomas-intel-job` — Schedule-triggered on cron `0 6 1,15 * *` with
`args: ["--monthly-schedule"]`.

A Container Apps job holds only one cron **and** one fixed arg list, so the choice of
verticals can't come from the args. `--monthly-schedule` reads `MONTHLY_SCHEDULE` in
`main.py` and picks the set from the UTC day-of-month:

| UTC day | Resolves to |
|---|---|
| 1 | `biopharma,cdmo_cro,education,hospital,industrial` |
| 15 | `education,clinical_dx,government` |
| anything else | logs why and exits 0 without running |

That last row matters: the cron only fires on the 1st and 15th, so the other-day branch is
just a safety net — but it also means a **manual** `job start` on any other day does
nothing. See "Running something ad-hoc" below.

`--monthly-schedule` resolves to the same list `--categories` takes, which runs the verticals
in order and then finalizes exactly like `--category all` — URL repair, then the SF export
CSV at `_export/market_intel_export_<DATE>.csv` for that run's UTC date, in the same
`adlsacctmarias` / `market-intel-output` container, with the Gemini key from the same Key
Vault. Nothing about the output location or format changes.

### Permissions

Setting the schedule only *updates* an existing job, so all it needs is
`Microsoft.App/jobs/write` on the resource group — **confirmed present** for
`neha.mazumdar@thomassci.com` on 2026-07-30, and already exercised by the `job update
--image` deploys. Check with:

```bash
az rest --method GET --url "https://management.azure.com/subscriptions/d0fb2aac-3e96-49cc-8b7f-a84c8caf4973/resourceGroups/marias_advisory_ai_rg/providers/Microsoft.Authorization/permissions?api-version=2022-04-01" --query "value[].actions[]" -o tsv | grep -i '^microsoft.app'
```

**AcrPush is irrelevant here** — that role only governs pushing images to the registry.

### Deploying — from a GitHub Codespace

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

**2. Deploy the code first — this is not optional.** `--monthly-schedule` is new code. Set
the schedule on an image that predates it and the run dies on the 1st with
`unrecognized arguments: --monthly-schedule`. Verify any image before trusting it — the
entrypoint is `python main.py`, so this costs nothing:

```bash
docker run --rm thomasscientificintel.azurecr.io/ts-market-intel:<TAG> --help | grep -E "categories|monthly-schedule"
```

Build and push from the Codespace (`az acr build` fails for this account — it lacks the
registry `listBuildSourceUploadUrl` action; `docker build` + `docker push` is the working
path, and the Codespace is already amd64 so no `--platform` flag is needed).

`az acr login` does **not** work from this Codespace even though push rights are fine — see
the troubleshooting block below. Authenticate docker with an Azure AD token directly
instead; the all-zeros GUID is the well-known "this is a token, not a username" value, and
the token is good for ~3 hours:

```bash
git pull                                          # make sure the Codespace has the new code

TENANT=$(az account show --query tenantId -o tsv)
AAD=$(az account get-access-token --query accessToken -o tsv)
REFRESH=$(curl -s -X POST "https://thomasscientificintel.azurecr.io/oauth2/exchange" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=access_token&service=thomasscientificintel.azurecr.io&tenant=$TENANT&access_token=$AAD" \
  | jq -r .refresh_token)
docker login thomasscientificintel.azurecr.io -u 00000000-0000-0000-0000-000000000000 -p "$REFRESH"

TAG=$(date -u +%Y%m%d%H%M%S)
docker build -t "thomasscientificintel.azurecr.io/ts-market-intel:$TAG" -f Dockerfile .
docker push "thomasscientificintel.azurecr.io/ts-market-intel:$TAG"
az containerapp job update -n thomas-intel-job -g marias_advisory_ai_rg --image "thomasscientificintel.azurecr.io/ts-market-intel:$TAG"
echo "NEW TAG: $TAG"
```

> **`argument --image: expected one argument`** means `--image` got no value: either `$TAG`
> is empty because you're in a different shell than the one that set it, or a `\`
> line-continuation didn't survive the paste (see RUN_AZURE_VSCODE.md gotcha #4 — this bites
> repeatedly). The `az` lines above are deliberately kept on one line each for that reason.
> Recover the tag from the registry and pass it literally:
>
> ```bash
> echo "TAG=[$TAG]"        # empty? the variable is gone
> az acr repository show-tags -n thomasscientificintel --repository ts-market-intel --orderby time_desc --top 5 -o tsv
> az containerapp job update -n thomas-intel-job -g marias_advisory_ai_rg --image thomasscientificintel.azurecr.io/ts-market-intel:<TAG>
> ```

#### If `az acr login` says "Failed to retrieve credentials"

```
No credential was provided to access Azure Container Registry. Trying to look up credentials...
Failed to retrieve credentials for container registry thomasscientificintel.
Please provide the registry username and password
```

That message is a **red herring**. `az acr login` does three things in order — resolve the
registry over ARM, exchange your Azure AD token at the registry's `/oauth2/exchange`
endpoint, then hand the result to `docker login`. When the *exchange* fails it silently
falls back to admin username/password, and only the fallback's failure is printed.

The registry does have `adminUserEnabled: true`, but *reading* the admin password needs
`Microsoft.ContainerRegistry/registries/listCredentials/action` (ACR Contributor / Owner),
which this account doesn't hold — so seeing this message at all means the fallback already
failed too. The real failure is the step before it. Diagnose in this order:

```bash
# 1. Logged in, and pointed at the right subscription? A fresh `az login` in a new
#    Codespace defaults to whatever subscription comes first — often the wrong one.
az account show --query "{sub:name, subId:id, tenant:tenantId, user:user.name}" -o json
az account set --subscription d0fb2aac-3e96-49cc-8b7f-a84c8caf4973

# 2. Can you read the registry over ARM at all? This is the usual culprit.
az acr show -n thomasscientificintel \
  --query "{name:name, loginServer:loginServer, adminEnabled:adminUserEnabled}" -o json

# 3. Is docker up? az acr login shells out to `docker login`.
docker info >/dev/null && echo "docker OK"
```

**If step 2 fails with `AuthorizationFailed`**, that's the root cause: `AcrPush` grants only
the data-plane actions (`registries/pull/read`, `registries/push/write`) — it does **not**
include `Microsoft.ContainerRegistry/registries/read`. Ask for **`Reader` on the registry**
alongside AcrPush (management-plane read only, adds no push rights).

**If step 2 succeeds** (confirmed for `neha.mazumdar@thomassci.com` on 2026-07-30, along
with the right subscription and a healthy docker), the ARM side is fine and the token
exchange is what's failing. Test it directly — this is the one command that returns a real
HTTP status instead of a swallowed one:

```bash
TENANT=$(az account show --query tenantId -o tsv)
AAD=$(az account get-access-token --query accessToken -o tsv)
curl -s -i -X POST "https://thomasscientificintel.azurecr.io/oauth2/exchange" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=access_token&service=thomasscientificintel.azurecr.io&tenant=$TENANT&access_token=$AAD" \
  | head -20
```

Read the response body, not just the status: on success the token's own payload lists your
effective rights, which is more direct than a role listing. Decode the middle JWT segment
and look at `permissions.actions` — `write` is what a push needs:

```bash
echo '<refresh_token>' | cut -d. -f2 | base64 -d 2>/dev/null | jq '{sub, aud, permissions}'
```

| Exchange result | Meaning | Fix |
|---|---|---|
| `200`, `permissions.actions` includes `write` | Token auth and push rights are fine; the `docker login` handoff inside `az acr login` is what broke | Authenticate docker with the token yourself (below) |
| `200` but no `write` action | Read-only at the registry (AcrPull, not AcrPush) | Role request — AcrPush at the registry scope |
| `401` | Assignment missing, scoped elsewhere, or **PIM-eligible and not activated** | Activate in PIM if eligible, else request AcrPush |

> **Observed 2026-07-30** for `neha.mazumdar@thomassci.com`: exchange returned `200` with
> `actions: [read, write, metadata/read, deleted/read]` while `az acr login` still failed —
> i.e. permissions were never the problem. The manual `docker login` below is the working
> path. If you want `az acr login` itself fixed, check `~/.docker/config.json` for a
> `"credsStore"` entry naming a helper that doesn't exist in the Codespace (e.g. `desktop`,
> left over from a Docker Desktop config); `az` shells out to `docker login`, that fails,
> and `az` misreports it as a registry-credential problem. Removing the `credsStore` line
> makes docker store credentials in the file instead.

To list role assignments instead, note `--scope` and `--all` are mutually exclusive
(`--all` errors with "group or scope are not required"):

```bash
ACR_ID=$(az acr show -n thomasscientificintel --query id -o tsv)
az role assignment list --scope "$ACR_ID" --include-inherited \
  --assignee neha.mazumdar@thomassci.com \
  --query "[].{role:roleDefinitionName, scope:scope}" -o table
```

To use a working token directly, bypassing `az acr login` (the all-zeros GUID is the
well-known "this is a token, not a username" value):

```bash
REFRESH=$(curl -s -X POST "https://thomasscientificintel.azurecr.io/oauth2/exchange" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=access_token&service=thomasscientificintel.azurecr.io&tenant=$TENANT&access_token=$AAD" \
  | jq -r .refresh_token)
docker login thomasscientificintel.azurecr.io \
  -u 00000000-0000-0000-0000-000000000000 -p "$REFRESH"
```

Then run the `docker build` / `docker push` from step 2 unchanged.

**Last resort, since `adminUserEnabled` is true:** someone holding ACR Contributor or Owner
can run `az acr credential show -n thomasscientificintel` and pass you the admin password
out of band, after which `docker login thomasscientificintel.azurecr.io -u thomasscientificintel -p <password>`
works. That is a shared static registry credential — prefer fixing the role assignment, and
don't paste it into the repo, a chat, or the job's env.

**3. Set the args, then the schedule.**

Back up the job spec first — there is no git here and the PATCH below is the one step that
can drop fields:

```bash
JOB=thomas-intel-job; RG=marias_advisory_ai_rg; SUB=d0fb2aac-3e96-49cc-8b7f-a84c8caf4973
az containerapp job show -n $JOB -g $RG -o json > ~/job-backup.json
```

Args first. `job update` is a GET-merge-PUT, so env and identity survive it:

```bash
az containerapp job update -n $JOB -g $RG --args="--monthly-schedule"
```

`args` must stay a **single** token (gotcha #5). Then flip the trigger — `az containerapp
job update` cannot change `triggerType`, so this needs a raw PATCH:

```bash
BODY=$(mktemp /tmp/jobpatch.XXXXXX.json)
cat > "$BODY" <<'EOF'
{"properties":{"configuration":{"triggerType":"Schedule","scheduleTriggerConfig":{"cronExpression":"0 6 1,15 * *","parallelism":1,"replicaCompletionCount":1}}}}
EOF
jq . "$BODY"
az rest --method PATCH --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.App/jobs/$JOB?api-version=2024-03-01" --headers "Content-Type=application/json" --body @"$BODY"
rm -f "$BODY"
```

Use a real temp file for `--body`, not process substitution — `--body @<(...)` fails with
`'u' is an invalid start of a value`.

**4. Verify. Do not skip this** — per gotcha #6 a PATCH can replace a nested object wholesale
rather than merging, and the field that hurts most is `replicaTimeout`:

```bash
az containerapp job show -n $JOB -g $RG --query "{trigger:properties.configuration.triggerType, cron:properties.configuration.scheduleTriggerConfig.cronExpression, timeoutSec:properties.configuration.replicaTimeout, retry:properties.configuration.replicaRetryLimit, registries:properties.configuration.registries, identity:keys(identity.userAssignedIdentities), image:properties.template.containers[0].image, args:properties.template.containers[0].args, env:properties.template.containers[0].env[].name}" -o json
```

| Field | Must be |
|---|---|
| `trigger` | `Schedule` |
| `cron` | `0 6 1,15 * *` |
| `timeoutSec` | **`43200`** (12h). If it reads `1800`, the PATCH clobbered it and the 399-account run will be killed at 30 minutes |
| `args` | exactly `["--monthly-schedule"]` |
| `identity` | the `thomas-intel-identity` resource id |
| `env` | all 6 `AZURE_*` names |

Repair a clobbered timeout without another PATCH:

```bash
az containerapp job update -n $JOB -g $RG --replica-timeout 43200 --replica-retry-limit 1
```

If `env` or `identity` went missing, restore from `~/job-backup.json` before the 1st.

### Proving the cron actually fires

Everything above verifies *configuration*. It does not prove Container Apps will trigger the
job — and the next natural chance to find out is 06:00 UTC on the 1st. You cannot force a
scheduled trigger, but you can move the schedule to a minute that is about to happen.

Cost is ~2 accounts of Gemini spend and about five minutes.

```bash
# 1. Baseline — note the newest execution name, or that there are none
az containerapp job execution list -n $JOB -g $RG --query "[].{name:name,start:properties.startTime}" -o table | head -5

# 2. Make the run cheap: 2 accounts instead of 399
az containerapp job update -n $JOB -g $RG --args="--total-limit=2"

# 3. What time is it in UTC? Pick a minute 5-10 ahead.
date -u
```

Now set the cron to that minute, **pinned to today's date** — `M H D Mo *`. Using
`35 14 30 7 *` (14:35 on 30 July only) instead of `35 14 * * *` means that if you forget to
revert, it cannot fire again tomorrow:

```bash
BODY=$(mktemp /tmp/jobpatch.XXXXXX.json)
cat > "$BODY" <<'EOF'
{"properties":{"configuration":{"triggerType":"Schedule","scheduleTriggerConfig":{"cronExpression":"35 14 30 7 *","parallelism":1,"replicaCompletionCount":1}}}}
EOF
az rest --method PATCH --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.App/jobs/$JOB?api-version=2024-03-01" --headers "Content-Type=application/json" --body @"$BODY"
rm -f "$BODY"
```

Wait past that minute — allow a minute or two of scheduler lag — then compare against your
baseline. **Don't start anything by hand in the meantime**, or you can't tell which trigger
produced the execution:

```bash
az containerapp job execution list -n $JOB -g $RG --query "[].{name:name,status:properties.status,start:properties.startTime}" -o table | head -5
```

A new execution whose `startTime` matches your target minute is the proof. Read its log to
confirm it really ran:

```bash
az containerapp job logs show -n $JOB -g $RG --container thomas-intel-job --execution <NEW_EXECUTION_NAME> --tail 100
```

Nothing new after ~3 minutes means the schedule isn't live. Re-check `trigger` is `Schedule`
(a Manual job silently ignores `scheduleTriggerConfig`), and that the cron is **5 fields, in
UTC** — Container Apps rejects 6-field expressions with seconds, and there is no local-time
option.

**Then restore both**, and re-run the step-4 verification:

```bash
az containerapp job update -n $JOB -g $RG --args="--monthly-schedule"
# PATCH the cron back to "0 6 1,15 * *" using the step-3 block
```

Leaving `--total-limit=2` in place means the 1st runs two accounts and calls it a month —
so confirm `args` reads `["--monthly-schedule"]` before you walk away. Note this test also
overwrites `_export/market_intel_export_<TODAY>.csv`, so don't run it on a day whose export
matters.

### Did a real run fire?

```bash
az containerapp job execution list -n thomas-intel-job -g marias_advisory_ai_rg --query "[0].{status:properties.status, start:properties.startTime, end:properties.endTime}" -o json
```

Logs are per-execution, same as a manual run (see RUN_AZURE_VSCODE.md §5). Container Apps
does not notify on failure — if you want to know without checking, add an alert on the job's
`Failed` executions.

### Running something ad-hoc

The job's args are `--monthly-schedule`, so a manual start on any day other than the 1st or
15th logs why and exits 0 without running. To run something by hand, swap the args and swap
them back:

```bash
az containerapp job update -n thomas-intel-job -g marias_advisory_ai_rg --args="--total-limit=10"
az containerapp job start -n thomas-intel-job -g marias_advisory_ai_rg
# ... when it's done:
az containerapp job update -n thomas-intel-job -g marias_advisory_ai_rg --args="--monthly-schedule"
az containerapp job show -n thomas-intel-job -g marias_advisory_ai_rg --query "properties.template.containers[0].args" -o json
```

**Forgetting that last restore breaks the next scheduled run**, so always confirm the args
before you finish.

### Changing the scope or the schedule

The vertical sets live in `MONTHLY_SCHEDULE` in `main.py`, so changing scope is a **code**
change plus a rebuild and redeploy — not an `az` command.
`tests/test_scheduled_categories.py` pins that every vertical in `ACCOUNTS` is covered by
one of the two dates, so it will fail if a newly added vertical would be silently skipped.

Changing the *time* or *dates* is just the cron, via the step-3 PATCH. Keep the days in the
cron consistent with the keys in `MONTHLY_SCHEDULE` — a cron that fires on a day the dict
doesn't know about produces a run that exits 0 doing nothing.

### Operational notes

- **A failed run resumes on the same UTC day** — but only via the ad-hoc recipe above, since
  `--monthly-schedule` no-ops on the 2nd. On the 1st or 15th a plain `job start` resumes
  correctly: checkpointing is per-date, so it skips the accounts already done. Start it on any
  later date and it begins a fresh date and re-runs everything from scratch.
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
