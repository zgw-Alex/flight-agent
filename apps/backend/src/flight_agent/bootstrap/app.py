"""FastAPI application entrypoint for the backend."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI

from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.api.health import router as health_router
from flight_agent.api.structured_entry import create_structured_entry_router
from flight_agent.application import NormalizationContext
from flight_agent.application.structured_entry import StartStructuredRequirement
from flight_agent.domain.shared import DomainInstant


def create_app() -> FastAPI:
    """Create the backend ASGI application and wire outer transport routes."""
    app = FastAPI(title="Flight Agent Backend")
    structured_entry = StartStructuredRequirement(
        repository=InMemoryRequirementRepository(),
        normalization_context=NormalizationContext(
            reference_instant=DomainInstant(datetime.now(UTC)),
            timezone="Asia/Shanghai",
            locale="zh-CN",
            reference_data_version="bootstrap-v1",
        ),
        recorded_at=lambda: DomainInstant(datetime.now(UTC)),
        id_factory=lambda: str(uuid4()),
    )
    app.include_router(health_router)
    app.include_router(create_structured_entry_router(structured_entry))

    return app


app = create_app()
