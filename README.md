# Flight Agent

Flight Agent is a V0 flight filtering agent for outbound flights from mainland China. This repository is completing the Third Stage M0 Development Baseline.

## Project Status

- Stage: Third Stage implementation
- Milestone: M0 Development Baseline
- Unit: M0-U5 CI / Unified Development Baseline
- Current state: repository identity, backend toolchain, frontend toolchain, PostgreSQL configuration baseline, local CI wrappers, and GitHub Actions baseline

## Scope

V0 focuses on a flight filtering agent for outbound flights from mainland China. M0 establishes the reproducible development baseline only. It does not implement product behavior, persistence workflows, provider integrations, LLM integrations, ranking, recommendation, or orchestration.

## Architecture Overview

This repository is a monorepo. The frontend and backend are separate applications under `apps/`.

- `apps/backend/` contains the Python FastAPI backend baseline, managed with `uv`, Ruff, Pyright, and pytest.
- `apps/frontend/` contains the React and TypeScript frontend baseline, managed with `pnpm`, ESLint, TypeScript, Vitest, and Vite.
- PostgreSQL is the formal local persistence target for later units. M0 provides Docker Compose configuration and typed settings only; SQLAlchemy, Alembic, schemas, migrations, repositories, and business persistence are not implemented in M0.

## Repository Structure

```text
flight-agent/
|-- apps/
|   |-- backend/
|   |   |-- pyproject.toml
|   |   `-- src/
|   `-- frontend/
|       |-- package.json
|       `-- src/
|-- scripts/
|   `-- ci/
|       |-- backend.ps1
|       |-- frontend.ps1
|       |-- postgres.ps1
|       `-- all.ps1
|-- .github/
|   `-- workflows/
|       `-- ci.yml
|-- docs/
|   `-- DOCUMENT_AUTHORITY.md
|-- project-docs/
|   `-- authoritative project planning and reference documents
|-- .editorconfig
|-- .env.example
|-- .gitignore
|-- compose.yaml
`-- README.md
```

## Architecture Rules

- Domain code must not depend on FastAPI, SQLAlchemy, HTTP clients, provider SDKs, LLM SDKs, or frontend code.
- Application code depends on Domain and Ports.
- Adapters implement Ports; bootstrap or composition root code wires concrete implementations.
- API DTOs, Domain models, and persistence models remain separate.
- Secrets must not be committed to Git. Use local `.env` files and keep `.env.example` as a non-secret template.
- Generated code is not manually edited.
- Stable Domain semantics, Ports, Public APIs, Workflow State, Schema/Migration, Architecture Rules, and Security Policy require an approved Contract Amendment before change.

## Development Quick Start

### Prerequisites

- Git
- Docker Desktop or a compatible Docker Compose runtime
- Python 3.12 or newer
- `uv`
- Node.js 24 or a compatible current Node.js runtime
- `pnpm` 10 or newer
- PowerShell 7+ recommended for local wrapper parity with CI

### Fresh clone

```powershell
git clone https://github.com/zgw-Alex/flight-agent.git
cd flight-agent
Copy-Item .env.example .env
```

The local PostgreSQL container binds `127.0.0.1:55432` on the host to PostgreSQL `5432` in the container. This avoids local PostgreSQL services commonly using `5432` or `5433`.

### Install dependencies

```powershell
cd apps/backend
uv sync --frozen

cd ..\frontend
pnpm install --frozen-lockfile
```

### Start local PostgreSQL

```powershell
cd ..\..
docker compose up -d postgres
docker compose ps
docker compose exec -T postgres pg_isready -U flight_agent -d flight_agent
```

Stop the local project infrastructure without deleting the named volume:

```powershell
docker compose down
```

Do not use `docker compose down -v` as the default local cleanup command.

### Run the backend

```powershell
cd apps/backend
uv run uvicorn flight_agent.bootstrap.app:app --host 127.0.0.1 --port 8000
```

Health check:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/healthz -UseBasicParsing
```

### Run the frontend

```powershell
cd apps/frontend
pnpm dev -- --host 127.0.0.1 --port 5173
```

Open or check:

```powershell
Invoke-WebRequest http://127.0.0.1:5173/ -UseBasicParsing
```

### Unified baseline checks

Run each baseline independently:

```powershell
.\scripts\ci\backend.ps1
.\scripts\ci\frontend.ps1
.\scripts\ci\postgres.ps1
```

Run the aggregate M0 baseline:

```powershell
.\scripts\ci\all.ps1
```

The aggregate script reports every required baseline and exits non-zero if any required check fails.

## Document Authority

Read [docs/DOCUMENT_AUTHORITY.md](docs/DOCUMENT_AUTHORITY.md) before using project documents as implementation authority.
