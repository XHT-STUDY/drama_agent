# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DramaAgent is a conversational agent system for Chinese short-drama creation. It is NOT a single-prompt generator — it's a stateful, multi-stage workflow with memory, retrieval, evaluation, revision, versioning, and export capabilities.

**MVP scope**: Idea/Outline/TXT/DOCX → StoryBible → 10-episode outline → 3 full scripts → per-episode evaluation → auto-revise the lowest-scored episode → continuity check + re-evaluation → version diff → Markdown/DOCX export.

## Development Guide

The authoritative development document is [docs/DEV_PLAN.md](docs/DEV_PLAN.md). **Always read relevant sections before starting any task.** Every AI Coding task must follow the execution principles in Section 0.1 of DEV_PLAN.md. The project is brand new — code under `backend/` and `frontend/` does not exist yet and must be created from scratch.

### Task-Based Development

All work is organized by task IDs (A-01 through I-06) defined in DEV_PLAN.md Section 12. Each task card specifies:
- Estimated effort, dependencies, files to modify
- Concrete implementation requirements
- Acceptance criteria (checkbox list)
- Test commands

**Rules for task execution** (from DEV_PLAN.md §0.1):
1. Implement only one task ID at a time
2. Read `## 0. AI Coding 的执行原则`, the current phase section, the task card, and dependency implementations before starting
3. Do NOT refactor across modules or implement future phases without task card authorization
4. Every task must deliver: implementation code, unit/integration tests, config/migrations, doc updates, and reproducible verification commands
5. Task statuses: TODO → DOING → BLOCKED → REVIEW → DONE
6. Mark DONE only when ALL acceptance criteria are met
7. Use **FakeLLM** for automated tests — never call real LLMs in CI
8. All LLM output must pass structured Pydantic v2 schema validation before writing to any Artifact
9. Artifacts are immutable — revisions create new versions, never overwrite
10. Update the progress tracker (Section 13) after every task

### Definition of Done (§0.3)

A task is complete only when ALL of:
- Code matches task boundaries
- All new/modified tests pass
- Ruff, typecheck, frontend lint: zero new errors
- DB changes include Alembic migration
- API changes sync OpenAPI + frontend types
- LLM Schema/Prompt changes include version number + fixed examples
- Logs contain no API keys, full uploads, or complete prompts
- No existing Artifacts overwritten
- Acceptance evidence written to progress table
- No unexplained TODOs left behind

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, Pydantic v2 |
| Workflow | LangGraph (stateful nodes, conditional branches, checkpoint) |
| ORM/Migration | SQLAlchemy 2 (async), Alembic |
| Primary DB | PostgreSQL + pgvector |
| Transient state | Redis (SSE pub/sub, short-term memory, rate limiting) |
| File storage | Local filesystem (interface reserved for object storage) |
| Background exec | In-process Worker abstraction |
| Frontend | Next.js, React, TypeScript |
| Frontend state | TanStack Query + local state |
| Styling | Tailwind CSS |
| Testing | pytest, Vitest, Playwright |
| Tooling | uv, Ruff, mypy, pnpm |

## Common Commands

These are defined in the Makefile (to be created in A-01):

```bash
make install      # Install all dependencies (uv sync + pnpm install)
make lint         # Run all linters (Ruff + ESLint)
make typecheck    # Run all type checkers (mypy + tsc)
make test         # Run all tests
make ci           # Full CI pipeline (lint + typecheck + test)
make up           # Start Docker Compose (PostgreSQL + Redis)
make down         # Stop Docker Compose
make doctor       # Verify environment health (DB, Redis, config)
make e2e          # Run Playwright end-to-end tests
make perf         # Run performance tests
```

Run specific test subsets:
```bash
pytest -m unit                                  # Unit tests only
pytest -m integration                           # Integration tests only
pytest -m workflow                              # Workflow tests only
pytest -m contract                              # Contract tests only
pytest backend/tests/unit/skills/test_story_bible.py  # Single test file
pnpm test -- projects                           # Frontend: run specific test suite
pnpm playwright test                            # E2E tests
```

## Architecture

### Logical Layers (top → bottom, §3.1)

1. **Next.js workspace** → FastAPI API — user interaction, progress, asset display
2. **FastAPI API** → Application Services — params, auth boundary, error contracts
3. **Application Services** → Run Service / Repositories — use-case orchestration, transaction boundaries
4. **Run / Event Service** → LangGraph Workflows — async execution, events, recovery
5. **LangGraph Workflows** → Agents + Skills — state nodes, conditional branches
6. **Agents + Skills** → LLM / RAG / Tools / Memory — single business capability
7. **Repositories** → PostgreSQL + pgvector — persistent state, vector search

Side dependencies: Run Service uses Redis for real-time notifications; Application Services use Local File Store for uploads/exports. Neither replaces PostgreSQL as the source of truth.

### Module Boundaries (§4.1)

| Module | Allowed | Forbidden |
|---|---|---|
| `api` | Param parsing, auth, call application service | Direct LLM calls, direct ORM writes |
| `application` | Use-case orchestration, transaction boundaries | Storing prompt templates, implementing DB details |
| `domain` | Schemas, enums, pure rules | Network, DB, LLM calls |
| `workflows` | Node wiring, state transitions, recovery | Long prompts, raw SQL |
| `agents` | Compose generic Skills, provide business role entry points | Deciding the main workflow freely |
| `skills` | Single reusable task, assemble context + output schema | Direct frontend or HTTP operations |
| `tools` | Deterministic capabilities (stats, parsing, diff) | Implicit LLM calls |
| `repositories` | Data persistence | Business scoring, control flow |
| `artifacts` | Immutable versions, dependencies, diff | Modifying historical versions |

### Key Architectural Decisions (§2.2)

1. **PostgreSQL is the single source of truth** for all persistent state. Redis loss must not cause project asset loss.
2. **pgvector** shares the PostgreSQL instance — no separate Vector DB in MVP.
3. **Artifacts use an immutable version model**. Updates create new records.
4. **LangGraph State stores only IDs and lightweight structures** — large text lives in Artifacts to avoid checkpoint bloat.
5. **BaseAgent** handles generic calling, validation, retries, and tracing. Business logic lives in Skills.
6. **Orchestrator** deterministically selects workflows — agents do not freely converse to decide control flow.
7. **Real LLM and FakeLLM implement the same protocol** — all automated tests default to FakeLLM.
8. **Prompts are versioned code assets**. Every generation records prompt_version, model, params, and input Artifact IDs.
9. **API returns `run_id` immediately** for long tasks; progress observed via SSE.
10. **Auto-revision selects by deterministic code**: lowest `overall_score`; tiebreak by smallest `episode_number`.
11. **Evaluation scores are signals only** — revision acceptance also checks structure, continuity, locked facts, and compliance risks.

### Domain Model Rules (§5.1)

- IDs use UUID; times use UTC with ISO 8601 output
- All schemas set `extra=forbid`
- `episode_number` starts at 1; score range 0–100
- JSONB business content must pass Pydantic schema validation before storage
- Artifact content is never UPDATEd; status only transitions `draft → valid` or `draft → invalid`
- `content_schema_version` and `prompt_version` are tracked separately
- All list fields return explicit empty arrays, never null
- Body text allows Markdown but must never execute HTML/scripts server-side

### Creation Workflow (§7.2)

```
normalize → retrieve → story_bible → outline → write episodes 1..3
→ evaluate episodes 1..3 → select + plan → revise
→ continuity_check → re-evaluate → done
```

Each node writes `node.started` / `node.completed` events. Node failures follow retry policy; after exhausting retries, write `node.failed` + `run.failed`. Already-created Artifacts are never rolled back or deleted.

### Model Roles (§2.3)

| Role | Default Use | Required Output |
|---|---|---|
| `normalizer` | Requirement normalization, file classification | NormalizedRequirement / ImportClassification |
| `planner` | StoryBible, episode outlines | StoryBible / EpisodeOutlineSet |
| `writer` | Single-episode scripts, revision | ScriptDraft / RevisedScript |
| `evaluator` | Scoring, issue diagnosis, revision planning | EvaluationReport / RevisionPlan |
| `summarizer` | Conversation & episode summaries | ConversationSummary / EpisodeSummary |
| `embedding` | Knowledge base vectors | float vector |

Constraints: max 2 retries on structured output failure, 180s node timeout, soft cap of 18 LLM calls per complete Demo run.

### FakeLLM Rules (§10.2)

All automated tests use FakeLLM which:
- Returns legal objects from fixtures/golden by `prompt_name`
- Can be configured to timeout, rate-limit, or output invalid JSON at specific call indices
- Records call sequence and input Artifact IDs
- Supports deterministic seeding
- E2E fixed data must include one low-scored episode to ensure the revision branch is exercised

## Project Status

Currently at **Phase A** (Engineering baseline) — all tasks are TODO. The repository has only the DEV_PLAN.md, README.md, LICENSE, .gitignore, and .vscode/settings.json. No backend or frontend code exists yet.

The first task is **A-01**: Initialize monorepo with dev commands (Makefile, pyproject.toml, package.json, empty test suites).

## Environment Setup

```bash
cp .env.example .env    # Edit as needed (do NOT commit .env)
make install            # Install all dependencies
make up                 # Start PostgreSQL + Redis via Docker Compose
make doctor             # Verify everything is healthy
```

Required services for local development: Docker Compose (PostgreSQL 15+ with pgvector, Redis 7+).

## Key Constraints

- MVP outline count: exactly 10 episodes; script count: first 3 episodes
- MAX_REVISION_ROUNDS=1 (never auto-revise more than once)
- Upload max: 10 MB; TXT/DOCX only
- API p95 < 300ms (excluding LLM calls); first SSE event within 1s of Run creation
- Core domain/workflow/artifact test coverage ≥ 85%; overall backend ≥ 75%
- `.env` never committed; `.env.example` contains no real keys
- All configurable results-affecting values written to `WorkflowRun.config_snapshot`
- Function and class comments in Chinese ( explain intent, not restate code)
