"""
Step 3: The Generator agent.

Hands the real metrics object (from Step 2) to Claude and asks for two written
summaries back:
  - a short TECHNICAL summary for an analyst/engineer, and
  - a short PLAIN-ENGLISH summary for a non-technical stakeholder.

We use "structured outputs": instead of parsing a paragraph, we give Claude a
strict Pydantic shape (two labeled fields) and the SDK returns exactly that.

Run it directly to see two summaries generated from real data:

    .venv/bin/python src/generator.py
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from dotenv import load_dotenv
import anthropic

from metrics import MetricsSummary, get_metrics

# Load ANTHROPIC_API_KEY from the .env file so anthropic.Anthropic() finds it.
load_dotenv()

# One place to choose the model. Opus 4.8 is the highest-quality default.
# To spend less (especially in Step 6, which makes many calls), switch this to
# "claude-sonnet-4-6" (~40% cheaper) or "claude-haiku-4-5" (cheapest).
MODEL = "claude-opus-4-8"


class GeneratedReport(BaseModel):
    """The strict shape Claude must fill in."""
    technical_summary: str = Field(
        description="2-4 sentences for an analyst. Cite specific numbers "
                    "(CTR, impressions, week-over-week %). Precise and factual."
    )
    plain_summary: str = Field(
        description="2-4 sentences for a non-technical stakeholder. No jargon, "
                    "no raw metric names — explain what happened in plain English."
    )


SYSTEM_PROMPT = (
    "You are an advertising-analytics writer. You are given a JSON object of real "
    "ad-performance metrics. Write two summaries of it. Base every statement only "
    "on the numbers provided — do not invent figures, dates, or trends that aren't "
    "in the data."
)


def generate_report(summary: MetricsSummary, model: str = MODEL) -> GeneratedReport:
    """Ask Claude to summarize the metrics object into two written summaries."""
    client = anthropic.Anthropic()

    # Turn the metrics object into clean JSON text to show the model.
    metrics_json = summary.model_dump_json(indent=2)
    user_prompt = (
        "Here are the ad-performance metrics for the period "
        f"{summary.start_date} to {summary.end_date}:\n\n{metrics_json}\n\n"
        "Write the two summaries."
    )

    # messages.parse() validates Claude's reply against GeneratedReport for us.
    response = client.messages.parse(
        model=model,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=GeneratedReport,
    )
    return response.parsed_output


if __name__ == "__main__":
    metrics = get_metrics(last_n_days=14)
    report = generate_report(metrics)
    print("=== TECHNICAL SUMMARY ===")
    print(report.technical_summary)
    print("\n=== PLAIN-ENGLISH SUMMARY ===")
    print(report.plain_summary)
