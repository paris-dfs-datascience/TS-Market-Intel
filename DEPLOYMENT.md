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
