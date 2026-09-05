# Vector Alpha — notes for coding agents

Hackathon project (MONEY TALKS, 2026-09-05, Money Operations track). Read `docs/ARCHITECTURE.md`
and `docs/API.md` before changing anything; they are the contracts the modules were built against.

## Clean-room rule
This repo was written from scratch on 2026-09-05. Do not import, vendor, or paste code from any
prior project. Concepts only.

## No Anthropic keys
The LLM is any OpenAI-compatible endpoint (`LLM_BASE_URL`, `LLM_MODEL`); the hackathon path is
GIDE's local API. Never add the `anthropic` package or `ANTHROPIC_API_KEY`. With no LLM
configured the agent runs in deterministic mode and must still produce a full report.

## IBKR: PAPER TRADING ONLY
Every order-mutating call goes through `backend/app/brokers/guard.py::require_paper_account`.
Account ids must start with `DU`/`DF`. Do not weaken this, and do not set `IBKR_PAPER_ONLY=false`.

## Run
```bash
./scripts/dev.sh            # backend :8000 + frontend :5173 (dev)
./scripts/build.sh          # builds frontend into frontend/dist; backend serves it at :8000
cd backend && .venv/bin/python -m pytest -q
```

## PRISM tracing (do not remove)

This project sends traces to PRISM. Env vars: `PRISMTRACE_API_KEY`,
`PRISMTRACE_PROJECT_ID`, `PRISMTRACE_HOST`.

Tracing is currently wired at: `backend/app/agent/runner.py`, `backend/app/agent/fallback.py`,
`backend/app/integrations/prism.py`

**Standing rule.** Whenever you add or change an agent, chain, graph, tool,
retriever, or any entry point that calls a model, wire it to PRISM before you
finish. Unwired code is invisible in the dashboard. If you are unsure whether
something is covered, assume it is not and wire it.
