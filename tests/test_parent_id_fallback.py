"""Coverage for the no-ParentId → first-account-Id fallback in load_accounts_from_csv.

When a Corporate_ID__c has no ParentId on any of its rows, the loader falls back
to the account's own `Id` (smallest, for a deterministic "first") so the account
lands in the main SF export instead of the review CSV. A real ParentId always
wins over the fallback, and a CSV with no `Id` column degrades gracefully.
"""
from market_intel.accounts import load_accounts_from_csv


def _flatten(result):
    """{vertical: [{name, parent_id}, ...]} → {name: parent_id}."""
    return {row["name"]: row["parent_id"]
            for rows in result.values() for row in rows}


def test_no_parentid_falls_back_to_smallest_account_id(tmp_path):
    csv_path = tmp_path / "accounts.csv"
    # Rows deliberately out of Id order to prove the smallest Id is chosen.
    csv_path.write_text(
        "Corporate_ID__c,ParentId,segment_raw,tier,Id\n"
        "TEST NOPARENT MULTI,,PHARMA-BIOTECH,Customer80,001CCC\n"
        "TEST NOPARENT MULTI,,PHARMA-BIOTECH,Customer80,001AAA\n"
        "TEST NOPARENT MULTI,,PHARMA-BIOTECH,Customer80,001BBB\n",
        encoding="utf-8",
    )
    names = _flatten(load_accounts_from_csv(str(csv_path)))
    assert names["TEST NOPARENT MULTI"] == "001AAA"


def test_real_parentid_wins_over_account_id(tmp_path):
    csv_path = tmp_path / "accounts.csv"
    csv_path.write_text(
        "Corporate_ID__c,ParentId,segment_raw,tier,Id\n"
        "TEST HASPARENT,001PARENTZ,PHARMA-BIOTECH,Customer80,001AAA\n",
        encoding="utf-8",
    )
    names = _flatten(load_accounts_from_csv(str(csv_path)))
    assert names["TEST HASPARENT"] == "001PARENTZ"


def test_missing_id_column_degrades_gracefully(tmp_path):
    # No `Id` column at all: behavior is unchanged — parent_id stays None.
    csv_path = tmp_path / "accounts.csv"
    csv_path.write_text(
        "Corporate_ID__c,ParentId,segment_raw,tier\n"
        "TEST NOPARENT NOID,,PHARMA-BIOTECH,Customer80\n",
        encoding="utf-8",
    )
    names = _flatten(load_accounts_from_csv(str(csv_path)))
    assert names["TEST NOPARENT NOID"] is None
