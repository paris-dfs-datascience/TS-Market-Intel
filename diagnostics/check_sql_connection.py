"""
test_sql_connection.py -- Standalone Azure SQL connectivity diagnostic.

Eight ordered checks against `[SalesForce].[Account_base]` in the configured
Azure SQL database, using the same `DefaultAzureCredential` + pyodbc auth flow
as `accounts_sql.py`. Each step prints "OK" or a labelled failure hint, then
continues (so one run surfaces as much information as possible). Exit code is
`0` on full success, `1` if any step failed.

Usage:
    python test_sql_connection.py            # diagnostics only
    python test_sql_connection.py --sample   # also prints 3 sample rows

Required env vars:
    AZURE_SQL_SERVER, AZURE_SQL_DATABASE
Optional:
    AZURE_SQL_SCHEMA (default: SalesForce)
    AZURE_SQL_TABLE  (default: Account_base)
    AZURE_CLIENT_ID  (required for user-assigned MI; ignored locally)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone


# Columns the production pipeline reads from `Account_base`. Step 6 reports
# whether each of these exists on the table.
_PIPELINE_COLUMNS = [
    "Corporate_ID__c",
    "X80_20__c",
    "Market_Segment__c",
    "ParentId",
    "Id",
]


# -- Output helpers --------------------------------------------------------


def _header(n: int, title: str) -> None:
    print()
    print(f"-- Step {n}: {title} " + "-" * max(0, 60 - len(title)))


def _ok(msg: str) -> None:
    print(f"  OK   {msg}")


def _fail(msg: str, *hints: str) -> None:
    print(f"  FAIL {msg}")
    for hint in hints:
        print(f"       -> {hint}")


def _info(msg: str) -> None:
    print(f"       {msg}")


# -- Step implementations --------------------------------------------------


def step_env_vars() -> tuple[bool, dict]:
    """Step 1 -- required env vars present?"""
    _header(1, "env-var presence")
    server   = os.environ.get("AZURE_SQL_SERVER", "").strip()
    database = os.environ.get("AZURE_SQL_DATABASE", "").strip()
    schema   = os.environ.get("AZURE_SQL_SCHEMA", "SalesForce").strip() or "SalesForce"
    table    = os.environ.get("AZURE_SQL_TABLE",  "Account_base").strip() or "Account_base"
    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()

    missing = [k for k, v in [("AZURE_SQL_SERVER", server),
                              ("AZURE_SQL_DATABASE", database)] if not v]
    if missing:
        _fail(
            f"Missing required env var(s): {', '.join(missing)}",
            "Set them in the Container App Job env or your local .env.",
            "See accounts_sql.py docstring for the full list.",
        )
        return False, {}

    _ok(f"AZURE_SQL_SERVER   = {server}")
    _ok(f"AZURE_SQL_DATABASE = {database}")
    _ok(f"AZURE_SQL_SCHEMA   = {schema}")
    _ok(f"AZURE_SQL_TABLE    = {table}")
    if client_id:
        _ok(f"AZURE_CLIENT_ID    = {client_id}  (user-assigned MI selector)")
    else:
        _info("AZURE_CLIENT_ID    = (unset -- fine for system-assigned MI or local az login)")
    return True, {
        "server": server, "database": database,
        "schema": schema, "table": table,
        "client_id": client_id,
    }


def step_odbc_driver() -> bool:
    """Step 2 -- is ODBC Driver 18 installed?"""
    _header(2, "ODBC driver presence")
    try:
        import pyodbc
    except ImportError as e:
        _fail(
            f"pyodbc is not importable: {e}",
            "pip install 'pyodbc>=5.0' (already in requirements.txt).",
        )
        return False
    drivers = pyodbc.drivers()
    target = "ODBC Driver 18 for SQL Server"
    if target in drivers:
        _ok(f"{target} is installed")
        for d in drivers:
            _info(f"(all detected drivers): {d}")
        return True
    _fail(
        f"{target!r} not found among installed drivers",
        f"Detected drivers: {drivers!r}",
        "Debian: ACCEPT_EULA=Y apt-get install msodbcsql18 unixodbc",
        "Windows: download from https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server",
    )
    return False


def step_token() -> tuple[bool, object | None]:
    """Step 3 -- obtain an AAD access token for Azure SQL."""
    _header(3, "AAD token acquisition")
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError:
        _fail(
            "azure-identity is not importable",
            "It's already in requirements.txt for Key Vault auth -- check the install.",
        )
        return False, None
    try:
        credential = DefaultAzureCredential()
        token = credential.get_token("https://database.windows.net/.default")
    except Exception as e:
        _fail(
            f"DefaultAzureCredential.get_token() raised: {type(e).__name__}: {e}",
            "In Azure Container Apps: confirm AZURE_CLIENT_ID matches the bound MI.",
            "Locally: run `az login` first.",
            "Check that the MI has any role on the Azure SQL resource (RBAC layer, not data plane).",
        )
        return False, None
    exp_dt = datetime.fromtimestamp(token.expires_on, tz=timezone.utc)
    _ok(f"token acquired, expires_on = {token.expires_on} ({exp_dt.isoformat()})")
    return True, token.token


def step_connect(server: str, database: str, token_str: str) -> tuple[bool, object | None]:
    """Step 4 -- open a SQL connection with the AAD token."""
    _header(4, "SQL connection")
    import pyodbc
    import struct

    token_bytes = token_str.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    SQL_COPT_SS_ACCESS_TOKEN = 1256

    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30"
    )
    try:
        conn = pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})
    except pyodbc.Error as e:
        sqlstate = e.args[0] if e.args else "?????"
        msg = str(e.args[1]) if len(e.args) > 1 else str(e)
        _fail(f"pyodbc.connect failed: SQLSTATE={sqlstate}")
        for line in msg.splitlines():
            _info(line.strip())
        if "18456" in msg:
            print()
            _info("Hint -- 18456 'Login failed for user <token-identified principal>' means")
            _info("the token reached SQL but no matching database user exists. Verify with the DB admin:")
            _info("  1. The CREATE USER ran INSIDE the user database (e.g. `USE msql_datahub;`),")
            _info("     not in `master`. Confirm via `SELECT DB_NAME();` in their session.")
            _info("  2. The bracketed name matches the MI's display name EXACTLY. Compare against:")
            _info("       az identity show --ids $MI_RID --query name -o tsv")
            _info("  3. The role grant landed: `ALTER ROLE db_datareader ADD MEMBER [<name>];`")
        elif "firewall" in msg.lower():
            print()
            _info("Hint -- firewall may be blocking. Confirm 'Allow Azure services and resources")
            _info("to access this server' is ON in the SQL server's firewall settings, or that")
            _info("the Container Apps egress IPs are explicitly allow-listed.")
        return False, None
    except Exception as e:
        _fail(f"Unexpected connection error: {type(e).__name__}: {e}")
        return False, None
    _ok("connected")
    return True, conn


def step_identity_probe(conn) -> bool:
    """Step 5 -- what does SQL think of this session?"""
    _header(5, "identity probe")
    queries = [
        ("current database",        "SELECT DB_NAME()"),
        ("server-level principal",  "SELECT SUSER_NAME()"),
        ("database user",           "SELECT CURRENT_USER"),
        ("db_datareader member?",   "SELECT IS_ROLEMEMBER('db_datareader')"),
    ]
    all_ok = True
    for label, sql in queries:
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                value = row[0] if row else None
        except Exception as e:
            _fail(f"{label}: query raised {type(e).__name__}: {e}")
            all_ok = False
            continue
        if label == "db_datareader member?" and value != 1:
            _fail(
                f"{label}: returned {value!r} (expected 1)",
                "User exists but db_datareader role wasn't added.",
                "Admin: `ALTER ROLE db_datareader ADD MEMBER [<name>];` in the user database.",
            )
            all_ok = False
        else:
            _ok(f"{label}: {value!r}")
    return all_ok


def step_schema_probe(conn, schema: str, table: str) -> bool:
    """Step 6 -- do the columns the pipeline reads exist on the table?"""
    _header(6, "schema probe")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
                schema, table,
            )
            cols = {name: dtype for name, dtype in cur.fetchall()}
    except Exception as e:
        _fail(f"INFORMATION_SCHEMA.COLUMNS query raised {type(e).__name__}: {e}")
        return False
    if not cols:
        _fail(
            f"Table [{schema}].[{table}] not found (or no SELECT permission on INFORMATION_SCHEMA)",
            "Verify schema/table names in AZURE_SQL_SCHEMA / AZURE_SQL_TABLE.",
        )
        return False
    _ok(f"table found, {len(cols)} columns total")
    all_present = True
    for col in _PIPELINE_COLUMNS:
        if col in cols:
            _info(f"  [+] {col}  ({cols[col]})")
        else:
            _info(f"  [-] {col}  MISSING")
            all_present = False
    if not all_present:
        _fail(
            "One or more pipeline columns are missing on the table",
            "Update accounts_sql.py's column constants if SF renamed them, or",
            "confirm AZURE_SQL_SCHEMA / AZURE_SQL_TABLE point at the right object.",
        )
    return all_present


def step_row_count(conn, schema: str, table: str) -> tuple[bool, int]:
    """Step 7 -- how many Customer80/Super80 rows match the pipeline filter?"""
    _header(7, "filtered row-count probe")
    sql = (
        f"SELECT COUNT(*) FROM [{schema}].[{table}] "
        f"WHERE [X80_20__c] IN (N'Customer80', N'Super80')"
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            n = cur.fetchone()[0]
    except Exception as e:
        _fail(f"COUNT(*) raised {type(e).__name__}: {e}")
        return False, 0
    if n == 0:
        _fail(
            f"COUNT = 0 -- pipeline would have nothing to process",
            "Either no rows are tier-tagged Customer80/Super80 today,",
            "or the column name X80_20__c is wrong (check step 6 results).",
        )
        return False, 0
    _ok(f"COUNT(*) where X80_20__c IN ('Customer80','Super80') = {n}")
    return True, n


def step_sample_rows(conn, schema: str, table: str) -> bool:
    """Step 8 (opt-in) -- print 3 sample rows so a human can eyeball them."""
    _header(8, "sample rows (opt-in via --sample)")
    sql = (
        f"SELECT TOP 3 [Corporate_ID__c], [X80_20__c], [Market_Segment__c], [ParentId] "
        f"FROM [{schema}].[{table}] "
        f"WHERE [X80_20__c] IN (N'Customer80', N'Super80')"
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    except Exception as e:
        _fail(f"sample query raised {type(e).__name__}: {e}")
        return False
    if not rows:
        _fail("sample query returned 0 rows")
        return False
    _ok(f"{len(rows)} rows returned:")
    for row in rows:
        name, tier, segment, parent_id = row
        _info(f"  {name!r:40}  tier={tier!r:14}  segment={segment!r:30}  parent_id={parent_id!r}")
    return True


# -- Driver ----------------------------------------------------------------


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--sample", action="store_true",
                        help="Also print 3 sample rows (off by default)")
    args = parser.parse_args()

    all_pass = True

    ok, env = step_env_vars()
    if not ok:
        return 1

    if not step_odbc_driver():
        return 1

    ok, token_str = step_token()
    if not ok:
        return 1

    ok, conn = step_connect(env["server"], env["database"], token_str)
    if not ok:
        return 1

    try:
        all_pass &= step_identity_probe(conn)
        all_pass &= step_schema_probe(conn, env["schema"], env["table"])
        row_ok, _row_count = step_row_count(conn, env["schema"], env["table"])
        all_pass &= row_ok
        if args.sample:
            all_pass &= step_sample_rows(conn, env["schema"], env["table"])
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print()
    if all_pass:
        print("ALL STEPS OK -- the MI can read Account_base. Safe to run --from-sql.")
        return 0
    print("ONE OR MORE STEPS FAILED -- see hints above; share this output with the DB admin.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
