"""
Step 7: The Streamlit dashboard.

READ-ONLY BY DESIGN. This app makes zero API calls and never touches DuckDB.
It replays results saved to results/ by Steps 5 and 6:

    results/hallucination_rate.json  - the 66-run measurement (Step 6)
    results/demo_run.json            - one real correction-loop run (Step 5)
    results/metrics_snapshot.json    - the daily numbers behind it all (Step 2)

That means the deployed page is free to host, safe on a public URL, and needs
no API key in the cloud. Every number and every sentence on it is real output
from a real run — replayed, not regenerated, and not mocked.

To add a live mode later (Phase 2), the seam is already here: everything below
renders a `PipelineResult`, and `PipelineResult` comes back identically whether
you load it from disk or call `run_pipeline()`. Only the data source changes.

Run it:

    .venv/bin/streamlit run src/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# Put src/ on the import path explicitly. `streamlit run src/app.py` happens to
# do this for us, but the test harness and some hosts don't — so don't rely on it.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure import Measurement          # noqa: E402  (import after sys.path setup)
from metrics import MetricsSummary       # noqa: E402
from pipeline import PipelineResult      # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"

# Categorical slots 1 and 2 from the validated palette. This exact pair was
# checked with the palette validator against a white surface: worst-case
# colorblind separation dE 24.7, well clear of the >=8 target.
BLUE = "#2a78d6"      # grounded
ORANGE = "#eb6834"    # ungrounded
GRID = "#e1e0d9"
MUTED = "#898781"

st.set_page_config(page_title="Ad Ops Command Center", page_icon="•", layout="wide")


# --- Loading the saved results ------------------------------------------------

@st.cache_data
def load_all() -> tuple:
    measurement = Measurement.model_validate_json(
        (RESULTS_DIR / "hallucination_rate.json").read_text())
    demo = PipelineResult.model_validate_json(
        (RESULTS_DIR / "demo_run.json").read_text())
    metrics = MetricsSummary.model_validate_json(
        (RESULTS_DIR / "metrics_snapshot.json").read_text())
    return measurement, demo, metrics


def _axis(title: str) -> alt.Axis:
    """Recessive axis styling — the data should be louder than the chrome."""
    return alt.Axis(title=title, labelColor=MUTED, titleColor=MUTED,
                    gridColor=GRID, domainColor=GRID, tickColor=GRID)


# --- Page ---------------------------------------------------------------------

measurement, demo, metrics = load_all()

st.title("Ad Ops Command Center")
st.caption(
    "A grounded ad-reporting pipeline. Claude writes the report, a second Claude "
    "call fact-checks every claim against real DuckDB numbers, and unsupported "
    "claims get sent back for a targeted rewrite. This is a **workflow**, not an "
    "agent — the path is fixed in Python; the model fills in the writing and the "
    "checking."
)

st.divider()

# --- The headline -------------------------------------------------------------
# A handful of headline numbers is a KPI row, not a grouped bar chart. The one
# number the dashboard leads with is a hero figure.

st.subheader("Does grounding actually reduce hallucination?")

ungrounded_rate = measurement.hallucination_rate("ungrounded")
grounded_rate = measurement.hallucination_rate("grounded")
n_runs = measurement.n_runs("grounded")

hero_left, hero_right = st.columns([2, 3])
with hero_left:
    st.markdown(
        f"<div style='font-size:4.5rem;line-height:1;font-weight:600'>"
        f"<span style='color:{ORANGE}'>{ungrounded_rate:.0f}%</span>"
        f"<span style='color:{MUTED}'> &rarr; </span>"
        f"<span style='color:{BLUE}'>{grounded_rate:.0f}%</span></div>"
        f"<div style='color:{MUTED};margin-top:.5rem'>"
        f"first-draft hallucination rate, ungrounded &rarr; grounded</div>",
        unsafe_allow_html=True,
    )
with hero_right:
    st.markdown(
        f"Across **{n_runs} date ranges** (14-, 21- and 28-day windows), every "
        f"ungrounded first draft contained at least one claim the data does not "
        f"support. Every grounded first draft was clean.\n\n"
        f"The correction loop then repaired the ungrounded drafts to "
        f"**{measurement.post_loop_rate('ungrounded'):.0f}%** unsupported."
    )

st.text("")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Ungrounded first drafts", f"{ungrounded_rate:.0f}%",
          help="% of reports with at least one unsupported claim")
c2.metric("Grounded first drafts", f"{grounded_rate:.0f}%",
          delta=f"{grounded_rate - ungrounded_rate:.0f} pts", delta_color="inverse",
          help="Same measurement, with the metrics JSON in the prompt")
c3.metric("Ungrounded claims wrong", f"{measurement.claim_level_rate('ungrounded'):.1f}%",
          help="% of individual claims unsupported, not just % of reports")
c4.metric("After correction loop", f"{measurement.post_loop_rate('ungrounded'):.0f}%",
          help="Ungrounded arm, once the pipeline ran")

st.divider()

# --- Every run, so it's visibly not cherry-picked ------------------------------

st.subheader("Every run, not just the good ones")

good_rows = [r for r in measurement.rows if r.error is None]

# One run number per DATE RANGE, shared by both arms — so the two arms line up
# vertically at the same x. (Indexing per row would give 66 x-positions and the
# arms would never sit together.)
range_index = {
    key: i + 1
    for i, key in enumerate(sorted({(r.window_days, r.end_date) for r in good_rows}))
}
runs_df = pd.DataFrame([
    {
        "run": range_index[(r.window_days, r.end_date)],
        "arm": r.arm,
        "unsupported": r.first_draft_unsupported,
        "claims": r.first_draft_claims,
        "window": f"{r.window_days}d ending {r.end_date}",
    }
    for r in good_rows
])

# Dots, not bars: the grounded series is zero everywhere, and a zero-height bar
# is invisible. As dots, grounded forms a visible row along the baseline — you
# can see the series is present and flat, rather than missing.
claims_chart = (
    alt.Chart(runs_df)
    .mark_circle(size=90, opacity=0.95, stroke="#ffffff", strokeWidth=2)
    .encode(
        x=alt.X("run:Q", axis=_axis("date range"),
                scale=alt.Scale(domain=[0.5, len(range_index) + 0.5], nice=False)),
        y=alt.Y("unsupported:Q", axis=_axis("unsupported claims in first draft"),
                scale=alt.Scale(domain=[-0.6, runs_df["unsupported"].max() + 1])),
        color=alt.Color("arm:N",
                        scale=alt.Scale(domain=["grounded", "ungrounded"],
                                        range=[BLUE, ORANGE]),
                        legend=alt.Legend(title=None, orient="top", labelColor=MUTED)),
        tooltip=["window", "arm", "unsupported", "claims"],
    )
    .properties(height=260)
)
st.altair_chart(claims_chart, use_container_width=True)
st.caption(
    f"One column per date range, both arms plotted together. The grounded series "
    f"(blue) sits flat on zero across all {len(range_index)} ranges; the ungrounded "
    f"series (orange) never touches it."
)

st.divider()

# --- The correction loop, replayed --------------------------------------------

st.subheader("The correction loop, on a real run")
st.caption(
    "A saved run seeded with a deliberately wrong draft, so the repair path is "
    "visible. The evaluator's verdicts and the rewrite below are genuine model "
    "output, replayed from disk."
)

for iteration in demo.iterations:
    label = "First draft" if iteration.attempt == 1 else f"Revision {iteration.attempt - 1}"
    n_bad = len(iteration.evaluation.unsupported)
    n_all = len(iteration.evaluation.claims)
    icon = "✅" if n_bad == 0 else "❌"

    with st.expander(f"{icon}  **{label}** — {n_bad} of {n_all} claims unsupported",
                     expanded=True):
        st.write(iteration.report.technical_summary)
        checks = pd.DataFrame([
            {"": "pass" if c.supported else "FAIL",
             "claim": c.claim,
             "what the data says": c.explanation}
            for c in iteration.evaluation.claims
        ])
        st.dataframe(checks, hide_index=True, width="stretch")

st.divider()

# --- The real numbers underneath ----------------------------------------------

st.subheader("The ground truth")
st.caption(
    f"Real Criteo attribution data, {metrics.start_date} to {metrics.end_date}. "
    f"Every claim above was checked against these numbers. Note the CTR: this is "
    f"an attribution-logged sample, so it runs far above real-world CTR."
)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Impressions", f"{metrics.total_impressions:,}")
m2.metric("Clicks", f"{metrics.total_clicks:,}")
m3.metric("Overall CTR", f"{metrics.overall_ctr * 100:.2f}%")
m4.metric("Conversions", f"{metrics.total_conversions:,}")

days_df = pd.DataFrame([d.model_dump() for d in metrics.days])
days_df["date"] = pd.to_datetime(days_df["date"])
days_df["ctr_pct"] = days_df["ctr"] * 100

# CTR and spend are different scales, so they get their own charts.
# A dual-axis chart here would invite a false correlation.
left, right = st.columns(2)
with left:
    st.altair_chart(
        alt.Chart(days_df).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=45))
        .encode(
            x=alt.X("date:T", axis=_axis(None)),
            y=alt.Y("ctr_pct:Q", axis=_axis("daily CTR (%)"),
                    scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("ctr_pct:Q", format=".2f")],
        ).properties(height=220, title="Daily CTR"),
        use_container_width=True,
    )
with right:
    st.altair_chart(
        alt.Chart(days_df).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=45),
                                     color=ORANGE)
        .encode(
            x=alt.X("date:T", axis=_axis(None)),
            y=alt.Y("spend:Q", axis=_axis("daily spend ($)"),
                    scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("spend:Q", format="$.2f")],
        ).properties(height=220, title="Daily spend"),
        use_container_width=True,
    )

with st.expander("How the measurement works"):
    st.markdown(
        f"""
**Two arms, identical pipeline.**

- **Grounded** — the generator gets the full metrics JSON, the way the real
  pipeline runs it.
- **Ungrounded** — the generator gets only the date range and one anchor number
  (total impressions), then is asked for the same detailed report. It has to
  invent CTR, spend, conversions and the week-over-week direction.

The ungrounded arm is deliberately not a strawman. Handing the model nothing at
all would produce a meaningless 100% rate by construction. Giving it one real
number and asking it to fill in the rest is what a naive implementation actually
looks like.

Both arms then run the same evaluator and the same correction loop (capped at
two revisions), so the comparison is like-for-like.

**Three rates, because they answer different questions.** *First-draft* is the
share of reports with at least one bad claim. *Claim-level* is the share of
individual claims that were wrong — severity, not just presence. *After loop* is
what survived the pipeline.

Measured with `{measurement.model}` over {n_runs} date ranges per arm,
{measurement.n_errors()} errors.
"""
    )
