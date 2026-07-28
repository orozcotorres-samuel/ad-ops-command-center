"""
Step 5: The correction loop — the "optimizer" half of evaluator-optimizer.

Steps 3 and 4 gave us two halves that don't talk to each other yet: a generator
that writes a summary, and an evaluator that fact-checks it. This file wires
them into one loop:

    metrics -> generate -> evaluate -> (if claims are unsupported) rewrite
                              ^                                      |
                              +--------------------------------------+

The rewrite is NOT "try again, do better". We hand the generator the exact
claims the evaluator flagged, along with the real values, and tell it to fix
those and leave everything else alone. That's what makes this a grounded
pipeline instead of a slot machine.

Important framing: this is a WORKFLOW, not an "agent". The path is fixed and
written by us in Python — generate, check, maybe rewrite, stop. Claude does not
decide what to do next; it only fills in the writing and the checking. Workflows
like this are what production teams actually ship, because you can measure them.

The loop always terminates: at most `max_revisions` rewrites (default 2), so at
most 3 drafts, no matter what.

Run it directly to watch a real report get generated, checked, and corrected:

    .venv/bin/python src/pipeline.py
"""

from __future__ import annotations

from pydantic import BaseModel
from dotenv import load_dotenv
import anthropic

from metrics import MetricsSummary, get_metrics
from generator import GeneratedReport, generate_report, MODEL
from evaluator import ClaimCheck, EvaluationResult, evaluate_summary

load_dotenv()


# --- What one trip around the loop looks like --------------------------------

class PipelineIteration(BaseModel):
    """One draft and the verdict it received."""
    attempt: int                    # 1 = the first draft, 2 = first rewrite, ...
    report: GeneratedReport
    evaluation: EvaluationResult


class PipelineResult(BaseModel):
    """
    Everything that happened, start to finish.

    We keep EVERY draft (not just the final one) because Step 6 measures the
    hallucination rate: how often the FIRST draft contained unsupported claims,
    and how often the loop managed to fix them.
    """
    start_date: str
    end_date: str
    iterations: list[PipelineIteration]

    @property
    def final_report(self) -> GeneratedReport:
        return self.iterations[-1].report

    @property
    def final_evaluation(self) -> EvaluationResult:
        return self.iterations[-1].evaluation

    @property
    def n_attempts(self) -> int:
        return len(self.iterations)

    @property
    def first_draft_had_unsupported_claims(self) -> bool:
        """The headline number for Step 6: did the ungrounded draft hallucinate?"""
        return self.iterations[0].evaluation.has_unsupported_claims

    @property
    def final_has_unsupported_claims(self) -> bool:
        """Did anything survive the correction loop? (Should usually be False.)"""
        return self.final_evaluation.has_unsupported_claims

    @property
    def was_corrected(self) -> bool:
        """True if the loop found a problem AND ended up fixing it."""
        return self.first_draft_had_unsupported_claims and not self.final_has_unsupported_claims


# --- The rewrite call ---------------------------------------------------------

REVISION_SYSTEM_PROMPT = (
    "You are an advertising-analytics writer revising your own draft. A fact-checker "
    "compared your draft against the ground-truth metrics and flagged specific claims "
    "as unsupported. Rewrite both summaries so that every flagged claim is corrected "
    "to the real value from the data. Keep everything that was already correct — same "
    "length, same tone, same structure. Do not add new figures, dates, or trends that "
    "are not in the provided metrics. If a flagged claim cannot be supported by the "
    "data at all, remove it rather than guessing."
)


def _format_flagged(flagged: list[ClaimCheck]) -> str:
    """Turn the evaluator's flagged claims into a numbered fix-list for the writer."""
    lines = []
    for i, c in enumerate(flagged, start=1):
        lines.append(f'{i}. CLAIM: "{c.claim}"\n   PROBLEM: {c.explanation}')
    return "\n".join(lines)


def revise_report(metrics: MetricsSummary, previous: GeneratedReport,
                  flagged: list[ClaimCheck], model: str = MODEL) -> GeneratedReport:
    """Ask the generator to rewrite its draft, fixing exactly the flagged claims."""
    client = anthropic.Anthropic()
    metrics_json = metrics.model_dump_json(indent=2)

    user_prompt = (
        "GROUND-TRUTH METRICS (the only source of truth):\n"
        f"{metrics_json}\n\n"
        "YOUR PREVIOUS TECHNICAL SUMMARY:\n"
        f"{previous.technical_summary}\n\n"
        "YOUR PREVIOUS PLAIN-ENGLISH SUMMARY:\n"
        f"{previous.plain_summary}\n\n"
        "THE FACT-CHECKER FLAGGED THESE CLAIMS AS UNSUPPORTED:\n"
        f"{_format_flagged(flagged)}\n\n"
        "Rewrite both summaries with exactly these problems fixed."
    )

    response = client.messages.parse(
        model=model,
        max_tokens=1500,
        system=REVISION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=GeneratedReport,
    )
    return response.parsed_output


# --- The loop -----------------------------------------------------------------

def run_pipeline(metrics: MetricsSummary, max_revisions: int = 2,
                 model: str = MODEL, verbose: bool = False,
                 seed_report: GeneratedReport | None = None) -> PipelineResult:
    """
    Generate a report, fact-check it, and rewrite it until it holds up.

    Stops as soon as an evaluation comes back clean, or after `max_revisions`
    rewrites — whichever happens first. Set verbose=True to watch it work;
    Step 6 will run it quietly hundreds of times.

    `seed_report` skips the first generator call and uses the draft you hand it
    as attempt 1. That is only for demos and tests — it lets us feed in a draft
    with known errors and watch the loop repair it, without waiting for the
    model to hallucinate on its own.
    """
    iterations: list[PipelineIteration] = []

    # Attempt 1: the plain, uncorrected draft. This is the one Step 6 measures.
    report = seed_report if seed_report is not None else generate_report(metrics, model=model)
    evaluation = evaluate_summary(report.technical_summary, metrics, model=model)
    iterations.append(PipelineIteration(attempt=1, report=report, evaluation=evaluation))
    if verbose:
        _print_attempt(iterations[-1])

    # Attempts 2..N: only happen if the fact-checker found something wrong.
    for attempt in range(2, max_revisions + 2):
        if not evaluation.has_unsupported_claims:
            break
        report = revise_report(metrics, report, evaluation.unsupported, model=model)
        evaluation = evaluate_summary(report.technical_summary, metrics, model=model)
        iterations.append(
            PipelineIteration(attempt=attempt, report=report, evaluation=evaluation)
        )
        if verbose:
            _print_attempt(iterations[-1])

    return PipelineResult(
        start_date=metrics.start_date,
        end_date=metrics.end_date,
        iterations=iterations,
    )


# --- Pretty printing (only for running this file by hand) ---------------------

def _print_attempt(it: PipelineIteration) -> None:
    label = "FIRST DRAFT" if it.attempt == 1 else f"REVISION {it.attempt - 1}"
    print(f"\n{'=' * 62}")
    print(f"ATTEMPT {it.attempt} — {label}")
    print("=" * 62)
    print(it.report.technical_summary)
    print("\n--- fact-check ---")
    for c in it.evaluation.claims:
        mark = "PASS" if c.supported else "FAIL"
        print(f"[{mark}] {c.claim}")
        if not c.supported:
            print(f"       real value: {c.explanation}")
    n_bad = len(it.evaluation.unsupported)
    print(f"\n{len(it.evaluation.claims)} claims checked, {n_bad} unsupported")


def _print_summary_block(result: PipelineResult) -> None:
    print(f"\n{'=' * 62}")
    print("PIPELINE RESULT")
    print("=" * 62)
    print(f"Period:            {result.start_date} to {result.end_date}")
    print(f"Drafts written:    {result.n_attempts}")
    print(f"First draft clean: {not result.first_draft_had_unsupported_claims}")
    print(f"Final draft clean: {not result.final_has_unsupported_claims}")
    if result.was_corrected:
        print("The loop caught a problem and fixed it.")
    elif result.final_has_unsupported_claims:
        print("Still unsupported after the revision cap — worth a human look.")

    print("\n=== FINAL TECHNICAL SUMMARY ===")
    print(result.final_report.technical_summary)
    print("\n=== FINAL PLAIN-ENGLISH SUMMARY ===")
    print(result.final_report.plain_summary)


# A draft with deliberate errors, used by the --demo run below. The CTR, the
# spend, and the direction of the week-over-week change are all wrong.
PLANTED_ERROR_DRAFT = GeneratedReport(
    technical_summary=(
        "Over the two-week period the campaign delivered 217,715 impressions and "
        "achieved an overall CTR of 55%, driving 10,847 conversions on $4,200 spend. "
        "Week-over-week, impressions rose 30% and conversions increased 12%, making "
        "this the strongest fortnight of the quarter."
    ),
    plain_summary=(
        "The ads had a fantastic two weeks. More than half of everyone who saw them "
        "clicked, traffic jumped 30% versus the week before, and sign-ups climbed 12% "
        "— our best stretch of the quarter, for about $4,200."
    ),
)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    metrics = get_metrics(last_n_days=14)

    if "--demo" in sys.argv:
        # Force the correction path: start from a draft we KNOW is wrong, so we
        # can watch the evaluator catch it and the generator repair it.
        print("Running with a deliberately incorrect first draft (--demo).")
        result = run_pipeline(metrics, max_revisions=2, verbose=True,
                              seed_report=PLANTED_ERROR_DRAFT)
    else:
        result = run_pipeline(metrics, max_revisions=2, verbose=True)

    _print_summary_block(result)

    # `--save PATH` writes the whole run to JSON. The Streamlit app (Step 7)
    # replays a saved run instead of calling the API, so the deployed page
    # costs nothing to visit. PipelineResult is a Pydantic model, so the
    # saved file reloads into the exact same object.
    if "--save" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--save") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.model_dump_json(indent=2))
        print(f"\nSaved this run to {out}")
