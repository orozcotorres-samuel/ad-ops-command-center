"""
Step 1: Get real data flowing.

Reads the Criteo Attribution dataset (a big tab-separated file), takes a
manageable sample, adds a human-readable calendar date, and writes it into a
local DuckDB database file. That DuckDB file becomes our "source of truth" —
the real facts every AI claim later gets checked against.

Run it (with the project's virtual environment) like this:

    .venv/bin/python src/load_data.py

You only need to run this once (or again if you want to rebuild the database).
"""

from pathlib import Path
from datetime import date, timedelta

import pandas as pd
import duckdb

# --- Where things live -------------------------------------------------------
# The raw dataset you download from Kaggle. It may be gzipped (.tsv.gz) or
# already unzipped (.csv / .tsv) — this script handles the .gz directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_FILE = PROJECT_ROOT / "data" / "criteo_attribution_dataset.tsv.gz"
DB_FILE = PROJECT_ROOT / "data" / "ad_ops.duckdb"

# The dataset is huge (16.5M rows). We only need a sample to build and test the
# whole pipeline quickly. Raise this later if you want more data.
SAMPLE_ROWS = 300_000

# The dataset's timestamps are "seconds since the start of the 30-day window,"
# not real calendar dates. We anchor day 0 to a fixed start date so our reports
# can say things like "2024-01-01" instead of "second 0." The relative spacing
# between days is real Criteo data — only the starting calendar date is chosen.
START_DATE = date(2024, 1, 1)

# The columns we actually care about for reporting. The file has more, but these
# are enough for impressions / clicks / CTR / spend per day.
COLUMNS_WE_KEEP = ["timestamp", "campaign", "click", "conversion", "cost"]


def main() -> None:
    if not RAW_FILE.exists():
        raise SystemExit(
            f"Could not find the dataset at:\n  {RAW_FILE}\n\n"
            "Download it from Kaggle first and place it in the data/ folder.\n"
            "See the README / setup instructions."
        )

    print(f"Reading a sample of {SAMPLE_ROWS:,} rows from {RAW_FILE.name} ...")
    # The file is tab-separated with a header row. We read only the columns we
    # need and only the first SAMPLE_ROWS rows to keep this fast.
    df = pd.read_csv(
        RAW_FILE,
        sep="\t",
        usecols=COLUMNS_WE_KEEP,
        nrows=SAMPLE_ROWS,
        compression="infer",  # handles .gz automatically
    )

    # Turn the relative "seconds" timestamp into a real calendar date.
    # 86,400 = seconds in a day. Day 0 -> START_DATE, day 1 -> next day, etc.
    day_index = (df["timestamp"] // 86_400).astype(int)
    df["event_date"] = day_index.apply(lambda d: START_DATE + timedelta(days=int(d)))

    print(f"Loaded {len(df):,} rows spanning "
          f"{df['event_date'].min()} to {df['event_date'].max()}.")

    # Write into DuckDB. If the file/table already exists, we replace it so
    # re-running this script gives a clean rebuild.
    print(f"Writing to DuckDB at {DB_FILE.name} ...")
    con = duckdb.connect(str(DB_FILE))
    con.execute("CREATE OR REPLACE TABLE impressions AS SELECT * FROM df")

    # Quick sanity check: count rows and show a few days of real CTR.
    total = con.execute("SELECT COUNT(*) FROM impressions").fetchone()[0]
    print(f"\nDone. 'impressions' table has {total:,} rows.\n")
    print("Sample — clicks and CTR for the first 5 days:")
    preview = con.execute(
        """
        SELECT event_date,
               COUNT(*)                 AS impressions,
               SUM(click)               AS clicks,
               ROUND(AVG(click), 4)     AS ctr
        FROM impressions
        GROUP BY event_date
        ORDER BY event_date
        LIMIT 5
        """
    ).fetchdf()
    print(preview.to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()
