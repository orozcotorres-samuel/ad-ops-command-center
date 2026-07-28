"""
Step 2: Compute real ad metrics from the DuckDB database.

This reads the 'impressions' table Step 1 created and computes clean daily
metrics (impressions, clicks, CTR, conversions, spend) plus week-over-week
changes. It returns everything as a strict, structured object (Pydantic models)
so the rest of the pipeline can rely on the exact shape.

This structured object is the GROUND TRUTH:
  - the generator agent (Step 3) will write a summary of it, and
  - the evaluator agent (Step 4) will fact-check the summary against it.

Run it directly to print the metrics for the last 14 days:

    .venv/bin/python src/metrics.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import duckdb
from pydantic import BaseModel

DB_FILE = Path(__file__).resolve().parent.parent / "data" / "ad_ops.duckdb"


# --- The strict shapes of our data (Pydantic models) -------------------------
# A Pydantic model is like a labeled form: it says exactly which fields exist
# and what type each one is. If the data doesn't fit, Pydantic complains loudly
# instead of letting a silent mistake through.

class DailyMetric(BaseModel):
    date: str            # e.g. "2024-01-14"
    impressions: int
    clicks: int
    ctr: float           # clicks / impressions, rounded
    conversions: int
    spend: float         # sum of 'cost', rounded


class WeekOverWeek(BaseModel):
    metric: str          # which metric this compares, e.g. "ctr"
    recent_7d: float     # value over the most recent 7 days
    prior_7d: float      # value over the 7 days before that
    pct_change: float    # % change from prior to recent (e.g. -12.0 = down 12%)


class MetricsSummary(BaseModel):
    start_date: str
    end_date: str
    total_impressions: int
    total_clicks: int
    overall_ctr: float
    total_conversions: int
    total_spend: float
    days: list[DailyMetric]
    week_over_week: list[WeekOverWeek]


# --- The computation ---------------------------------------------------------

def _pct_change(recent: float, prior: float) -> float:
    """Percent change from prior to recent. Guards against divide-by-zero."""
    if prior == 0:
        return 0.0
    return round((recent - prior) / prior * 100, 2)


def get_metrics(last_n_days: int = 14, end_date: Optional[str] = None) -> MetricsSummary:
    """
    Compute a metrics summary for the last `last_n_days` days of data.

    If `end_date` (a "YYYY-MM-DD" string) is given, the window ends there;
    otherwise it ends on the latest date present in the database. This lets the
    evaluation script (Step 6) ask for many different date ranges.
    """
    con = duckdb.connect(str(DB_FILE), read_only=True)

    if end_date is None:
        end_date = con.execute("SELECT MAX(event_date) FROM impressions").fetchone()[0]
        end_date = str(end_date)

    # Per-day rows within the window.
    day_rows = con.execute(
        """
        SELECT event_date::VARCHAR       AS date,
               COUNT(*)                  AS impressions,
               SUM(click)::BIGINT        AS clicks,
               ROUND(AVG(click), 4)      AS ctr,
               SUM(conversion)::BIGINT   AS conversions,
               ROUND(SUM(cost), 2)       AS spend
        FROM impressions
        WHERE event_date <= ?::DATE
          AND event_date >  ?::DATE - ?::INTEGER
        GROUP BY event_date
        ORDER BY event_date
        """,
        [end_date, end_date, last_n_days],
    ).fetchall()

    days = [
        DailyMetric(date=r[0], impressions=r[1], clicks=r[2],
                    ctr=r[3], conversions=r[4], spend=r[5])
        for r in day_rows
    ]

    if not days:
        raise SystemExit(f"No data found in the {last_n_days} days ending {end_date}.")

    # Period totals (computed from the day rows so they always match).
    total_impressions = sum(d.impressions for d in days)
    total_clicks = sum(d.clicks for d in days)
    total_conversions = sum(d.conversions for d in days)
    total_spend = round(sum(d.spend for d in days), 2)
    overall_ctr = round(total_clicks / total_impressions, 4) if total_impressions else 0.0

    # Week-over-week: most recent 7 days vs the 7 days before them.
    recent = days[-7:]
    prior = days[-14:-7]
    week_over_week: list[WeekOverWeek] = []
    if len(recent) == 7 and len(prior) == 7:
        def ctr_of(group: list[DailyMetric]) -> float:
            imp = sum(d.impressions for d in group)
            clk = sum(d.clicks for d in group)
            return round(clk / imp, 4) if imp else 0.0

        pairs = {
            "impressions": (sum(d.impressions for d in recent), sum(d.impressions for d in prior)),
            "clicks": (sum(d.clicks for d in recent), sum(d.clicks for d in prior)),
            "ctr": (ctr_of(recent), ctr_of(prior)),
            "conversions": (sum(d.conversions for d in recent), sum(d.conversions for d in prior)),
            "spend": (round(sum(d.spend for d in recent), 2), round(sum(d.spend for d in prior), 2)),
        }
        for name, (r_val, p_val) in pairs.items():
            week_over_week.append(
                WeekOverWeek(metric=name, recent_7d=r_val, prior_7d=p_val,
                             pct_change=_pct_change(r_val, p_val))
            )

    con.close()
    return MetricsSummary(
        start_date=days[0].date,
        end_date=days[-1].date,
        total_impressions=total_impressions,
        total_clicks=total_clicks,
        overall_ctr=overall_ctr,
        total_conversions=total_conversions,
        total_spend=total_spend,
        days=days,
        week_over_week=week_over_week,
    )


if __name__ == "__main__":
    summary = get_metrics(last_n_days=14)
    # model_dump_json prints the whole structured object as clean JSON.
    print(summary.model_dump_json(indent=2))
