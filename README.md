# Flight Agent

Flight Agent is a V0 flight filtering agent for outbound flights from mainland China. This repository is entering the Third Stage M0 Development Baseline, starting with M0-U1 Repository Bootstrap.

## Project Status

- Stage: Third Stage implementation
- Milestone: M0 Development Baseline
- Unit: M0-U1 Repository Bootstrap
- Current state: repository identity, governance files, documentation authority, and minimal frontend/backend project metadata only

## Scope

V0 focuses on a flight filtering agent for outbound flights from mainland China. M0-U1 does not implement product behavior, runtime services, API endpoints, frontend pages, persistence, provider integrations, LLM integrations, ranking, recommendation, or orchestration.

## Architecture Overview

This repository is a monorepo. The frontend and backend are separate applications under `apps/`.

- `apps/backend/` contains the backend Python project identity. The backend target architecture is a Modular Monolith with Domain, Application, Ports, Adapters, Infrastructure, API, and bootstrap boundaries, but those runtime modules are not initialized in M0-U1.
- `apps/frontend/` contains the frontend project identity. The frontend target architecture is React and TypeScript, but runtime tooling is not initialized in M0-U1.
- PostgreSQL is the future formal persistence target. Database configuration, SQLAlchemy, Alembic, schemas, and migrations are outside M0-U1.

## Repository Structure

```text
flight-agent/
|-- apps/
|   |-- backend/
|   |   `-- pyproject.toml
|   `-- frontend/
|       `-- package.json
|-- docs/
|   `-- DOCUMENT_AUTHORITY.md
|-- project-docs/
|   `-- authoritative project planning and reference documents
|-- .editorconfig
|-- .env.example
|-- .gitignore
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

## Development

Install, run, lint, type-check, and test commands will be introduced by later M0 units. M0-U1 intentionally does not add backend runtime dependencies, frontend runtime dependencies, CI workflows, or toolchain scripts.

## Document Authority

Read [docs/DOCUMENT_AUTHORITY.md](docs/DOCUMENT_AUTHORITY.md) before using project documents as implementation authority.
