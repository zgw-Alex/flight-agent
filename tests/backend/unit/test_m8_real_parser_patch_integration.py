from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.application import (
    LLMBackedCapabilityMetadata,
    LLMCapabilityInvocationMetadata,
    LLMRequirementInterpreter,
    NormalizationContext,
    RequirementPipelineOutcomeStatus,
    execute_llm_initial_requirement,
    execute_llm_patch_requirement,
    initial_requirement_proposal_from_json,
    patch_requirement_proposal_from_json,
)
from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    LocalTime,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    RequirementState,
    SoftPreference,
    ValueRange,
)
from flight_agent.domain.shared import DomainInstant, RequirementVersion
from flight_agent.ports import (
    CapabilityFailure,
    CapabilityFailureKind,
    CapabilityGenerationMetadata,
    CapabilityResult,
    CapabilitySemanticValidation,
    InitialRequirementInterpretationRequest,
    InitialRequirementProposal,
    LLMCapabilityName,
    PatchProposalAction,
    PatchProposalOperation,
    PatchRequirementProposal,
    PatchUnderstandingRequest,
)
from flight_agent.ports.llm_invocation import LLMInvocationId


def test_real_parser_bridge_commits_only_after_m3_validation() -> None:
    repository = InMemoryRequirementRepository()
    interpreter = llm_interpreter(
        initial=QueueInitialCapability(
            (
                CapabilityResult.success(
                    metadata(LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION),
                    clear_initial_proposal(),
                    CapabilitySemanticValidation(is_semantically_valid=True),
                ),
            )
        )
    )

    result = execute_llm_initial_requirement(
        repository=repository,
        interpreter=interpreter,
        source_input="从 PEK 到 SHA，2026-09-01 出发",
        normalization_context=normalization_context(),
        requirement_id=RequirementId("requirement-1"),
        operation_id="u4-parser-commit",
        recorded_at=instant(1),
    )

    assert result.pipeline_outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert result.pipeline_outcome.requirement == repository.get_current(RequirementId("requirement-1"))
    assert result.invocation_metadata is not None
    assert result.invocation_metadata.validation_outcome == "NOT_RUN"


def test_parser_ambiguity_failure_and_semantic_invalid_do_not_mutate() -> None:
    cases = (
        CapabilityResult.ambiguous(
            metadata(LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION),
            InitialRequirementProposal(
                source_input="去上海",
                ambiguity_reasons=("MISSING_ORIGIN_DATE",),
            ),
            CapabilitySemanticValidation(
                is_semantically_valid=False,
                issues=semantic_issue("AMBIGUOUS"),
            ),
        ),
        CapabilityResult.failure_result(
            metadata(LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION),
            CapabilityFailure(
                CapabilityFailureKind.PROVIDER_TRANSPORT_FAILURE,
                "TIMEOUT",
                "simulated timeout",
            ),
        ),
        CapabilityResult.failure_result(
            metadata(LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION),
            CapabilityFailure(
                CapabilityFailureKind.SEMANTIC_INVALID,
                "EMPTY_PROPOSAL",
                "no semantic content",
            ),
        ),
    )

    for index, capability_result in enumerate(cases, start=1):
        repository = InMemoryRequirementRepository()
        result = execute_llm_initial_requirement(
            repository=repository,
            interpreter=llm_interpreter(initial=QueueInitialCapability((capability_result,))),
            source_input=f"case-{index}",
            normalization_context=normalization_context(),
            requirement_id=RequirementId("requirement-1"),
            operation_id=f"u4-parser-no-mutation-{index}",
            recorded_at=instant(1),
        )

        assert result.pipeline_outcome.status in {
            RequirementPipelineOutcomeStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT,
            RequirementPipelineOutcomeStatus.INTERPRETATION_FAILED,
        }
        assert repository.get_current(RequirementId("requirement-1")) is None


def test_json_mapping_covers_hard_soft_and_patch_lineage() -> None:
    initial = initial_requirement_proposal_from_json(
        {
            "constraints": [
                constraint_json("constraint-origin", "ORIGIN", "EQ", "PEK"),
                constraint_json(
                    "constraint-max-price",
                    "MAX_PRICE",
                    "AT_OR_BEFORE",
                    {"amount": "1200", "currency": "CNY"},
                ),
            ],
            "preferences": [
                {
                    "preference_id": "preference-morning",
                    "scope": "DEPARTURE_TIME",
                    "importance": "HIGH",
                    "value": {"start": "08:00:00", "end": "11:00:00"},
                }
            ],
            "unresolved_semantics": [],
            "source_input": "synthetic",
            "evidence": [],
            "ambiguity_reasons": [],
            "insufficient_context": [],
        }
    )
    patch_request = PatchUnderstandingRequest(
        user_message="改成从 SHA 出发",
        requirement_id=RequirementId("requirement-1"),
        based_on_requirement_version=RequirementVersion(1),
        current_requirement_projection="constraint_ids=constraint-origin",
    )
    patch = patch_requirement_proposal_from_json(
        {
            "operations": [
                {
                    "action": "REPLACE_CONSTRAINT",
                    "target_id": "constraint-origin",
                    "item": {
                        "constraint-origin": constraint_json(
                            "proposal-origin",
                            "HardConstraint",
                            "EQUALS",
                            "SHA",
                        )
                    },
                }
            ],
            "unresolved_semantics": [],
            "source_input": "synthetic patch",
            "based_on_requirement_id": "requirement-1",
            "based_on_requirement_version": 1,
            "evidence": [],
            "ambiguity_reasons": [],
            "insufficient_context": [],
        },
        patch_request,
    )

    assert initial.constraints[1].value == Money(Decimal(1200), "CNY")
    assert initial.preferences[0].value == ValueRange(LocalTime(time(8)), LocalTime(time(11)))
    assert patch.based_on_requirement_id == RequirementId("requirement-1")
    assert patch.based_on_requirement_version == RequirementVersion(1)


def test_real_patch_bridge_preserves_untouched_fields_commits_then_triggers_m7_seam() -> None:
    repository, v1 = committed_ready_requirement()
    downstream_calls: list[RequirementState] = []
    patch = patch_proposal(
        PatchProposalOperation(
            PatchProposalAction.REPLACE_PREFERENCE,
            target_id=PreferenceId("preference-price"),
            item=price_preference(PreferenceImportance.LOW),
        )
    )

    result = execute_llm_patch_requirement(
        repository=repository,
        interpreter=llm_interpreter(patch=QueuePatchCapability((capability_success(patch),))),
        source_input="价格偏好降为普通即可",
        normalization_context=normalization_context(),
        requirement_id=v1.requirement_id,
        operation_id="u4-patch-commit",
        recorded_at=instant(2),
        on_patch_committed=downstream_calls.append,
    )

    assert result.pipeline_outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert result.pipeline_outcome.requirement is not None
    assert result.pipeline_outcome.requirement.constraints == v1.constraints
    assert result.pipeline_outcome.requirement.preferences[0].importance is PreferenceImportance.LOW
    assert result.downstream_invoked is True
    assert downstream_calls == [result.pipeline_outcome.requirement]


def test_patch_failures_and_ambiguous_targets_do_not_mutate_or_trigger_m7() -> None:
    cases = (
        CapabilityResult.failure_result(
            metadata(LLMCapabilityName.PATCH_UNDERSTANDING),
            CapabilityFailure(CapabilityFailureKind.SCHEMA_INVALID, "SCHEMA_INVALID", "bad JSON"),
        ),
        CapabilityResult.ambiguous(
            metadata(LLMCapabilityName.PATCH_UNDERSTANDING),
            PatchRequirementProposal(
                unresolved_semantics=("which constraint",),
                based_on_requirement_id=RequirementId("requirement-1"),
                based_on_requirement_version=RequirementVersion(1),
                source_input="改那个",
            ),
            CapabilitySemanticValidation(
                is_semantically_valid=False,
                issues=semantic_issue("AMBIGUOUS_PATCH"),
            ),
        ),
    )

    for index, capability_result in enumerate(cases, start=1):
        repository, v1 = committed_ready_requirement()
        downstream_calls: list[RequirementState] = []

        result = execute_llm_patch_requirement(
            repository=repository,
            interpreter=llm_interpreter(patch=QueuePatchCapability((capability_result,))),
            source_input=f"patch-failure-{index}",
            normalization_context=normalization_context(),
            requirement_id=v1.requirement_id,
            operation_id=f"u4-patch-no-mutation-{index}",
            recorded_at=instant(2),
            on_patch_committed=downstream_calls.append,
        )

        assert result.pipeline_outcome.status in {
            RequirementPipelineOutcomeStatus.INTERPRETATION_FAILED,
            RequirementPipelineOutcomeStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT,
        }
        assert repository.history(v1.requirement_id) == (v1,)
        assert downstream_calls == []


def test_stale_patch_race_rejects_without_rebase_or_m7_execution() -> None:
    repository, v1 = committed_ready_requirement()
    downstream_calls: list[RequirementState] = []

    def competing_commit() -> None:
        execute_llm_patch_requirement(
            repository=repository,
            interpreter=llm_interpreter(
                patch=QueuePatchCapability((capability_success(replace_origin_patch("SHA")),))
            ),
            source_input="先改成虹桥",
            normalization_context=normalization_context(),
            requirement_id=v1.requirement_id,
            operation_id="u4-race-winner",
            recorded_at=instant(2),
        )

    stale_capability = HookedPatchCapability(
        before_return=competing_commit,
        result=capability_success(replace_origin_patch("HKG")),
    )

    result = execute_llm_patch_requirement(
        repository=repository,
        interpreter=llm_interpreter(patch=stale_capability),
        source_input="改成香港",
        normalization_context=normalization_context(),
        requirement_id=v1.requirement_id,
        operation_id="u4-race-stale",
        recorded_at=instant(3),
        on_patch_committed=downstream_calls.append,
    )

    current = repository.get_current(v1.requirement_id)
    assert result.pipeline_outcome.status is RequirementPipelineOutcomeStatus.CONCURRENCY_CONFLICT
    assert current is not None
    assert current.version == RequirementVersion(2)
    assert airport_value(current.constraints[0]) == "SHA"
    assert len(repository.history(v1.requirement_id)) == 2
    assert downstream_calls == []
    assert result.invocation_metadata is not None
    assert result.invocation_metadata.stale_outcome == "STALE_REJECTED"


def test_fake_and_real_capabilities_share_the_same_business_path() -> None:
    fake_repository = InMemoryRequirementRepository()
    real_repository = InMemoryRequirementRepository()
    fake_result = execute_llm_initial_requirement(
        repository=fake_repository,
        interpreter=llm_interpreter(initial=QueueInitialCapability((capability_success(clear_initial_proposal()),))),
        source_input="same",
        normalization_context=normalization_context(),
        requirement_id=RequirementId("requirement-1"),
        operation_id="fake-path",
        recorded_at=instant(1),
    )
    real_like_result = execute_llm_initial_requirement(
        repository=real_repository,
        interpreter=llm_interpreter(initial=QueueInitialCapability((capability_success(clear_initial_proposal()),))),
        source_input="same",
        normalization_context=normalization_context(),
        requirement_id=RequirementId("requirement-1"),
        operation_id="real-path",
        recorded_at=instant(1),
    )

    assert fake_result.pipeline_outcome.status is real_like_result.pipeline_outcome.status
    assert fake_repository.get_current(RequirementId("requirement-1")) is not None
    assert real_repository.get_current(RequirementId("requirement-1")) is not None


def test_telemetry_lineage_is_redacted_and_context_stays_minimal() -> None:
    interpreter = llm_interpreter(initial=QueueInitialCapability((capability_success(clear_initial_proposal()),)))
    result = execute_llm_initial_requirement(
        repository=InMemoryRequirementRepository(),
        interpreter=interpreter,
        source_input="synthetic private text",
        normalization_context=normalization_context(),
        requirement_id=RequirementId("requirement-1"),
        operation_id="u4-telemetry",
        recorded_at=instant(1),
    )

    assert result.invocation_metadata is not None
    metadata_repr = repr(result.invocation_metadata)
    assert "synthetic private text" not in metadata_repr
    assert "Authorization" not in metadata_repr
    assert "secret" not in metadata_repr


class QueueInitialCapability:
    def __init__(self, results: tuple[CapabilityResult[InitialRequirementProposal], ...]) -> None:
        self.results = list(results)

    def interpret_initial_requirement(
        self, request: InitialRequirementInterpretationRequest
    ) -> CapabilityResult[InitialRequirementProposal]:
        return self.results.pop(0)


class QueuePatchCapability:
    def __init__(self, results: tuple[CapabilityResult[PatchRequirementProposal], ...]) -> None:
        self.results = list(results)

    def understand_patch(
        self, request: PatchUnderstandingRequest
    ) -> CapabilityResult[PatchRequirementProposal]:
        return self.results.pop(0)


class HookedPatchCapability:
    def __init__(
        self,
        *,
        before_return: Callable[[], None],
        result: CapabilityResult[PatchRequirementProposal],
    ) -> None:
        self.before_return = before_return
        self.result = result

    def understand_patch(
        self, request: PatchUnderstandingRequest
    ) -> CapabilityResult[PatchRequirementProposal]:
        self.before_return()
        return self.result


@dataclass(frozen=True)
class EmptyPatchCapability:
    def understand_patch(
        self, request: PatchUnderstandingRequest
    ) -> CapabilityResult[PatchRequirementProposal]:
        raise AssertionError("Patch capability should not be called")


def llm_interpreter(
    *,
    initial: QueueInitialCapability | None = None,
    patch: QueuePatchCapability | HookedPatchCapability | None = None,
) -> LLMRequirementInterpreter:
    return LLMRequirementInterpreter(
        initial_capability=initial or QueueInitialCapability(()),
        patch_capability=patch or EmptyPatchCapability(),
        locale="zh-CN",
    )


def metadata(capability: LLMCapabilityName) -> CapabilityGenerationMetadata:
    return LLMBackedCapabilityMetadata(
        capability=capability,
        output_schema_version="m8-u1",
        adapter_version="test-u4",
        model_identity="deepseek-v4-flash-candidate",
        invocation=LLMCapabilityInvocationMetadata(
            invocation_id=LLMInvocationId(f"test-{capability.value.lower()}"),
            capability=capability.value,
            model_id="deepseek-v4-flash-candidate",
            prompt_template_version="test-prompt-v1",
            output_schema_version="m8-u1",
            adapter_version="test-u4",
            attempt_count=1,
            latency_ms=1,
            token_count_observed=True,
        ),
    )


def capability_success(proposal):
    capability = (
        LLMCapabilityName.PATCH_UNDERSTANDING
        if isinstance(proposal, PatchRequirementProposal)
        else LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION
    )
    return CapabilityResult.success(
        metadata(capability),
        proposal,
        CapabilitySemanticValidation(is_semantically_valid=True),
    )


def semantic_issue(code: str):
    from flight_agent.ports import CapabilitySemanticIssue

    return (CapabilitySemanticIssue(code=code, message=code.lower()),)


def clear_initial_proposal() -> InitialRequirementProposal:
    return InitialRequirementProposal(
        constraints=(
            airport_constraint("constraint-origin", ConstraintScope.ORIGIN_AIRPORT, "PEK"),
            airport_constraint("constraint-destination", ConstraintScope.DESTINATION_AIRPORT, "SHA"),
            date_constraint(),
        ),
        preferences=(price_preference(),),
        source_input="从 PEK 到 SHA，2026-09-01 出发",
    )


def patch_proposal(operation: PatchProposalOperation) -> PatchRequirementProposal:
    return PatchRequirementProposal(
        operations=(operation,),
        source_input="synthetic patch",
        based_on_requirement_id=RequirementId("requirement-1"),
        based_on_requirement_version=RequirementVersion(1),
    )


def replace_origin_patch(airport: str) -> PatchRequirementProposal:
    return patch_proposal(
        PatchProposalOperation(
            PatchProposalAction.REPLACE_CONSTRAINT,
            target_id=ConstraintId("constraint-origin"),
            item=airport_constraint("proposal-origin", ConstraintScope.ORIGIN_AIRPORT, airport),
        )
    )


def committed_ready_requirement() -> tuple[InMemoryRequirementRepository, RequirementState]:
    repository = InMemoryRequirementRepository()
    v1 = RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(1),
        constraints=(
            airport_constraint("constraint-origin", ConstraintScope.ORIGIN_AIRPORT, "PEK"),
            airport_constraint("constraint-destination", ConstraintScope.DESTINATION_AIRPORT, "SHA"),
            date_constraint(),
        ),
        preferences=(price_preference(),),
    )
    repository.commit_initial(v1, operation_id="initial")
    return repository, v1


def airport_constraint(raw_id: str, scope: ConstraintScope, airport: str) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=scope,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def date_constraint() -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-date"),
        scope=ConstraintScope.DEPARTURE_DATE,
        operator=ConstraintOperator.EQUALS,
        value=LocalDate(date(2026, 9, 1)),
    )


def price_preference(importance: PreferenceImportance = PreferenceImportance.HIGH) -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId("preference-price"),
        scope=PreferenceScope.PRICE,
        importance=importance,
    )


def normalization_context() -> NormalizationContext:
    return NormalizationContext(
        reference_instant=instant(0),
        timezone="Asia/Shanghai",
        locale="zh-CN",
        reference_data_version="u4-test",
    )


def constraint_json(
    constraint_id: str,
    scope: str,
    operator: str,
    value: Any,
) -> dict[str, Any]:
    return {
        "constraint_id": constraint_id,
        "type": scope,
        "operator": operator,
        "value": value,
    }


def airport_value(constraint: HardConstraint) -> str:
    assert isinstance(constraint.value, AirportCode)
    return constraint.value.value


def instant(hour: int) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 28, hour, 0, tzinfo=UTC))
