# Ad Ops Command Center

A grounded ad-reporting pipeline that **measures its own hallucination rate**.

Claude writes an ad-performance report. A second Claude call fact-checks every
claim in it against real numbers in DuckDB. Any claim the data doesn't support
goes back to the writer with the real value attached, for a targeted rewrite.

📄 **[Visual explainer](docs/how-it-works.html)** — how it works in plain English,
then the architecture, experimental design and limitations. Open the file locally,
or view it published: https://claude.ai/code/artifact/a91ed742-878f-4bc5-b286-441c94c7edad

## The result

Measured over **33 date ranges** (14-, 21- and 28-day windows), 66 pipeline runs,
zero errors:

| | First-draft hallucination rate | Claims unsupported | After correction loop |
|---|---|---|---|
| **Ungrounded** | **100%** | 85.8% | 0% |
| **Grounded** | **0%** | 0% | 0% |

Every ungrounded first draft contained at least one claim the data doesn't
support, and roughly six of every seven individual claims were wrong. Every
grounded first draft was clean. The correction loop then repaired the ungrounded
drafts to zero unsupported claims.

Full per-run results: [`results/hallucination_rate.json`](results/hallucination_rate.json).

### What "ungrounded" means here

This matters for reading the 100% honestly. The ungrounded arm is **not** a
strawman — handing the model nothing at all would produce 100% by construction
and prove nothing.

Instead, the ungrounded generator gets the date range plus **one real anchor
number** (total impressions), then is asked for the same detailed report. It has
to supply CTR, spend, conversions, and the week-over-week direction itself. That
is what a naive implementation actually looks like: someone knows the headline
volume and asks an LLM to write up the rest.

Both arms then run the **identical** evaluator and correction loop, so the
comparison is like-for-like.

## This is a workflow, not an agent

The control flow is fixed in Python: generate → check → maybe rewrite → stop,
capped at two revisions so it can never loop forever. Claude doesn't decide what
happens next; it does the writing and the checking. That's deliberate — a fixed
path is what makes the hallucination rate measurable in the first place.

## How it fits together

```
DuckDB ──► metrics.py ──► generator.py ──► evaluator.py
           (ground        (writes the      (checks every claim
            truth)         report)          against ground truth)
                               ▲                    │
                               └──── pipeline.py ◄──┘
                                 (sends flagged claims
                                  back with real values)
```

| File | What it does |
|---|---|
| `src/load_data.py` | Loads a 500k-row sample of the Criteo attribution dataset into DuckDB |
| `src/metrics.py` | Computes daily + week-over-week metrics as a strict Pydantic object — the ground truth |
| `src/generator.py` | The writer. Structured output: a technical and a plain-English summary |
| `src/evaluator.py` | The fact-checker. Extracts each claim and marks it supported or not, with the real value |
| `src/pipeline.py` | The correction loop that wires the two together |
| `src/measure.py` | The two-arm experiment that produces the number above |
| `src/app.py` | Read-only Streamlit dashboard |

## Dashboard

```bash
.venv/bin/streamlit run src/app.py
```

Read-only by design: **zero API calls, no database at runtime.** It replays saved
results from `results/`, so it's free to host and needs no API key deployed.
Everything on the page is real model output — replayed, not regenerated, and not
mocked.

## Reproducing the measurement

The sweep is **resumable**. Completed runs are saved after every single run and
skipped on the next invocation, so an interrupted run loses at most the one run
in flight, and re-running a finished sweep makes zero API calls.

```bash
.venv/bin/python src/measure.py --report-only   # free — print the saved numbers
.venv/bin/python src/measure.py --limit 3       # cheap pilot: 3 date ranges
.venv/bin/python src/measure.py                 # resume; skips what's done
.venv/bin/python src/measure.py --force         # re-run everything (~$4)
```

Run the correction loop on its own, with a deliberately wrong first draft so the
repair path is visible:

```bash
.venv/bin/python src/pipeline.py --demo
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # paste your Anthropic API key into .env
```

The dataset isn't in this repo (it's 2.6 GB). Get the Criteo attribution data
from the [Kaggle mirror](https://www.kaggle.com/datasets/sharatsachin/criteo-attribution-modeling),
put it at `data/pcb_dataset_final.tsv`, then:

```bash
.venv/bin/python src/load_data.py
```

## Notes and caveats

- **CTR runs ~37%.** Expected, not a bug: this is an attribution-logged sample,
  not a representative impression log, so real-world CTR is far lower. The
  pipeline doesn't care — it checks claims against whatever the data says.
- **Measured with `claude-opus-4-8`** for both the generator and the evaluator.
  The evaluator is the measuring instrument, so it deliberately isn't run on a
  cheaper model — that would weaken the claim to "hallucination rate as judged by
  a cheaper fact-checker."
- **The grounded arm's 0% is a ceiling, not proof of perfection.** It means the
  evaluator found nothing unsupported across 33 runs on this dataset — not that
  grounding makes hallucination impossible.

## Roadmap

- **Phase 1 — done.** Grounded reporting pipeline, measured hallucination rate, dashboard.
- **Phase 2.** OpenRTB auction simulator.
- **Phase 3.** Connect the two.

## Tech

Python · Anthropic Claude API · DuckDB · Pydantic · pandas · Altair · Streamlit
