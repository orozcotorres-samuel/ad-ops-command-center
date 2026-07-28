"""
Step 4: The Evaluator agent — the important part.

A second Claude call that fact-checks a summary against the real numbers.
It receives BOTH:
  - the written summary (from the generator), and
  - the ground-truth metrics object (from Step 2),
then extracts each factual claim and checks it against the real data, flagging
anything the numbers don't support.

This is the "evaluator" half of the evaluator-optimizer pattern.

Run it directly to see it (a) pass an accurate summary and (b) catch a summary
with a deliberately planted error:

    .venv/bin/python src/evaluator.py
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from dotenv import load_dotenv
import anthropic

from metrics import MetricsSummary, get_metrics
from generator import GeneratedReport, generate_report, MODEL

load_dotenv()


class ClaimCheck(BaseModel):
    """The verdict on one factual claim pulled out of the summary."""
    claim: str = Field(description="The exact factual claim, quoted from the summary.")
    supported: bool = Field(description="True only if the ground-truth data supports it.")
    explanation: str = Field(
        description="Short reason. If unsupported, state the REAL value from the data."
    )


class EvaluationResult(BaseModel):
    claims: list[ClaimCheck]

    @property
    def unsupported(self) -> list[ClaimCheck]:
        return [c for c in self.claims if not c.supported]

    @property
    def has_unsupported_claims(self) -> bool:
        return len(self.unsupported) > 0


SYSTEM_PROMPT = (
    "You are a meticulous ad-analytics fact-checker. You are given (1) a JSON object "
    "of ground-truth metrics and (2) a written summary of those metrics. Pull out "
    "each distinct factual/numeric claim in the summary and check it against the "
    "ground truth. Mark a claim supported ONLY if the numbers and the direction of "
    "change match the data (allow reasonable rounding, e.g. 36.92% ~ 37%). If a claim "
    "is unsupported, say what the real value actually is. Do not invent data — rely "
    "only on the provided JSON."
)


def evaluate_summary(summary_text: str, metrics: MetricsSummary,
                     model: str = MODEL) -> EvaluationResult:
    """Check every factual claim in `summary_text` against the real metrics."""
    client = anthropic.Anthropic()
    metrics_json = metrics.model_dump_json(indent=2)
    user_prompt = (
        "GROUND-TRUTH METRICS (the only source of truth):\n"
        f"{metrics_json}\n\n"
        "SUMMARY TO FACT-CHECK:\n"
        f"{summary_text}\n\n"
        "Extract and check every factual claim."
    )
    response = client.messages.parse(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=EvaluationResult,
    )
    return response.parsed_output


def _print_result(title: str, result: EvaluationResult) -> None:
    print(f"\n=== {title} ===")
    for c in result.claims:
        mark = "✅" if c.supported else "❌"
        print(f"{mark} {c.claim}")
        if not c.supported:
            print(f"     ↳ {c.explanation}")
    n_bad = len(result.unsupported)
    print(f"--- {len(result.claims)} claims checked, {n_bad} unsupported ---")


if __name__ == "__main__":
    metrics = get_metrics(last_n_days=14)

    # (a) Check the generator's REAL summary — should pass cleanly.
    report = generate_report(metrics)
    good = evaluate_summary(report.technical_summary, metrics)
    _print_result("CHECKING THE REAL (ACCURATE) SUMMARY", good)

    # (b) Check a summary with a DELIBERATE error — should get flagged.
    planted_error = (
        "Over the two weeks, the campaign achieved an overall CTR of 55%, and "
        "week-over-week spend rose 30% while conversions increased 12%."
    )
    bad = evaluate_summary(planted_error, metrics)
    _print_result("CHECKING A SUMMARY WITH A PLANTED ERROR", bad)
