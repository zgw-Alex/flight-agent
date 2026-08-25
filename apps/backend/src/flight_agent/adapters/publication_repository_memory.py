"""In-memory publication repository for M5-U4 composition tests and bootstrap."""

from __future__ import annotations

from flight_agent.application.publication import (
    ConversationReadState,
    PublicWorkflowOutcome,
    PublishedRecommendationRecord,
)
from flight_agent.domain.requirements import RequirementId


class InMemoryPublicationRepository:
    def __init__(self) -> None:
        self._conversations: dict[str, ConversationReadState] = {}

    def save_current(self, record: PublishedRecommendationRecord) -> None:
        self._conversations[record.conversation_id] = ConversationReadState(
            conversation_id=record.conversation_id,
            outcome=PublicWorkflowOutcome.PUBLISHED,
            requirement_id=record.requirement_id,
            requirement_version=record.published_recommendation.based_on_requirement_version.value,
            execution_id=record.published_recommendation.execution_id.value,
            current_published_recommendation=record,
        )

    def record_outcome(
        self,
        *,
        conversation_id: str,
        outcome: PublicWorkflowOutcome,
        requirement_id: RequirementId | None,
        requirement_version: int | None,
        execution_id: str | None,
    ) -> None:
        current = self._conversations.get(conversation_id)
        self._conversations[conversation_id] = ConversationReadState(
            conversation_id=conversation_id,
            outcome=outcome,
            requirement_id=requirement_id,
            requirement_version=requirement_version,
            execution_id=execution_id,
            current_published_recommendation=current.current_published_recommendation
            if current is not None
            else None,
        )

    def get_conversation(self, conversation_id: str) -> ConversationReadState | None:
        return self._conversations.get(conversation_id)
