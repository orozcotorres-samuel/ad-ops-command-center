# Ad Ops Command Center

A grounded ad-reporting pipeline with a built-in AI fact-checker.

Two Claude-powered agents work together:
- a **generator** that writes plain-English + technical summaries of ad-performance metrics, and
- an **evaluator** that checks every factual claim in the summary against the real numbers in a DuckDB database and flags anything unsupported.

The headline result is a measured **hallucination rate**: what % of first-draft
summaries contained an unsupported claim, before vs. after the evaluator's grounding.

## Status
Phase 1 — in progress.

## Tech
Python · Anthropic Claude API · pandas · DuckDB · Pydantic · Streamlit

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste your Anthropic API key into .env
```
