"""
accounts_sql.py — Load the Customer80/Super80 account list from
`SalesForce.Account_base` in Azure SQL.

Returns the same `{vertical: [{"name", "parent_id"}, ...]}` shape as
`accounts.load_accounts_from_csv`, so the rest of the pipeline doesn't care
where accounts came from.

Auth: `DefaultAzureCredential` → AAD access token → pyodbc connection attr.
Works in Azure Container Apps via the bound user-assigned Managed Identity
(same identity already reads the Gemini key from Key Vault and writes to blob),
and locally via `az login`. For user-assigned MIs, `AZURE_CLIENT_ID` must be
set so DefaultAzureCredential knows which MI to use.

Hard-fail policy: any connection / auth / query failure raises
`SqlAccountsError` — the caller decides whether to bail or fall back. The
intended pipeline behavior (per the design discussion) is to abort the run
loudly so the integration gap is visible, not silently revert to a stale
hardcoded list.

Configuration (env vars, all required):
    AZURE_SQL_SERVER      — e.g. msql-datahub-server.database.windows.net
    AZURE_SQL_DATABASE    — e.g. msql_datahub
    AZURE_SQL_SCHEMA      — e.g. SalesForce         (default: SalesForce)
    AZURE_SQL_TABLE       — e.g. Account_base       (default: Account_base)

For user-assigned Managed Identity, also:
    AZURE_CLIENT_ID       — clientId of the bound MI
"""
from __future__ import annotations

import os
import struct
from functools import lru_cache
from collections import defaultdict

from accounts import _resolve_vertical


# Column mapping confirmed against `SalesForce.Account_base` schema:
#   Corporate_ID__c   → account name (canonical identifier)
#   X80_20__c         → tier filter (Customer80 / Super80)
#   Market_Segment__c → segment_raw value (used by SEGMENT_RAW_MAP)
#   ParentId          → SF parent record id (stamped onto results)
_COL_NAME      = "Corporate_ID__c"
_COL_TIER      = "X80_20__c"
_COL_SEGMENT   = "Market_Segment__c"
_COL_PARENT_ID = "ParentId"


class SqlAccountsError(RuntimeError):
    """Raised when SQL-backed account loading fails for any reason."""


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SqlAccountsError(
            f"Missing required env var {name}. Set it in the Container App Job env "
            f"or your local .env. See accounts_sql.py docstring for the full list."
        )
    return val


def _build_connection_string() -> str:
    server   = _require_env("AZURE_SQL_SERVER")
    database = _require_env("AZURE_SQL_DATABASE")
    return (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30"
    )


def _get_access_token() -> bytes:
    """Fetch an AAD access token for Azure SQL and pack it into pyodbc's
    SQL_COPT_SS_ACCESS_TOKEN attribute format (UTF-16-LE prefixed with a
    little-endian uint32 length)."""
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as e:
        raise SqlAccountsError(
            "azure-identity is required for SQL-backed account loading. "
            "It's already in requirements.txt for Key Vault auth — check imports."
        ) from e
    try:
        credential = DefaultAzureCredential()
        token = credential.get_token("https://database.windows.net/.default").token
    except Exception as e:
        raise SqlAccountsError(
            "Could not obtain an AAD token for Azure SQL. In Azure Container Apps "
            "this usually means AZURE_CLIENT_ID is missing or the bound MI lacks "
            "SQL DB read access. Locally, run `az login` first."
        ) from e
    token_bytes = token.encode("utf-16-le")
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


@lru_cache(maxsize=1)
def load_accounts_from_sql() -> dict[str, list[dict]]:
    """Load Customer80/Super80 accounts from `SalesForce.Account_base`.

    Returns a dict shaped like `accounts.load_accounts_from_csv`:
        { vertical: [{"name": <Corporate_ID__c>, "parent_id": <ParentId>}, ...] }

    Caches the result for the lifetime of the process — accounts don't change
    mid-run. Re-import or clear the cache for tests.
    """
    try:
        import pyodbc
    except ImportError as e:
        raise SqlAccountsError(
            "pyodbc is not installed. Add it to requirements.txt and rebuild "
            "the container — needs ODBC Driver 18 for SQL Server in the image."
        ) from e

    schema = os.environ.get("AZURE_SQL_SCHEMA", "SalesForce").strip() or "SalesForce"
    table  = os.environ.get("AZURE_SQL_TABLE",  "Account_base").strip() or "Account_base"
    fq_table = f"[{schema}].[{table}]"

    conn_str     = _build_connection_string()
    token_struct = _get_access_token()

    query = (
        f"SELECT [{_COL_NAME}], [{_COL_TIER}], [{_COL_SEGMENT}], [{_COL_PARENT_ID}] "
        f"FROM {fq_table} "
        f"WHERE [{_COL_TIER}] IN (N'Customer80', N'Super80')"
    )

    rows: list[tuple[str, str, str, str | None]] = []
    try:
        # SQL_COPT_SS_ACCESS_TOKEN — pyodbc connection attribute that takes the
        # packed AAD token. MSSQL-specific; documented at
        # https://learn.microsoft.com/en-us/sql/connect/odbc/using-azure-active-directory.
        SQL_COPT_SS_ACCESS_TOKEN = 1256
        with pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct}) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                for r in cur.fetchall():
                    name, tier, segment, parent_id = r
                    rows.append((
                        (name or "").strip(),
                        (tier or "").strip(),
                        (segment or "").strip(),
                        (parent_id or "").strip() or None,
                    ))
    except SqlAccountsError:
        raise
    except Exception as e:
        raise SqlAccountsError(
            f"Failed to query {fq_table} on {os.environ.get('AZURE_SQL_SERVER', '<unset>')}: {e}"
        ) from e

    # Deduplicate Corporate_ID__c the same way the CSV loader does:
    # within one Corporate_ID__c, the most-frequent segment wins.
    corp_data: dict[str, dict] = defaultdict(lambda: {"segments": defaultdict(int), "parent_id": None})
    for name, _tier, segment, parent_id in rows:
        if not name or name in ("0", "NULL"):
            continue
        corp_data[name]["segments"][segment.upper()] += 1
        if corp_data[name]["parent_id"] is None and parent_id and parent_id != "NULL":
            corp_data[name]["parent_id"] = parent_id

    result: dict[str, list[dict]] = {}
    skipped_segments: set[str] = set()
    for name, info in corp_data.items():
        primary_seg = max(info["segments"], key=lambda s: (info["segments"][s], s))
        vertical = _resolve_vertical(name, primary_seg)
        if vertical is None:
            skipped_segments.add(primary_seg)
            continue
        result.setdefault(vertical, []).append({"name": name, "parent_id": info["parent_id"]})

    if skipped_segments:
        print(
            f"WARNING: load_accounts_from_sql skipped accounts with unmapped "
            f"Market_Segment__c values: {sorted(skipped_segments)}. "
            f"Add them to SEGMENT_RAW_MAP in accounts.py to include them."
        )

    if not result:
        raise SqlAccountsError(
            f"Query returned 0 mappable accounts from {fq_table}. "
            f"Either the tier filter found nothing, or every account had an "
            f"unmapped Market_Segment__c. Investigate before continuing."
        )

    return result


if __name__ == "__main__":
    # Connectivity probe — useful for verifying the env vars + MI permissions
    # without firing the full pipeline.
    import json
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass
    data = load_accounts_from_sql()
    summary = {v: len(rows) for v, rows in data.items()}
    print(f"Loaded accounts from SQL ({sum(summary.values())} total):")
    print(json.dumps(summary, indent=2))
