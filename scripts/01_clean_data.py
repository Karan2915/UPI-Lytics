"""
UPIlytics - Step 1: Clean and validate the raw NPCI files.

Project layout expected (run from anywhere):

    upilytics/
    ├── data/
    │   ├── raw/     <- put the 4 raw CSV files here (never edit them)
    │   └── clean/   <- cleaned CSVs are written here
    ├── docs/        <- data_quality_log.md is written here
    └── scripts/01_clean_data.py

Raw files expected in data/raw/:
    upi-product-statistics.csv          (national monthly: volume, value, banks live)
    combined_RemitterBanks_data.csv     (top-50 remitter banks per month)
    combined_BeneficiaryBanks_data.csv  (top-50 beneficiary banks per month)
    combined_UPI_apps_data.csv          (UPI apps per month)

Outputs in data/clean/:
    fact_national_monthly.csv
    fact_remitter_monthly.csv
    fact_beneficiary_monthly.csv
    fact_app_monthly.csv

Also writes docs/data_quality_log.md describing every issue found and fixed.

Requires: pandas, numpy   (pip install pandas numpy)
"""

from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Paths and file names
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
CLEAN_DIR = ROOT / "data" / "clean"
DOCS_DIR = ROOT / "docs"

NATIONAL_FILE = RAW_DIR / "upi-product-statistics.csv"
REMITTER_FILE = RAW_DIR / "combined_RemitterBanks_data.csv"
BENEFICIARY_FILE = RAW_DIR / "combined_BeneficiaryBanks_data.csv"
APPS_FILE = RAW_DIR / "combined_UPI_apps_data.csv"

# Bank names that appear under more than one spelling in the raw data.
# Keys are lower-case; values are the standard name we keep.
BANK_NAME_MAP = {
    "the jammu and kashmir bank": "Jammu and Kashmir Bank",
    "the south indian bank limited": "South Indian Bank",
    "the cosmos co-operative bank ltd": "Cosmos Bank",
    "esaf small finance bank ltd": "ESAF Small Finance Bank",
    "india post payment bank": "India Post Payments Bank",
    "rbl": "RBL Bank",
}

LOG_LINES = []


def log(message=""):
    """Print a line and keep it for the data quality log."""
    print(message)
    LOG_LINES.append(message)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def read_raw(path):
    if not path.exists():
        raise FileNotFoundError(
            f"Missing raw file: {path}\nPlace it in data/raw/ and run again."
        )
    df = pd.read_csv(path, encoding="utf-8-sig")
    # Headers in the raw files contain stray tabs/spaces (e.g. 'BD %\t').
    df.columns = df.columns.str.strip()
    return df


def to_pct(series):
    """'99.73%' -> 99.73 ; '-' or blank -> NaN."""
    cleaned = (
        series.astype(str)
        .str.replace("%", "", regex=False)
        .str.strip()
        .replace({"-": np.nan, "": np.nan, "nan": np.nan, "None": np.nan})
    )
    return pd.to_numeric(cleaned, errors="coerce")


def to_num(series):
    """Numeric conversion; '-' or blank -> NaN."""
    cleaned = (
        series.astype(str)
        .str.strip()
        .replace({"-": np.nan, "": np.nan, "nan": np.nan, "None": np.nan})
    )
    return pd.to_numeric(cleaned, errors="coerce")


def clean_text(series):
    return series.astype(str).str.replace(r"\s+", " ", regex=True).str.strip()


def add_period(df):
    """Add a month-start date column and a YYYY-MM label from year + month."""
    df["period"] = pd.to_datetime(
        dict(year=df["year"], month=df["month"], day=1)
    )
    df["year_month"] = df["period"].dt.strftime("%Y-%m")
    return df


def weighted_mean(values, weights):
    mask = values.notna() & weights.notna()
    if not mask.any() or weights[mask].sum() == 0:
        return np.nan
    return np.average(values[mask], weights=weights[mask])


# --------------------------------------------------------------------------
# 1. National monthly file
# --------------------------------------------------------------------------
def clean_national():
    log("## 1. National monthly statistics (upi-product-statistics.csv)")
    df = read_raw(NATIONAL_FILE)
    log(f"- Raw rows: {len(df)}; columns: {list(df.columns)}")

    # The 'date' column has day and month swapped: '2023-01-08' means Aug 2023.
    parsed = pd.to_datetime(df["date"], format="%Y-%d-%m", errors="coerce")
    bad_dates = int(parsed.isna().sum())
    log(
        "- FIXED: `date` column was stored as YYYY-DD-MM (day and month "
        "swapped, e.g. 2023-01-08 = August 2023). Re-parsed with the correct "
        f"format. Unparseable dates: {bad_dates}."
    )

    out = pd.DataFrame(
        {
            "row_id": pd.to_numeric(df["id"], errors="coerce"),
            "period": parsed.dt.to_period("M").dt.to_timestamp(),
            "banks_live_on_upi": to_num(df["num_of_banks_live_on_upi"]),
            "volume_mn": to_num(df["volume"]),
            "value_cr": to_num(df["value"]),
        }
    )
    out = out.dropna(subset=["period"]).reset_index(drop=True)

    # The raw rows are listed newest first with consecutive ids, so a month
    # that appears twice means the older row (higher id) carries a typo in
    # its date. Shift that row back one month.
    for period, grp in out[out.duplicated("period", keep=False)].groupby("period"):
        if len(grp) == 2:
            older_idx = grp["row_id"].idxmax()
            out.loc[older_idx, "period"] = period - pd.DateOffset(months=1)
            log(f"- FIXED: two rows were both dated {period:%Y-%m}; the row "
                f"with id {int(grp['row_id'].max())} was moved to "
                f"{(period - pd.DateOffset(months=1)):%Y-%m} because the ids "
                "run in consecutive month order")
    out = out.drop(columns="row_id").sort_values("period").reset_index(drop=True)
    out["year"] = out["period"].dt.year
    out["month"] = out["period"].dt.month
    out["year_month"] = out["period"].dt.strftime("%Y-%m")

    # Derived columns. 1 Cr = 1e7 rupees, 1 Mn = 1e6 transactions,
    # so average ticket size in rupees = value_cr * 10 / volume_mn.
    out["avg_ticket_size_rs"] = np.where(
        out["volume_mn"] > 0, out["value_cr"] * 10 / out["volume_mn"], np.nan
    )
    vol_prev = out["volume_mn"].shift(1)
    out["volume_mom_growth_pct"] = np.where(
        vol_prev > 0, (out["volume_mn"] / vol_prev - 1) * 100, np.nan
    )
    vol_prev_year = out["volume_mn"].shift(12)
    out["volume_yoy_growth_pct"] = np.where(
        vol_prev_year > 0, (out["volume_mn"] / vol_prev_year - 1) * 100, np.nan
    )

    # Validation
    dup_months = int(out.duplicated("period").sum())
    expected = pd.date_range(out["period"].min(), out["period"].max(), freq="MS")
    missing = sorted(set(expected) - set(out["period"]))
    zero_rows = int((out["volume_mn"] == 0).sum())
    neg_rows = int(((out["volume_mn"] < 0) | (out["value_cr"] < 0)).sum())
    log(f"- Range: {out['year_month'].min()} to {out['year_month'].max()} "
        f"({len(out)} months)")
    log(f"- Duplicate months: {dup_months}; missing months: {len(missing)}; "
        f"negative values: {neg_rows}")
    log(f"- Months with zero volume (UPI launch period in 2016): {zero_rows} "
        "(kept; growth and ticket size left blank for these)")
    log("- Units: volume in millions, value in crores (1 crore = 10 million rupees)")
    log()

    out = out[
        ["period", "year", "month", "year_month", "banks_live_on_upi",
         "volume_mn", "value_cr", "avg_ticket_size_rs",
         "volume_mom_growth_pct", "volume_yoy_growth_pct"]
    ]
    return out


# --------------------------------------------------------------------------
# 2. Bank files (remitter and beneficiary share the same cleaning logic)
# --------------------------------------------------------------------------
def standardise_bank_name(series):
    cleaned = clean_text(series)
    return cleaned.apply(lambda n: BANK_NAME_MAP.get(n.lower(), n))


def clean_bank_file(path, role):
    """role is 'Remitter' or 'Beneficiary'."""
    log(f"## {'2' if role == 'Remitter' else '3'}. {role} banks ({path.name})")
    df = read_raw(path)
    log(f"- Raw rows: {len(df)}; columns: {list(df.columns)}")

    name_col = f"UPI {role} Banks"
    rename = {
        name_col: "bank_name",
        "Year": "year",
        "Month": "month",
        "Total Volume (In Mn)": "total_volume_mn",
        "Approved %": "approved_pct",
        "BD %": "bd_pct",
        "TD%": "td_pct",
        "Deemed Approved %": "deemed_approved_pct",
        "Total Debit Reversal Count (In Mn)": "debit_reversal_count_mn",
        "Debit Reversal Success %": "debit_reversal_success_pct",
    }
    df = df.rename(columns=rename)

    # Drop empty / index-like helper columns.
    dropped = [c for c in df.columns
               if c.startswith("Column") or c.startswith("Unnamed") or c == "Sr.No."]
    df = df.drop(columns=dropped)
    if dropped:
        log(f"- DROPPED columns with no analytical value: {dropped}")

    log("- FIXED: header names contained tabs/spaces (e.g. 'BD %\\t'); stripped.")

    raw_names = df["bank_name"].nunique()
    df["bank_name"] = standardise_bank_name(df["bank_name"])
    log(f"- FIXED: standardised bank names ({raw_names} raw -> "
        f"{df['bank_name'].nunique()} distinct names)")

    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    df["month"] = pd.to_numeric(df["month"], errors="coerce").astype("Int64")
    df["total_volume_mn"] = to_num(df["total_volume_mn"])
    for col in ["approved_pct", "bd_pct", "td_pct", "deemed_approved_pct",
                "debit_reversal_success_pct"]:
        if col in df.columns:
            df[col] = to_pct(df[col])
    if "debit_reversal_count_mn" in df.columns:
        df["debit_reversal_count_mn"] = to_num(df["debit_reversal_count_mn"])
    log("- FIXED: percentage columns converted from text ('99.73%') to numbers "
        "(99.73); '-' placeholders converted to blanks")

    # Duplicate bank-month rows (can appear after name standardisation).
    dup_mask = df.duplicated(["bank_name", "year", "month"], keep=False)
    dup_rows = int(dup_mask.sum())
    if dup_rows:
        pct_cols = [c for c in ["approved_pct", "bd_pct", "td_pct",
                                "deemed_approved_pct"] if c in df.columns]
        merged = []
        for (name, y, m), grp in df.groupby(["bank_name", "year", "month"],
                                            sort=False):
            if len(grp) == 1:
                merged.append(grp.iloc[0].to_dict())
                continue
            row = {"bank_name": name, "year": y, "month": m,
                   "total_volume_mn": grp["total_volume_mn"].sum()}
            for c in pct_cols:
                row[c] = weighted_mean(grp[c], grp["total_volume_mn"])
            if "debit_reversal_count_mn" in grp.columns:
                row["debit_reversal_count_mn"] = grp["debit_reversal_count_mn"].sum(
                    min_count=1)
                row["debit_reversal_success_pct"] = weighted_mean(
                    grp["debit_reversal_success_pct"],
                    grp["debit_reversal_count_mn"])
            merged.append(row)
        df = pd.DataFrame(merged)
        log(f"- FIXED: {dup_rows} rows were duplicate bank-month entries; "
            "merged (volumes summed, percentages volume-weighted)")
    else:
        log("- Duplicate bank-month rows: 0")

    df = add_period(df)

    # Validation checks
    null_report = df.isna().sum()
    null_report = null_report[null_report > 0]
    log(f"- Remaining blanks: {null_report.to_dict() if len(null_report) else 'none'}")

    pct_cols_all = [c for c in ["approved_pct", "bd_pct", "td_pct",
                                "deemed_approved_pct",
                                "debit_reversal_success_pct"] if c in df.columns]
    out_of_range = 0
    for c in pct_cols_all:
        bad = (df[c] < 0) | (df[c] > 100)
        for _, r in df[bad].iterrows():
            log(f"- FLAGGED: {r['bank_name']} {int(r['year'])}-{int(r['month']):02d} "
                f"has {c} = {r[c]:.2f}, which is impossible; set to blank")
        out_of_range += int(bad.sum())
        df.loc[bad, c] = np.nan
    log(f"- Percentages outside 0-100: {out_of_range}")
    log(f"- Negative or zero volumes: {int((df['total_volume_mn'] <= 0).sum())}")

    gap = (df["approved_pct"] + df["bd_pct"] + df["td_pct"] - 100).abs()
    log(f"- Rows where approved + BD + TD differs from 100% by more than 1 "
        f"point: {int((gap > 1).sum())} of {len(df)} "
        "(informational; rounding and deemed approvals can explain small gaps)")

    per_month = df.groupby("year_month").size()
    log(f"- Banks per month: min {per_month.min()}, max {per_month.max()} "
        "(NPCI publishes only the top 50 each month, so the set of banks "
        "changes over time; a bank missing in a month fell out of the top 50)")
    expected = pd.date_range(df["period"].min(), df["period"].max(), freq="MS")
    missing = sorted(set(expected) - set(df["period"]))
    log(f"- Range: {df['year_month'].min()} to {df['year_month'].max()} "
        f"({df['year_month'].nunique()} months); missing months: {len(missing)}")

    # Derived counts, useful for volume-weighted analysis in SQL and Power BI.
    df["approved_volume_mn"] = df["total_volume_mn"] * df["approved_pct"] / 100
    df["bd_volume_mn"] = df["total_volume_mn"] * df["bd_pct"] / 100
    df["td_volume_mn"] = df["total_volume_mn"] * df["td_pct"] / 100
    df["volume_rank_in_month"] = (
        df.groupby("year_month")["total_volume_mn"]
        .rank(ascending=False, method="first").astype(int)
    )
    log()

    front = ["period", "year", "month", "year_month", "bank_name",
             "total_volume_mn"]
    rest = [c for c in df.columns if c not in front]
    return df.sort_values(["period", "volume_rank_in_month"]).reset_index(
        drop=True)[front + rest]


# --------------------------------------------------------------------------
# 4. Apps file
# --------------------------------------------------------------------------
def clean_apps():
    log("## 4. UPI apps (combined_UPI_apps_data.csv)")
    df = read_raw(APPS_FILE)
    log(f"- Raw rows: {len(df)}; columns: {list(df.columns)}")

    df = df.rename(columns={
        "Application Name": "app_name",
        "Year": "year",
        "Month": "month",
        "Volume Customer(Mn)": "volume_customer_mn",
        "Value Customer (Cr)": "value_customer_cr",
        "Volume B2C (Mn)": "volume_b2c_mn",
        "Value B2C (Cr)": "value_b2c_cr",
        "Volume B2B(Mn)": "volume_b2b_mn",
        "Value B2B(Cr)": "value_b2b_cr",
        "Volume (Mn)": "total_volume_mn",
        "Value (Cr)": "total_value_cr",
    })

    original = df["app_name"].copy()
    # NPCI marks some apps with a trailing ' #' footnote; remove it.
    df["app_name"] = (
        clean_text(df["app_name"]).str.replace(r"\s*#$", "", regex=True).str.strip()
    )
    changed = int((original != df["app_name"]).sum())
    log(f"- FIXED: removed trailing '#' footnote markers and extra spaces from "
        f"{changed} app-name entries ({df['app_name'].nunique()} distinct apps)")

    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    df["month"] = pd.to_numeric(df["month"], errors="coerce").astype("Int64")
    num_cols = ["volume_customer_mn", "value_customer_cr", "volume_b2c_mn",
                "value_b2c_cr", "volume_b2b_mn", "value_b2b_cr",
                "total_volume_mn", "total_value_cr"]
    for col in num_cols:
        df[col] = to_num(df[col])

    # The same app can appear on more than one row in a month (for example
    # Bajaj Finserv has two rows per month). Combine them by summing.
    dup_rows = int(df.duplicated(["app_name", "year", "month"], keep=False).sum())
    df = (
        df.groupby(["app_name", "year", "month"], as_index=False)[num_cols]
        .sum(min_count=1)
    )
    log(f"- FIXED: {dup_rows} rows belonged to app-month pairs listed more than "
        "once; combined into one row per app per month by summing volume and "
        "value")

    df = add_period(df)

    blanks = df[num_cols].isna().sum().to_dict()
    log(f"- Blanks: {blanks}")
    log("- NOTE: B2C and B2B columns are blank for many apps and months "
        "because NPCI only reports them for some apps; they were left blank, "
        "not filled with zeros. Use total_volume_mn and total_value_cr for "
        "market share.")

    components = (
        df["volume_customer_mn"].fillna(0)
        + df["volume_b2c_mn"].fillna(0)
        + df["volume_b2b_mn"].fillna(0)
    )
    mismatch = int(((components - df["total_volume_mn"]).abs() > 0.05).sum())
    log(f"- Rows where customer + B2C + B2B volume differs from total volume "
        f"by more than 0.05 Mn: {mismatch} of {len(df)} (informational)")
    log(f"- Negative values: {int((df[num_cols] < 0).sum().sum())}")
    log(f"- Range: {df['year_month'].min()} to {df['year_month'].max()} "
        f"({df['year_month'].nunique()} months)")

    # Market share of total app volume per month.
    month_total = df.groupby("year_month")["total_volume_mn"].transform("sum")
    df["volume_share_pct"] = np.where(
        month_total > 0, df["total_volume_mn"] / month_total * 100, np.nan
    )
    month_value = df.groupby("year_month")["total_value_cr"].transform("sum")
    df["value_share_pct"] = np.where(
        month_value > 0, df["total_value_cr"] / month_value * 100, np.nan
    )
    log()

    front = ["period", "year", "month", "year_month", "app_name",
             "total_volume_mn", "total_value_cr"]
    rest = [c for c in df.columns if c not in front]
    return df.sort_values(["period", "total_volume_mn"],
                          ascending=[True, False]).reset_index(drop=True)[
        front + rest]


# --------------------------------------------------------------------------
# 5. Cross-file reconciliation
# --------------------------------------------------------------------------
def cross_check(national, remitter, beneficiary, apps):
    log("## 5. Cross-file reconciliation")
    nat = national.set_index("year_month")["volume_mn"]
    for label, df, col in [
        ("Apps (all apps listed)", apps, "total_volume_mn"),
        ("Remitter banks (top 50), approved volume", remitter, "approved_volume_mn"),
        ("Beneficiary banks (top 50), approved volume", beneficiary, "approved_volume_mn"),
    ]:
        monthly = df.groupby("year_month")[col].sum()
        joined = pd.concat([monthly.rename("file"), nat.rename("national")],
                           axis=1, join="inner")
        if joined.empty:
            log(f"- {label}: no overlapping months with the national file")
            continue
        joined["diff_pct"] = (joined["file"] / joined["national"] - 1) * 100
        log(f"- {label} vs national volume over {len(joined)} overlapping "
            f"months ({joined.index.min()} to {joined.index.max()}): "
            f"average difference {joined['diff_pct'].mean():.1f}%, "
            f"range {joined['diff_pct'].min():.1f}% to "
            f"{joined['diff_pct'].max():.1f}%")
    log("- Reading guide: the national file counts successful transactions "
        "only, while the bank files' total volume also includes declined "
        "ones, so bank files are compared on approved volume. Bank files "
        "cover only the top 50 banks, so a gap well below zero means banks "
        "outside the top 50 carry meaningful volume. Small positive gaps can "
        "come from rounding and from deemed approvals.")
    log()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    log("# UPIlytics Data Quality Log")
    log()
    log("Generated by scripts/01_clean_data.py. Raw files are never modified.")
    log()

    national = clean_national()
    remitter = clean_bank_file(REMITTER_FILE, "Remitter")
    beneficiary = clean_bank_file(BENEFICIARY_FILE, "Beneficiary")
    apps = clean_apps()
    cross_check(national, remitter, beneficiary, apps)

    outputs = {
        "fact_national_monthly.csv": national,
        "fact_remitter_monthly.csv": remitter,
        "fact_beneficiary_monthly.csv": beneficiary,
        "fact_app_monthly.csv": apps,
    }
    log("## Output files")
    for name, frame in outputs.items():
        frame = frame.copy()
        frame["period"] = pd.to_datetime(frame["period"]).dt.strftime("%Y-%m-%d")
        frame.to_csv(CLEAN_DIR / name, index=False)
        log(f"- data/clean/{name}: {len(frame)} rows, {len(frame.columns)} columns")

    (DOCS_DIR / "data_quality_log.md").write_text(
        "\n".join(LOG_LINES) + "\n", encoding="utf-8"
    )
    print("\nDone. Data quality log written to docs/data_quality_log.md")


if __name__ == "__main__":
    main()