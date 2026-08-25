"""Public structured requirement entry API for M5-U1."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from flight_agent.application.publication import (
    ConversationReadState,
    PublicationRepository,
    PublicWorkflowOutcome,
    PublishedRecommendationRecord,
)
from flight_agent.application.structured_entry import (
    StartStructuredRequirement,
    StructuredEntryResult,
    StructuredEntryStatus,
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


class PublicPublishedRecommendationResponse(BaseModel):
    publication_id: str
    recommendation_result_id: str
    execution_id: str
    requirement_id: str
    requirement_version: int
    snapshot_id: str
    snapshot_version: int
    published_at: str
    route_origin: str
    route_destination: str
    departure_date: str
    selected_price_amount: str
    selected_price_currency: str
    role: str
    reason: str
    evidence: list[str]


class ConversationReadResponse(BaseModel):
    conversation_id: str
    outcome: str
    requirement_id: str | None
    requirement_version: int | None
    execution_id: str | None
    current_published_recommendation: PublicPublishedRecommendationResponse | None


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
    publication_repository: PublicationRepository | None = None,
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
        result = use_case.start(structured_request_to_command(request))
        if (
            publication_repository is not None
            and result.status is StructuredEntryStatus.NOT_READY
        ):
            publication_repository.record_outcome(
                conversation_id=result.conversation_id,
                outcome=PublicWorkflowOutcome.NOT_READY,
                requirement_id=result.requirement_id,
                requirement_version=result.requirement_version,
                execution_id=result.execution_id,
            )
        return structured_result_to_response(result)

    @router.get(
        "/conversations/{conversation_id}",
        response_model=ConversationReadResponse,
    )
    def read_conversation(conversation_id: str) -> ConversationReadResponse:
        if publication_repository is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation read model unavailable")
        state = publication_repository.get_conversation(conversation_id)
        if state is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        return conversation_state_to_response(state)

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


def conversation_state_to_response(state: ConversationReadState) -> ConversationReadResponse:
    return ConversationReadResponse(
        conversation_id=state.conversation_id,
        outcome=state.outcome.value,
        requirement_id=state.requirement_id.value if state.requirement_id is not None else None,
        requirement_version=state.requirement_version,
        execution_id=state.execution_id,
        current_published_recommendation=_published_to_response(
            state.current_published_recommendation
        ),
    )


def _published_to_response(
    record: PublishedRecommendationRecord | None,
) -> PublicPublishedRecommendationResponse | None:
    if record is None:
        return None
    published = record.published_recommendation
    return PublicPublishedRecommendationResponse(
        publication_id=published.publication_id.value,
        recommendation_result_id=published.recommendation_result_id.value,
        execution_id=published.execution_id.value,
        requirement_id=record.requirement_id.value,
        requirement_version=published.based_on_requirement_version.value,
        snapshot_id=published.snapshot_id.value,
        snapshot_version=published.snapshot_version.value,
        published_at=published.published_at.value.isoformat(),
        route_origin=record.route_origin,
        route_destination=record.route_destination,
        departure_date=record.departure_date.isoformat(),
        selected_price_amount=str(record.selected_price_amount),
        selected_price_currency=record.selected_price_currency,
        role=record.role.value,
        reason=record.reason,
        evidence=[
            f"{evidence.source.value}:{evidence.identity.value}"
            for evidence in record.evidence
        ],
    )
