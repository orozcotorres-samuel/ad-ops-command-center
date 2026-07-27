"""
Step 1: Get real data flowing.

Reads the Criteo Attribution dataset (a big tab-separated file), takes a random
sample spread across all 30 days, adds a human-readable calendar date, and
writes it into a local DuckDB database file. That DuckDB file becomes our
"source of truth" — the real facts every AI claim later gets checked against.

We let DuckDB read the raw file directly (that's what it's best at) instead of
loading 2.6 GB through pandas.

Run it (with the project's virtual environment) like this:

    .venv/bin/python src/load_data.py

You only need to run this once (or again if you want to rebuild the database).
"""

from pathlib import Path
from datetime import date

import duckdb

# --- Where things live -------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_FILE = PROJECT_ROOT / "data" / "pcb_dataset_final.tsv"
DB_FILE = PROJECT_ROOT / "data" / "ad_ops.duckdb"

# The dataset has 16.5M rows. We only need a sample to build and test the whole
# pipeline quickly. This sample is drawn RANDOMLY across the full file, so it
# covers all 30 days instead of just the first one.
SAMPLE_ROWS = 500_000

# The dataset's timestamps are "seconds since the start of the 30-day window,"
# not real calendar dates. We anchor day 0 to a fixed start date so reports can
# say "2024-01-01" instead of "second 0." The spacing between days is real
# Criteo data — only the starting calendar date is chosen.
START_DATE = date(2024, 1, 1)


def main() -> None:
    if not RAW_FILE.exists():
        raise SystemExit(
            f"Could not find the dataset at:\n  {RAW_FILE}\n\n"
            "Download it from Kaggle first and place it in the data/ folder."
        )

    con = duckdb.connect(str(DB_FILE))

    print(f"Reading {RAW_FILE.name} and drawing a random {SAMPLE_ROWS:,}-row "
          "sample across all days ...")

    # Read the tab-separated file directly, keep only the columns we need, turn
    # the relative "seconds" timestamp into a real calendar date (86,400 =
    # seconds per day), and take a random sample of the whole file.
    con.execute(
        f"""
        CREATE OR REPLACE TABLE impressions AS
        SELECT
            timestamp,
            campaign,
            click,
            conversion,
            cost,
            DATE '{START_DATE}' + (timestamp // 86400)::INTEGER AS event_date
        FROM read_csv(
            '{RAW_FILE.as_posix()}',
            delim='\t',
            header=true
        )
        USING SAMPLE {SAMPLE_ROWS} ROWS (reservoir)
        """
    )

    # Sanity checks so we can SEE it worked.
    total = con.execute("SELECT COUNT(*) FROM impressions").fetchone()[0]
    n_days, first_day, last_day = con.execute(
        "SELECT COUNT(DISTINCT event_date), MIN(event_date), MAX(event_date) "
        "FROM impressions"
    ).fetchone()

    print(f"\nDone. 'impressions' table has {total:,} rows "
          f"spanning {n_days} days ({first_day} to {last_day}).\n")

    print("Real clicks and CTR for the first 7 days:")
    preview = con.execute(
        """
        SELECT event_date,
               COUNT(*)              AS impressions,
               SUM(click)            AS clicks,
               ROUND(AVG(click), 4)  AS ctr
        FROM impressions
        GROUP BY event_date
        ORDER BY event_date
        LIMIT 7
        """
    ).fetchdf()
    print(preview.to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()
