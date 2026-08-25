"""Public structured requirement entry API for M5-U1."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from flight_agent.application.structured_entry import (
    StartStructuredRequirement,
    StructuredEntryResult,
    StructuredRequirementCommand,
)


class StructuredRequirementRequest(BaseModel):
    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_date: date | None = None
    max_price_cny: int | None = Field(default=None, ge=1)
    lower_price_preferred: bool = False


class StructuredRequirementResponse(BaseModel):
    conversation_id: str
    execution_id: str
    requirement_id: str | None
    requirement_version: int | None
    status: str
    search_readiness: str | None
    downstream_search_eligible: bool
    validation_issues: list[str]


def structured_request_to_command(
    request: StructuredRequirementRequest,
) -> StructuredRequirementCommand:
    return StructuredRequirementCommand(
        origin=request.origin,
        destination=request.destination,
        departure_date=request.departure_date,
        max_price_cny=request.max_price_cny,
        lower_price_preferred=request.lower_price_preferred,
        source_input="public-structured-requirement",
    )


def create_structured_entry_router(
    use_case: StartStructuredRequirement,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/conversations",
        response_model=StructuredRequirementResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def start_conversation(
        request: StructuredRequirementRequest,
    ) -> StructuredRequirementResponse:
        return structured_result_to_response(use_case.start(structured_request_to_command(request)))

    return router


def structured_result_to_response(result: StructuredEntryResult) -> StructuredRequirementResponse:
    validation = result.pipeline_outcome.validation
    return StructuredRequirementResponse(
        conversation_id=result.conversation_id,
        execution_id=result.execution_id,
        requirement_id=result.requirement_id.value if result.requirement_id is not None else None,
        requirement_version=result.requirement_version,
        status=result.status.value,
        search_readiness=result.readiness.value if result.readiness is not None else None,
        downstream_search_eligible=result.downstream_search_eligible,
        validation_issues=[issue.code.value for issue in validation.issues]
        if validation is not None
        else [],
    )
