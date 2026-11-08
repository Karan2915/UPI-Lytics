"""
UPIlytics - Step 2: Create the MySQL star schema and load the cleaned data.

What this script does:
    1. Runs sql/01_create_schema.sql (creates database `upilytics` and all
       tables; any existing UPIlytics tables are dropped and recreated).
    2. Builds the dimension tables (date, bank, app) from the cleaned CSVs.
    3. Loads the four fact tables.
    4. Prints row counts and integrity checks.

Before running:
    - MySQL Server must be running.
    - Run scripts/01_clean_data.py first (it creates data/clean/*.csv).
    - Create a file named .env in the project root (same level as scripts/)
      containing your MySQL login:

          MYSQL_HOST=localhost
          MYSQL_PORT=3306
          MYSQL_USER=root
          MYSQL_PASSWORD=your_password_here

      .env must be listed in .gitignore so the password is never pushed.

Install the required packages once:
    pip install pandas numpy sqlalchemy pymysql cryptography python-dotenv

Run:
    python scripts/02_load_to_mysql.py
"""

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import URL
except ImportError as exc:  # pragma: no cover
    sys.exit(
        f"Missing package: {exc.name}\n"
        "Install everything with:\n"
        "  pip install pandas numpy sqlalchemy pymysql cryptography python-dotenv"
    )

# --------------------------------------------------------------------------
# Paths and settings
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
CLEAN_DIR = ROOT / "Data" / "clean" if (ROOT / "Data").exists() else ROOT / "data" / "clean"
SCHEMA_FILE = ROOT / "sql" / "01_create_schema.sql"
DATABASE = "upilytics"

CLEAN_FILES = {
    "national": CLEAN_DIR / "fact_national_monthly.csv",
    "remitter": CLEAN_DIR / "fact_remitter_monthly.csv",
    "beneficiary": CLEAN_DIR / "fact_beneficiary_monthly.csv",
    "apps": CLEAN_DIR / "fact_app_monthly.csv",
}

CHUNK = 500


# --------------------------------------------------------------------------
# Connection helpers
# --------------------------------------------------------------------------
def get_credentials():
    load_dotenv(ROOT / ".env")
    password = os.getenv("MYSQL_PASSWORD")
    if password is None:
        sys.exit(
            "MYSQL_PASSWORD not found.\n"
            f"Create {ROOT / '.env'} with MYSQL_HOST, MYSQL_PORT, "
            "MYSQL_USER and MYSQL_PASSWORD (see the top of this script)."
        )
    return {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "username": os.getenv("MYSQL_USER", "root"),
        "password": password,
    }


def make_url(creds, database=None):
    return URL.create(
        "mysql+pymysql",
        username=creds["username"],
        password=creds["password"],
        host=creds["host"],
        port=creds["port"],
        database=database,
        query={"charset": "utf8mb4"},
    )


def run_schema(creds):
    """Execute every statement in sql/01_create_schema.sql."""
    if not SCHEMA_FILE.exists():
        sys.exit(f"Schema file not found: {SCHEMA_FILE}")

    # Remove full-line comments, then split into statements on ';'.
    lines = [
        line for line in SCHEMA_FILE.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    ]
    statements = [s.strip() for s in "\n".join(lines).split(";") if s.strip()]

    engine = create_engine(make_url(creds))
    with engine.connect() as conn:
        for stmt in statements:
            conn.exec_driver_sql(stmt)
        conn.commit()
    engine.dispose()
    print(f"Schema created ({len(statements)} statements executed).")


# --------------------------------------------------------------------------
# Rule-based categories for the dimension tables
# --------------------------------------------------------------------------
def classify_bank(name):
    n = name.lower()
    if "payments bank" in n:
        return "Payments Bank"
    if "small finance bank" in n or name == "Equitas Bank":
        return "Small Finance Bank"
    if any(k in n for k in ("gramin", "grameena", "grameen")) or name == "Baroda U.P. Bank":
        return "Regional Rural Bank"
    if "cosmos" in n or "saraswat" in n:
        return "Co-operative Bank"
    if name in ("Tri O Tech Solutions Private Limited",
                "One Mobikwik Systems Limited"):
        return "Non-bank PPI"
    return "Commercial Bank"


def classify_app(name):
    n = name.lower()
    if name == "BHIM":
        return "NPCI app"
    if name == "Others":
        return "Others"
    if n.endswith(" app") or n.endswith(" apps") or "bank" in n:
        return "Bank app"
    return "Third-party app"


def fiscal_labels(year, month):
    """Indian fiscal year runs April to March."""
    start = year if month >= 4 else year - 1
    label = f"FY{start}-{str(start + 1)[-2:]}"
    fq = ((month - 4) % 12) // 3 + 1
    return label, fq


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def read_clean(key):
    path = CLEAN_FILES[key]
    if not path.exists():
        sys.exit(f"Missing {path}\nRun scripts/01_clean_data.py first.")
    df = pd.read_csv(path)
    df["period"] = pd.to_datetime(df["period"]).dt.date
    return df


def insert(df, table, engine):
    df.to_sql(table, engine, if_exists="append", index=False,
              method="multi", chunksize=CHUNK)
    print(f"  loaded {table}: {len(df)} rows")


def build_dim_date(frames):
    periods = sorted(set().union(*[set(f["period"]) for f in frames]))
    start, end = min(periods), max(periods)
    months = pd.date_range(start, end, freq="MS")
    rows = []
    for ts in months:
        fy, fq = fiscal_labels(ts.year, ts.month)
        rows.append({
            "date_key": ts.date(),
            "year": ts.year,
            "month": ts.month,
            "month_name": ts.strftime("%B"),
            "year_month_label": ts.strftime("%Y-%m"),
            "quarter": (ts.month - 1) // 3 + 1,
            "fiscal_year": fy,
            "fiscal_quarter": fq,
        })
    return pd.DataFrame(rows)


def main():
    creds = get_credentials()
    run_schema(creds)

    national = read_clean("national")
    remitter = read_clean("remitter")
    beneficiary = read_clean("beneficiary")
    apps = read_clean("apps")

    engine = create_engine(make_url(creds, DATABASE))

    print("Loading dimensions...")
    dim_date = build_dim_date([national, remitter, beneficiary, apps])
    insert(dim_date, "dim_date", engine)

    bank_names = sorted(set(remitter["bank_name"]) | set(beneficiary["bank_name"]))
    dim_bank = pd.DataFrame({
        "bank_name": bank_names,
        "bank_category": [classify_bank(n) for n in bank_names],
    })
    insert(dim_bank, "dim_bank", engine)

    app_names = sorted(set(apps["app_name"]))
    dim_app = pd.DataFrame({
        "app_name": app_names,
        "app_category": [classify_app(n) for n in app_names],
    })
    insert(dim_app, "dim_app", engine)

    bank_ids = pd.read_sql("SELECT bank_id, bank_name FROM dim_bank", engine)
    app_ids = pd.read_sql("SELECT app_id, app_name FROM dim_app", engine)

    print("Loading facts...")
    fact_national = national.rename(columns={"period": "date_key"})[
        ["date_key", "banks_live_on_upi", "volume_mn", "value_cr",
         "avg_ticket_size_rs", "volume_mom_growth_pct", "volume_yoy_growth_pct"]
    ]
    insert(fact_national, "fact_national_monthly", engine)

    remitter_cols = [
        "date_key", "bank_id", "total_volume_mn", "approved_pct", "bd_pct",
        "td_pct", "debit_reversal_count_mn", "debit_reversal_success_pct",
        "approved_volume_mn", "bd_volume_mn", "td_volume_mn",
        "volume_rank_in_month",
    ]
    fact_remitter = (
        remitter.merge(bank_ids, on="bank_name", how="left")
        .rename(columns={"period": "date_key"})[remitter_cols]
    )
    insert(fact_remitter, "fact_remitter_performance", engine)

    beneficiary_cols = [
        "date_key", "bank_id", "total_volume_mn", "approved_pct", "bd_pct",
        "td_pct", "deemed_approved_pct", "approved_volume_mn", "bd_volume_mn",
        "td_volume_mn", "volume_rank_in_month",
    ]
    fact_beneficiary = (
        beneficiary.merge(bank_ids, on="bank_name", how="left")
        .rename(columns={"period": "date_key"})[beneficiary_cols]
    )
    insert(fact_beneficiary, "fact_beneficiary_performance", engine)

    app_cols = [
        "date_key", "app_id", "total_volume_mn", "total_value_cr",
        "volume_customer_mn", "value_customer_cr", "volume_b2c_mn",
        "value_b2c_cr", "volume_b2b_mn", "value_b2b_cr", "volume_share_pct",
        "value_share_pct",
    ]
    fact_apps = (
        apps.merge(app_ids, on="app_name", how="left")
        .rename(columns={"period": "date_key"})[app_cols]
    )
    insert(fact_apps, "fact_app_monthly", engine)

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------
    print("\nRow counts in MySQL:")
    expected = {
        "dim_date": len(dim_date),
        "dim_bank": len(dim_bank),
        "dim_app": len(dim_app),
        "fact_national_monthly": len(fact_national),
        "fact_remitter_performance": len(fact_remitter),
        "fact_beneficiary_performance": len(fact_beneficiary),
        "fact_app_monthly": len(fact_apps),
    }
    all_ok = True
    with engine.connect() as conn:
        for table, exp in expected.items():
            got = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            flag = "OK" if got == exp else "MISMATCH"
            all_ok &= got == exp
            print(f"  {table:<30} {got:>6}  (expected {exp})  {flag}")

        print("\nCategory breakdown (check the rule-based labels):")
        cats = pd.read_sql(
            "SELECT bank_category, COUNT(*) AS banks FROM dim_bank "
            "GROUP BY bank_category ORDER BY banks DESC", conn)
        print(cats.to_string(index=False))
        cats = pd.read_sql(
            "SELECT app_category, COUNT(*) AS apps FROM dim_app "
            "GROUP BY app_category ORDER BY apps DESC", conn)
        print(cats.to_string(index=False))

        print("\nSample: top 5 remitter banks in the latest month")
        sample = pd.read_sql(
            "SELECT d.year_month_label AS month, b.bank_name, "
            "f.total_volume_mn, f.approved_pct, f.td_pct "
            "FROM fact_remitter_performance f "
            "JOIN dim_bank b ON b.bank_id = f.bank_id "
            "JOIN dim_date d ON d.date_key = f.date_key "
            "WHERE f.date_key = (SELECT MAX(date_key) "
            "FROM fact_remitter_performance) "
            "ORDER BY f.total_volume_mn DESC LIMIT 5", conn)
        print(sample.to_string(index=False))

    engine.dispose()
    print("\nDone." if all_ok else "\nFinished with row-count mismatches; check above.")


if __name__ == "__main__":
    main()