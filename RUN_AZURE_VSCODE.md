# Running the Pipeline from VS Code (Azure)

Step-by-step, copy-paste guide to run the market-intel pipeline on Azure from the
**VS Code integrated terminal** — from logging into the Thomas Azure environment through
starting a run, watching logs, and stopping it.

This is the **operate-it** guide. To change the *code* that runs, see the deploy section
below (or [DEPLOYMENT.md](DEPLOYMENT.md) for the full version). To run the pipeline
**locally** on your Mac instead, see [RUN.md](RUN.md).

> ⚠️ **These run in your local VS Code terminal on your Mac — NOT Azure Cloud Shell.**
> The job-control and log commands (`az containerapp job ...`, `az monitor ...`) work
> from either place, but the **build/deploy** commands (`docker build` / `docker push`)
> need local Docker — Cloud Shell has no Docker daemon, and `az acr build` fails with
> this account's permissions. So do everything from the local terminal and you never
> have to switch contexts mid-deploy.

---

## 0. One-time prerequisites

You need these installed (check marks = run the command, expect a version, not an error):

```bash
az version          # Azure CLI
docker --version    # Docker Desktop — only needed if you'll BUILD a new image
git --version
```

- **Azure CLI** — https://learn.microsoft.com/cli/azure/install-azure-cli
- **Docker Desktop** — only required for the deploy path (building a new image). Make
  sure it's actually *running* (whale icon in the menu bar) before you build.

Open the terminal in VS Code with **`Ctrl+`` ` `` (backtick)** or **Terminal → New
Terminal**. Everything below is pasted there.

---

## 1. Log in to the Thomas Azure environment

```bash
az login
```

A browser opens — sign in as **matt.paris@thomassci.com**. Then pin the right
subscription so every later command targets it:

```bash
az account set --subscription d0fb2aac-3e96-49cc-8b7f-a84c8caf4973
az account show --query "{name:name, user:user.name}" -o table
```

Expect `Thomas Data Hub` and your email. `az` keeps multiple logins at once — this won't
disturb any other (e.g. DFS) subscription; switch back later with
`az account set --subscription <other-sub-id>`.

> These names/IDs are the live ones for this job:
> | Thing | Value |
> |---|---|
> | Subscription | `d0fb2aac-3e96-49cc-8b7f-a84c8caf4973` (Thomas Data Hub) |
> | Resource group | `marias_advisory_ai_rg` |
> | Job | `thomas-intel-job` |
> | Registry | `thomasscientificintel` (`.azurecr.io`) |
> | Image repo | `ts-market-intel` |

---

## 2. (Optional) See the current state before you touch anything

```bash
az containerapp job show -n thomas-intel-job -g marias_advisory_ai_rg \
  --query "properties.template.containers[0].{image:image, command:command, args:args, env:env[].{name:name,value:value}}" -o json
```

You want to confirm:
- `command` → `["python","main.py"]`
- `args` → `[]` for a full run, or `["--total-limit=10"]` if it's still set to a test cap
- `env` → all 6 wiring vars present (AZURE_CLIENT_ID, AZURE_KEY_VAULT_URL,
  AZURE_STORAGE_ACCOUNT_URL, AZURE_STORAGE_CONTAINER, AZURE_SQL_SERVER, AZURE_SQL_DATABASE)
  plus `SEMAPHORE_SIZE`. **If any of the 6 are missing, stop** — don't start a run.

---

## 3A. Just run it (no code change)

If the deployed image is already the code you want, just start a run:

```bash
az containerapp job start -n thomas-intel-job -g marias_advisory_ai_rg
```

### Want a small test run first?

Cap the number of accounts (the `=` form is required — see gotcha #2):

```bash
az containerapp job update -n thomas-intel-job -g marias_advisory_ai_rg --args="--total-limit=10"
az containerapp job start  -n thomas-intel-job -g marias_advisory_ai_rg
```

To go back to a **full run**, clear the cap first:

```bash
az containerapp job update -n thomas-intel-job -g marias_advisory_ai_rg --args=""
az containerapp job start  -n thomas-intel-job -g marias_advisory_ai_rg
```

---

## 3B. Deploy new code, then run

Do this when you've changed the Python and pushed to git. **Docker Desktop must be
running.**

```bash
# 1. Push your code
git push

# 2. Build + push the image (MUST be linux/amd64 — see gotcha #1)
az acr login -n thomasscientificintel
TAG=$(date -u +%Y%m%d%H%M%S)
docker build --platform linux/amd64 -t "thomasscientificintel.azurecr.io/ts-market-intel:$TAG" .
docker push "thomasscientificintel.azurecr.io/ts-market-intel:$TAG"
echo "NEW TAG: $TAG"

# 3. Point the job at the new image (keeps env + identity intact)
az containerapp job update -n thomas-intel-job -g marias_advisory_ai_rg \
  --image "thomasscientificintel.azurecr.io/ts-market-intel:$TAG"

# 4. Start
az containerapp job start -n thomas-intel-job -g marias_advisory_ai_rg
```

> If you closed the terminal between steps and `$TAG` is gone, list recent tags with:
> `az acr repository show-tags -n thomasscientificintel --repository ts-market-intel --orderby time_desc --top 5 -o table`
> and paste the literal tag in place of `$TAG`.

---

## 4. Find the running execution name

A `start` kicks off an *execution* with a random suffix (e.g. `thomas-intel-job-3yqpp0v`).
Get it:

```bash
az containerapp job execution list -n thomas-intel-job -g marias_advisory_ai_rg \
  --query "[0].{name:name, status:properties.status, start:properties.startTime}" -o json
```

Copy the `name` value — you'll need it to watch and to stop.

---

## 5. Watch the logs

**Live stream** (replace the execution name — **no `< >` brackets**, see gotcha #4):

```bash
az containerapp job logs show -n thomas-intel-job -g marias_advisory_ai_rg --container thomas-intel-job --execution thomas-intel-job-XXXXXXX --follow --tail 200
```

Healthy startup signs in the first ~30s:
- **no** `No Gemini API key found` → Key Vault fetch worked
- **no** `can't open file ...` → command is correct
- account-processing lines scrolling → it's running

**Search history with KQL** (e.g. to read the full Gemini rate-limit responses):

```bash
ENV_ID=$(az containerapp job show -n thomas-intel-job -g marias_advisory_ai_rg --query "properties.environmentId" -o tsv)
WS=$(az containerapp env show --ids "$ENV_ID" --query "properties.appLogsConfiguration.logAnalyticsConfiguration.customerId" -o tsv)

az monitor log-analytics query --workspace "$WS" \
  --analytics-query "ContainerAppConsoleLogs_CL | where Log_s has 'full Gemini response' | project TimeGenerated, Log_s | order by TimeGenerated desc | take 50" \
  -o table
```

If that returns nothing/errors on the table name, swap `ContainerAppConsoleLogs_CL` for
`ContainerAppConsoleLogs` (no `_CL`). Logs lag 1–3 minutes behind real time.

What the log lines mean:
- `⚠ Rate limit [...] full Gemini response: ...` → per-minute 429; **retried** automatically (slow, not broken)
- `✘ Gemini quota exhausted` → daily/billing wall; run **hard-stops** (only the client can lift it)
- `ERROR [...] after Xs: ...` → other error, full exception printed

---

## 6. Stop a run

```bash
az containerapp job stop -n thomas-intel-job -g marias_advisory_ai_rg \
  --job-execution-name thomas-intel-job-XXXXXXX
```

Stopping is safe — completed accounts are checkpointed in Blob, so the next start picks
up where it left off (it skips accounts already done that day).

---

## Gotchas (the ones that actually bite)

1. **Always `--platform linux/amd64` when building.** Apple Silicon builds arm64 by
   default; the job then crashes with an exec-format error.
2. **Dash-prefixed args break the CLI.** `--args "--total-limit" "10"` fails. Use the
   single-token equals form: `--args="--total-limit=10"`.
3. **`--command ""` does NOT clear the command** — it stores `[""]` and the container
   tries to exec an empty string. If you ever need to set it, set it explicitly:
   `--command "python" "main.py"`.
4. **No `< >` around the execution name.** In zsh, `<name>` is read as a file redirect
   (`no such file or directory`). Paste the bare name. Also keep `--container <name>`
   and its value on the **same line** — a trailing `\ ` (backslash-space) breaks it.
5. **`SEMAPHORE_SIZE` env var overrides the code.** Changing concurrency in Python alone
   does nothing in the container — update the env var:
   `az containerapp job update -n thomas-intel-job -g marias_advisory_ai_rg --set-env-vars SEMAPHORE_SIZE=4`.
6. **`az acr build` won't work with this account** (it lacks the registry `scheduleRun`
   permission). Build locally with Docker as in 3B — that's the supported path.
7. **Never raw-`PATCH` the container** — it drops the env vars (then runs fail with
   `No Gemini API key found`). Always use `az containerapp job update`, which merges.
