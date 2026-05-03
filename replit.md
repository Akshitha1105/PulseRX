# Workspace

## Overview

pnpm workspace monorepo using TypeScript, plus a Python/FastAPI application (PulseRx).

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5 (Node.js api-server)
- **Database (Node)**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)

## Applications

### PulseRx (Python/FastAPI)
- **Location**: `artifacts/pulserx/main.py` — single-file application
- **Port**: 5000
- **Stack**: FastAPI, SQLAlchemy, SQLite, TextBlob, PRAW
- **Workflow**: "PulseRx" — `python artifacts/pulserx/main.py`
- **Database**: SQLite at `artifacts/pulserx/pulserx.db`
- **Features**:
  - Social listening for healthcare/drug safety
  - Reddit ingestion via PRAW (falls back to simulated data without credentials)
  - Sentiment analysis with TextBlob
  - Entity extraction (drugs, symptoms)
  - Adverse event detection and alerting
  - PII masking (emails, phone numbers)
  - Embedded HTML frontend with Chart.js sentiment trend chart

### API Server (Node.js/Express)
- **Location**: `artifacts/api-server/`
- **Port**: 8080, path: `/api`
- **Workflow**: managed by artifact system

### Mockup Sandbox
- **Location**: `artifacts/mockup-sandbox/`
- **Port**: 8081, path: `/__mockup`

## Key Commands

- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)

## Reddit API (Optional)
Set these env vars to use real Reddit data (falls back to simulated posts without them):
- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT` (defaults to "PulseRx/1.0 drug-safety-monitor")

See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details.
