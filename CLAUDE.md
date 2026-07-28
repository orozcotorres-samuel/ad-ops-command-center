# CLAUDE.md

Working notes for Claude Code sessions in this repo. The README is for humans;
this file is the stuff that isn't obvious from reading the code.

## What this is

A grounded ad-reporting pipeline that measures its own hallucination rate.
Generator writes a report → evaluator fact-checks every claim against DuckDB →
flagged claims go back for a targeted rewrite, capped at 2 revisions.

**Call it a workflow, not an agent.** The control flow is fixed in Python; the
model only writes and checks. The owner cares about this distinction — it's what
makes the hallucination rate measurable. Don't describe it as an autonomous agent.

## Who you're working with

A **coding beginner**. Explain concepts in plain English, go step by step, and
check in between steps rather than dumping a finished pile of code. Commit
locally as you go.

## Running things

**The shell's working directory resets between commands.** Always start with:

```bash
cd /Users/samuelorozco/Developer/ad-ops-command-center && .venv/bin/python src/...
```

Use `.venv/bin/python` and `.venv/bin/streamlit` directly — don't assume the venv
is activated.

```bash
.venv/bin/python src/measure.py --report-only   # free — prints the saved result
.venv/bin/python src/pipeline.py --demo         # correction loop, planted-error draft
.venv/bin/streamlit run src/app.py              # dashboard
```

## Cost discipline — read before running anything that calls the API

**Do not re-run the full sweep casually.** It's ~$4 and ~25 minutes. The measured
result is already committed in `results/hallucination_rate.json`.

- `--report-only` prints the saved numbers with **zero** API calls. Default to this.
- The sweep is **resumable**: it saves after every run and skips completed ones, so
  re-running a finished sweep costs nothing. Only `--force` re-spends.
- Use `--limit 3` for a pilot before any full run.
- The dashboard makes **zero** API calls — iterate on it freely.

## Settled decisions — don't relitigate

- **`claude-opus-4-8` for both generator and evaluator.** A cheaper model would
  save ~$2.50 on the sweep, but the evaluator *is* the measuring instrument —
  downgrading it weakens the headline to "hallucination rate as judged by a
  cheaper fact-checker."
- **The dashboard stays read-only** (no API key deployed, no per-visitor cost).
  A hybrid live-mode toggle is a Phase 2 option; the seam already exists —
  everything renders a `PipelineResult`, which is identical whether loaded from
  disk or returned by `run_pipeline()`. Only the data source would change.
- **The ungrounded arm gets one anchor number** (total impressions), not nothing.
  Handing the model nothing would make the 100% true by construction and
  worthless. Don't "simplify" this.

## Gotchas that have already cost time

- **Python 3.9.** Pydantic models need `Optional[X]`, not `X | None` — the newer
  syntax raises at class-definition time even with `from __future__ import
  annotations`. Plain function signatures are fine either way.
- **Verify Streamlit changes with `AppTest`**, not by checking the server responds:

  ```python
  from streamlit.testing.v1 import AppTest
  at = AppTest.from_file("src/app.py"); at.run(); print(at.exception)
  ```

  `streamlit run` returning HTTP 200 proves nothing — the script only executes
  when a client connects. This caught a real `ModuleNotFoundError`.
- `st.dataframe` takes `width="stretch"`; `st.altair_chart` still takes
  `use_container_width` and **rejects** `width`. They are not interchangeable.
- `src/app.py` must `sys.path.insert(0, ...)` its own directory. `streamlit run`
  adds it implicitly, but `AppTest` and some hosts don't.
- **Screenshots:** headless-Chrome `--screenshot` captures Streamlit's loading
  skeleton, not the page. Drive the DevTools protocol instead (navigate, real
  `asyncio.sleep(~14)`, then `Page.captureScreenshot`), and scroll with
  `element.scrollIntoView()` — `window.scrollTo` doesn't move Streamlit's inner
  container.

## Git rules

**Never `git add` the dataset.** `data/*` is gitignored except `.gitkeep`. A
2.6 GB `.tsv` was once committed because the ignore list covered `.csv/.gz/.txt`
but not `.tsv`; that blob — not the network — was why pushes failed with SSL and
connection-reset errors, and it took several turns to diagnose.

Before every push:

```bash
git ls-files -z | xargs -0 du -h | sort -rh | head -5
git ls-files | grep -iE "\.(tsv|csv|duckdb|gz|parquet)$"   # must be empty
git ls-files | grep -x ".env"                              # must be empty
```

The remote should stay in the tens of KB. `gh` is installed at `~/.local/bin/gh`
(not on PATH). Remote `origin` uses SSH over port 443.

## Data notes

Criteo attribution sample, 500k rows across 2024-01-01..31, in
`data/ad_ops.duckdb` (table `impressions`). Not in the repo — see README for
where to get it.

**CTR runs ~37%.** That's expected: it's an attribution-logged sample, not a
representative impression log. Not a bug, don't "fix" it.

## Status

Phase 1 complete and pushed. Phase 2 is an OpenRTB auction simulator — worth a
design conversation before writing code. Phase 3 connects the two.
