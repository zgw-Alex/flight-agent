from __future__ import annotations

from datetime import UTC, date, datetime

from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.application import (
    NormalizationContext,
    SearchEligibleRequirement,
    SearchReadinessStatus,
    StartStructuredRequirement,
    StructuredEntryStatus,
    StructuredRequirementCommand,
    structured_command_to_initial_proposal,
)
from flight_agent.application.requirement_pipeline import RequirementPipelineOutcomeStatus
from flight_agent.domain.requirements import (
    ConstraintScope,
    PreferenceScope,
    RequirementId,
    RequirementState,
)
from flight_agent.domain.shared import DomainInstant


def test_u1_gs_ready_structured_entry_reuses_m3_and_marks_search_eligible_once() -> None:
    repository = InMemoryRequirementRepository()
    eligible_calls: list[SearchEligibleRequirement] = []
    use_case = use_case_with(repository=repository, eligible_calls=eligible_calls)

    result = use_case.start(
        StructuredRequirementCommand(
            origin="PEK",
            destination="SHA",
            departure_date=date(2026, 9, 1),
            max_price_cny=1200,
            lower_price_preferred=True,
        )
    )

    assert result.status is StructuredEntryStatus.SEARCH_ELIGIBLE
    assert result.pipeline_outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert result.pipeline_outcome.requirement is not None
    assert not isinstance(
        structured_command_to_initial_proposal(
            StructuredRequirementCommand("PEK", "SHA", date(2026, 9, 1))
        ),
        RequirementState,
    )
    assert result.requirement_id == RequirementId("requirement-1")
    assert result.requirement_version == 1
    assert repository.get_current(RequirementId("requirement-1")) == result.pipeline_outcome.requirement
    assert result.readiness is SearchReadinessStatus.READY
    assert result.downstream_search_eligible is True
    assert len(eligible_calls) == 1
    eligible = eligible_calls[0]
    assert eligible.requirement_id == RequirementId("requirement-1")
    assert eligible.requirement_version == 1


def test_u1_gs_not_ready_commits_requirement_and_does_not_call_downstream_search() -> None:
    repository = InMemoryRequirementRepository()
    eligible_calls: list[SearchEligibleRequirement] = []
    use_case = use_case_with(repository=repository, eligible_calls=eligible_calls)

    result = use_case.start(
        StructuredRequirementCommand(
            origin="PEK",
            destination="SHA",
            departure_date=None,
            max_price_cny=1200,
            lower_price_preferred=True,
        )
    )

    assert result.status is StructuredEntryStatus.NOT_READY
    assert result.pipeline_outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert result.requirement_id == RequirementId("requirement-1")
    assert result.requirement_version == 1
    assert result.readiness is SearchReadinessStatus.NOT_READY
    assert result.downstream_search_eligible is False
    assert eligible_calls == []


def test_structured_command_maps_only_to_non_authoritative_m3_proposal_boundary() -> None:
    proposal = structured_command_to_initial_proposal(
        StructuredRequirementCommand(
            origin="PEK",
            destination="SHA",
            departure_date=date(2026, 9, 1),
            max_price_cny=1200,
            lower_price_preferred=True,
        )
    )

    assert not isinstance(proposal, RequirementState)
    assert not hasattr(proposal, "version")
    assert {constraint.scope for constraint in proposal.constraints} == {
        ConstraintScope.ORIGIN_AIRPORT,
        ConstraintScope.DESTINATION_AIRPORT,
        ConstraintScope.DEPARTURE_DATE,
    }
    assert [preference.scope for preference in proposal.preferences] == [PreferenceScope.PRICE]


def use_case_with(
    *,
    repository: InMemoryRequirementRepository,
    eligible_calls: list[SearchEligibleRequirement],
) -> StartStructuredRequirement:
    ids = iter(
        (
            "conversation-1",
            "execution-1",
            "requirement-1",
            "operation-1",
        )
    )
    return StartStructuredRequirement(
        repository=repository,
        normalization_context=NormalizationContext(
            reference_instant=instant(),
            timezone="Asia/Shanghai",
            locale="zh-CN",
            reference_data_version="test-v1",
        ),
        recorded_at=instant,
        id_factory=lambda: next(ids),
        on_search_eligible=eligible_calls.append,
    )


def instant() -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 25, 8, 0, tzinfo=UTC))
