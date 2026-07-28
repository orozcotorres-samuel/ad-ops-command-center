"""
Step 6: Measure the hallucination rate — the headline number of the project.

Steps 3-5 built the pipeline. This measures it. We run the SAME pipeline over
many different date ranges and count how often the first draft contained a
claim the real numbers don't support.

We run TWO arms so the number means something:

  GROUNDED   — the generator gets the full metrics JSON (Step 3's behavior).
  UNGROUNDED — the generator gets only the date range and ONE anchor number
               (total impressions), then is asked for the same detailed
               summary. It has to invent CTR, spend, conversions, and the
               week-over-week direction.

The ungrounded arm is deliberately NOT a strawman. Handing the model nothing
at all would produce a meaningless ~100% hallucination rate. Giving it one
real number and asking it to fill in the rest is what a naive implementation
actually looks like: someone knows the headline volume and asks an LLM to
"write up the weekly report."

Both arms then run the identical Step 5 correction loop, so the comparison is
apples-to-apples: same evaluator, same ground truth, same revision cap.

COST CONTROL — read this before running anything:

    .venv/bin/python src/measure.py --report-only   # free: prints saved numbers
    .venv/bin/python src/measure.py --limit 3       # cheap pilot: 3 ranges
    .venv/bin/python src/measure.py                 # resumes; skips what's done
    .venv/bin/python src/measure.py --force         # re-runs everything (~$4)

The sweep is RESUMABLE. Completed runs are saved to results/ and skipped on the
next run, so re-running a finished sweep makes zero API calls. Only --force
pays for it again. Day to day, use --report-only.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import duckdb
from pydantic import BaseModel
from dotenv import load_dotenv
import anthropic

from metrics import DB_FILE, MetricsSummary, get_metrics
from generator import GeneratedReport, MODEL
from pipeline import PipelineResult, run_pipeline

load_dotenv()

RESULTS_FILE = Path(__file__).resolve().parent.parent / "results" / "hallucination_rate.json"

# Window sizes to sweep. All are >= 14 days so week-over-week is always
# computed — otherwise the two arms would have different claim surfaces.
WINDOW_DAYS = (14, 21, 28)


# --- The ungrounded arm -------------------------------------------------------

UNGROUNDED_SYSTEM_PROMPT = (
    "You are an advertising-analytics writer. Write two summaries of the campaign "
    "period described below. The summaries must be specific and quantitative: cite "
    "CTR, spend, conversions, and week-over-week changes with actual figures, the way "
    "a real analyst report would."
)


def generate_ungrounded(metrics: MetricsSummary, model: str = MODEL) -> GeneratedReport:
    """
    Ask for the same report, but WITHOUT the metrics JSON.

    The model gets the date range and total impressions — one real anchor — and
    must supply every other figure itself. This is the arm we expect to
    hallucinate.
    """
    client = anthropic.Anthropic()
    user_prompt = (
        f"Campaign period: {metrics.start_date} to {metrics.end_date}.\n"
        f"Total impressions over the period: {metrics.total_impressions:,}.\n\n"
        "Write the two summaries."
    )
    response = client.messages.parse(
        model=model,
        max_tokens=1500,
        system=UNGROUNDED_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=GeneratedReport,
    )
    return response.parsed_output


# --- What we record for each run ---------------------------------------------

class MeasurementRow(BaseModel):
    """One (arm, date range) result, flattened so it's easy to aggregate."""
    arm: str                  # "grounded" or "ungrounded"
    window_days: int
    start_date: str
    end_date: str
    n_attempts: int

    first_draft_claims: int
    first_draft_unsupported: int
    first_draft_had_unsupported_claims: bool

    final_claims: int
    final_unsupported: int
    final_has_unsupported_claims: bool
    was_corrected: bool

    final_technical_summary: str
    error: Optional[str] = None   # Optional[...] not `| None`: this venv is Python 3.9


class Measurement(BaseModel):
    """The whole experiment: every row, plus the numbers we actually report."""
    model: str
    rows: list[MeasurementRow]

    def _arm(self, arm: str) -> list[MeasurementRow]:
        return [r for r in self.rows if r.arm == arm and r.error is None]

    def hallucination_rate(self, arm: str) -> float:
        """% of FIRST drafts containing at least one unsupported claim."""
        rows = self._arm(arm)
        if not rows:
            return 0.0
        bad = sum(1 for r in rows if r.first_draft_had_unsupported_claims)
        return round(bad / len(rows) * 100, 1)

    def post_loop_rate(self, arm: str) -> float:
        """% still containing an unsupported claim AFTER the correction loop."""
        rows = self._arm(arm)
        if not rows:
            return 0.0
        bad = sum(1 for r in rows if r.final_has_unsupported_claims)
        return round(bad / len(rows) * 100, 1)

    def claim_level_rate(self, arm: str) -> float:
        """% of individual first-draft CLAIMS that were unsupported."""
        rows = self._arm(arm)
        total = sum(r.first_draft_claims for r in rows)
        if not total:
            return 0.0
        bad = sum(r.first_draft_unsupported for r in rows)
        return round(bad / total * 100, 1)

    def n_runs(self, arm: str) -> int:
        return len(self._arm(arm))

    def n_errors(self) -> int:
        return sum(1 for r in self.rows if r.error is not None)


# --- Building the list of date ranges to test --------------------------------

def build_windows(window_days: tuple[int, ...] = WINDOW_DAYS) -> list[tuple[int, str]]:
    """
    Every (window_size, end_date) pair the data supports.

    With 31 days of data that's 18 fourteen-day windows + 11 twenty-one-day
    + 4 twenty-eight-day = 33 date ranges.
    """
    con = duckdb.connect(str(DB_FILE), read_only=True)
    dates = [str(r[0]) for r in con.execute(
        "SELECT DISTINCT event_date FROM impressions ORDER BY event_date"
    ).fetchall()]
    con.close()

    windows: list[tuple[int, str]] = []
    for days in window_days:
        for end_date in dates[days - 1:]:
            windows.append((days, end_date))
    return windows


# --- Running one arm on one date range ---------------------------------------

def _row_from_result(arm: str, window_days: int, result: PipelineResult) -> MeasurementRow:
    first = result.iterations[0].evaluation
    final = result.final_evaluation
    return MeasurementRow(
        arm=arm,
        window_days=window_days,
        start_date=result.start_date,
        end_date=result.end_date,
        n_attempts=result.n_attempts,
        first_draft_claims=len(first.claims),
        first_draft_unsupported=len(first.unsupported),
        first_draft_had_unsupported_claims=result.first_draft_had_unsupported_claims,
        final_claims=len(final.claims),
        final_unsupported=len(final.unsupported),
        final_has_unsupported_claims=result.final_has_unsupported_claims,
        was_corrected=result.was_corrected,
        final_technical_summary=result.final_report.technical_summary,
    )


def run_one(arm: str, window_days: int, end_date: str,
            model: str = MODEL, max_revisions: int = 2) -> MeasurementRow:
    """Run a single arm over a single date range. Never raises — records errors."""
    try:
        metrics = get_metrics(last_n_days=window_days, end_date=end_date)

        if arm == "grounded":
            result = run_pipeline(metrics, max_revisions=max_revisions, model=model)
        else:
            # Seed attempt 1 with the ungrounded draft, then let the same
            # correction loop try to repair it.
            seed = generate_ungrounded(metrics, model=model)
            result = run_pipeline(metrics, max_revisions=max_revisions,
                                  model=model, seed_report=seed)

        return _row_from_result(arm, window_days, result)

    except Exception as exc:  # one bad call shouldn't kill a 200-call sweep
        return MeasurementRow(
            arm=arm, window_days=window_days,
            start_date="", end_date=end_date, n_attempts=0,
            first_draft_claims=0, first_draft_unsupported=0,
            first_draft_had_unsupported_claims=False,
            final_claims=0, final_unsupported=0,
            final_has_unsupported_claims=False, was_corrected=False,
            final_technical_summary="",
            error=f"{type(exc).__name__}: {exc}",
        )


# --- The sweep ----------------------------------------------------------------

def load_measurement(path: Path = RESULTS_FILE) -> Optional[Measurement]:
    """Read a saved sweep off disk. Returns None if there isn't one yet."""
    if not path.exists():
        return None
    return Measurement.model_validate_json(path.read_text())


def _row_key(row: MeasurementRow) -> tuple:
    """What makes a run unique: which arm, over which date range."""
    return (row.arm, row.window_days, row.end_date)


def run_sweep(limit: Optional[int] = None, model: str = MODEL,
              out_file: Path = RESULTS_FILE, force: bool = False) -> Measurement:
    """
    Run both arms across every date range — but only the ones we don't already have.

    THIS IS RESUMABLE AND COSTS NOTHING TO RE-RUN. Every completed run is kept
    in the results file, and re-running skips them. So:
      - an interrupted sweep picks up exactly where it stopped,
      - a finished sweep re-runs as a no-op (zero API calls),
      - runs that ERRORED are retried, since we have no real result for those.

    Pass force=True to throw away saved results and pay for the whole thing again.
    """
    windows = build_windows()
    if limit is not None:
        windows = windows[:limit]

    previous = None if force else load_measurement(out_file)

    # Reusing results from a different model would silently mix two experiments.
    if previous is not None and previous.model != model:
        raise SystemExit(
            f"Saved results were generated with {previous.model!r}, but you asked for "
            f"{model!r}. Mixing models in one measurement would make the number "
            f"meaningless. Re-run with --force to start over, or use --out to write "
            f"the new model's results to a separate file."
        )

    # Keep the good rows; drop errored ones so they get retried.
    kept = [r for r in previous.rows if r.error is None] if previous else []
    already_done = {_row_key(r) for r in kept}
    measurement = Measurement(model=model, rows=kept)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    planned = [(w, d, arm) for w, d in windows for arm in ("grounded", "ungrounded")]
    todo = [p for p in planned if (p[2], p[0], p[1]) not in already_done]

    if kept:
        print(f"Reusing {len(kept)} saved run(s) from {out_file.name}.")
    if not todo:
        print("Nothing left to run — every date range is already measured.\n")
        return measurement
    print(f"{len(todo)} run(s) still to do.\n")

    for i, (window_days, end_date, arm) in enumerate(todo, start=1):
        print(f"[{i}/{len(todo)}] {arm:<10} {window_days}d ending {end_date} ... ",
              end="", flush=True)

        row = run_one(arm, window_days, end_date, model=model)
        measurement.rows.append(row)

        if row.error:
            print(f"ERROR ({row.error[:60]})")
        else:
            flag = "HALLUCINATED" if row.first_draft_had_unsupported_claims else "clean"
            print(f"{flag} "
                  f"({row.first_draft_unsupported}/{row.first_draft_claims} claims, "
                  f"{row.n_attempts} draft{'s' if row.n_attempts > 1 else ''})")

        # Save after EVERY run so nothing already paid for can be lost.
        out_file.write_text(measurement.model_dump_json(indent=2))

    return measurement


def print_report(m: Measurement) -> None:
    """The numbers you put in the README and say out loud in an interview."""
    print(f"\n{'=' * 66}")
    print("HALLUCINATION RATE")
    print("=" * 66)
    print(f"Model: {m.model}")
    if m.n_errors():
        print(f"NOTE: {m.n_errors()} run(s) errored and are excluded from the rates below.")

    print(f"\n{'':<12} {'runs':>5} {'first-draft':>12} {'claim-level':>12} {'after loop':>11}")
    print(f"{'':<12} {'':>5} {'halluc. rate':>12} {'halluc. rate':>12} {'':>11}")
    print("-" * 66)
    for arm in ("ungrounded", "grounded"):
        print(f"{arm:<12} {m.n_runs(arm):>5} "
              f"{m.hallucination_rate(arm):>11.1f}% "
              f"{m.claim_level_rate(arm):>11.1f}% "
              f"{m.post_loop_rate(arm):>10.1f}%")

    print("\nHow to read this:")
    print("  first-draft rate = % of reports with >=1 claim the data doesn't support")
    print("  claim-level rate = % of individual claims that were unsupported")
    print("  after loop       = % still unsupported once the correction loop ran")

    ung = m.hallucination_rate("ungrounded")
    gro = m.hallucination_rate("grounded")
    if m.n_runs("ungrounded") and m.n_runs("grounded"):
        print(f"\nGrounding the generator took the hallucination rate from "
              f"{ung}% to {gro}%.")
        after = m.post_loop_rate("grounded")
        print(f"The correction loop then brought the grounded arm to {after}%.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Measure the hallucination rate.")
    parser.add_argument("--report-only", action="store_true",
                        help="Just print the saved numbers. Makes NO API calls, costs nothing.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only run the first N date ranges (pilot run).")
    parser.add_argument("--force", action="store_true",
                        help="Discard saved results and re-run everything (costs money).")
    parser.add_argument("--model", default=MODEL, help=f"Model to use (default {MODEL}).")
    parser.add_argument("--out", type=Path, default=RESULTS_FILE,
                        help="Where to write the results JSON.")
    args = parser.parse_args()

    # --report-only never touches the API — use this while building anything
    # downstream (the Streamlit app in Step 7 reads the saved file too).
    if args.report_only:
        saved = load_measurement(args.out)
        if saved is None:
            raise SystemExit(f"No saved results at {args.out}. Run the sweep first.")
        print_report(saved)
        raise SystemExit(0)

    n_windows = len(build_windows()) if args.limit is None else args.limit
    print(f"Sweeping {n_windows} date ranges x 2 arms = {n_windows * 2} pipeline runs.")
    print(f"Model: {args.model}. Results stream to {args.out}")
    if args.force:
        print("--force: discarding any saved results and re-running everything.")
    print()

    measurement = run_sweep(limit=args.limit, model=args.model,
                            out_file=args.out, force=args.force)
    print_report(measurement)
    print(f"\nFull results: {args.out}")
