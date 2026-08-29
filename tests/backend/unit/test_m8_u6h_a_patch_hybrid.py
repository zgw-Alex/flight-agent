from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from flight_agent.adapters.requirement_repository_memory import (
    InMemoryRequirementRepository,
)
from flight_agent.application import (
    DeterministicPatchHybridInterpreter,
    MutationConsolidator,
    NormalizationContext,
    PatchSemanticIR,
    RequirementPipelineOutcomeStatus,
    ResolutionDisposition,
    SemanticEvidence,
    SemanticEvidenceKind,
    SemanticImportanceSignal,
    SemanticMutation,
    SemanticOperation,
    SemanticTarget,
    apply_patch_proposal,
    build_deterministic_patch_proposal,
    commit_requirement_transition,
    execute_patch_requirement_from_current,
)
from flight_agent.application.requirement_transition import PatchTransitionStatus
from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import (
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    RequirementState,
    SoftPreference,
    StopCount,
)
from flight_agent.domain.shared import DomainInstant, RequirementVersion
from flight_agent.ports import (
    CommitStatus,
    InterpreterInput,
    InterpreterMode,
    PatchInterpreterPayload,
    PatchProposalAction,
)


def test_u6h_a_a01_a02_price_patch_add_and_replace_are_deterministic_hard_constraints() -> None:
    added_ir, added = build_deterministic_patch_proposal("预算改到1200", requirement_with())
    assert added_ir.disposition is ResolutionDisposition.RESOLVED
    assert added.based_on_requirement_id == RequirementId("requirement-1")
    assert len(added.evidence) > 0
    assert len(added.operations) == 1
    assert added.operations[0].action is PatchProposalAction.ADD_CONSTRAINT
    assert_price_constraint(added.operations[0].item, Decimal(1200))

    _, replaced = build_deterministic_patch_proposal(
        "价格不要超过1200",
        requirement_with(max_price_constraint(1500)),
    )
    assert len(replaced.operations) == 1
    assert replaced.operations[0].action is PatchProposalAction.REPLACE_CONSTRAINT
    assert replaced.operations[0].target_id == ConstraintId("max-price")
    assert_price_constraint(replaced.operations[0].item, Decimal(1200))


def test_u6h_a_a03_a04_same_value_no_change_and_remove_use_existing_patch_authority() -> None:
    current = requirement_with(max_price_constraint(1500))

    _, same_value = build_deterministic_patch_proposal("预算还是1500", current)
    assert same_value.operations == ()

    _, remove_budget = build_deterministic_patch_proposal("取消预算限制", current)
    removed = apply_patch_proposal(current, remove_budget, recorded_at=instant(3))
    assert removed.status is PatchTransitionStatus.APPLIED
    assert removed.requirement is not None
    assert removed.requirement.constraints == ()


def test_u6h_a_price_equivalence_is_base_state_based_not_word_based() -> None:
    current = requirement_with(max_price_constraint(1500))

    for message in ("预算1500", "预算还是1500", "预算保持1500", "价格1500.0"):
        ir, proposal = build_deterministic_patch_proposal(message, current)

        assert ir.disposition is ResolutionDisposition.RESOLVED
        assert proposal.operations == ()

    _, changed = build_deterministic_patch_proposal("预算改成1800", current)
    assert tuple(operation.action for operation in changed.operations) == (
        PatchProposalAction.REPLACE_CONSTRAINT,
    )
    assert changed.operations[0].target_id == ConstraintId("max-price")
    assert_price_constraint(changed.operations[0].item, Decimal(1800))


def test_u6h_a_ambiguous_or_unknown_value_does_not_become_no_op() -> None:
    current = requirement_with(max_price_constraint(1500), max_stops_constraint(0))

    ambiguous_ir, ambiguous = build_deterministic_patch_proposal("刚才那个还是1500", current)
    assert ambiguous_ir.disposition is ResolutionDisposition.CLARIFICATION_REQUIRED
    assert ambiguous.operations == ()
    assert ambiguous.unresolved_semantics == ("No deterministic patch semantics found",)

    unknown_ir, unknown = build_deterministic_patch_proposal("预算还是一千五", current)
    assert unknown_ir.disposition is ResolutionDisposition.CLARIFICATION_REQUIRED
    assert unknown.operations == ()
    assert unknown.unresolved_semantics == ("No deterministic patch semantics found",)


def test_u6h_a_a05_a06_direct_flight_hard_maps_to_max_stops_and_soft_stays_preference() -> None:
    hard_ir, hard = build_deterministic_patch_proposal("必须直飞", requirement_with())
    assert hard_ir.disposition is ResolutionDisposition.RESOLVED
    assert len(hard.operations) == 1
    assert hard.operations[0].action is PatchProposalAction.ADD_CONSTRAINT
    assert_max_stops_constraint(hard.operations[0].item, 0)

    soft_ir, soft = build_deterministic_patch_proposal("最好直飞", requirement_with())
    assert soft_ir.disposition is ResolutionDisposition.RESOLVED
    assert len(soft.operations) == 1
    assert soft.operations[0].action is PatchProposalAction.ADD_PREFERENCE
    assert soft.operations[0].item == SoftPreference(
        PreferenceId("hybrid-fewer-stops"),
        PreferenceScope.FEWER_STOPS,
        PreferenceImportance.HIGH,
    )
    assert not hasattr(ConstraintScope, "DIRECT_FLIGHT")


def test_u6h_a_direct_flight_hard_equivalence_is_no_op_against_base() -> None:
    current = requirement_with(max_stops_constraint(0))

    for message in ("必须直飞", "还是必须直飞", "继续要求直飞"):
        ir, proposal = build_deterministic_patch_proposal(message, current)

        assert ir.disposition is ResolutionDisposition.RESOLVED
        assert proposal.operations == ()


def test_u6h_a_a07_hard_to_soft_direct_conversion_is_atomic_in_one_patch_set() -> None:
    current = requirement_with(max_stops_constraint(0))
    _, proposal = build_deterministic_patch_proposal("直飞不用必须，最好就行", current)

    assert tuple(operation.action for operation in proposal.operations) == (
        PatchProposalAction.REMOVE_CONSTRAINT,
        PatchProposalAction.ADD_PREFERENCE,
    )
    applied = apply_patch_proposal(current, proposal, recorded_at=instant(2))
    assert applied.status is PatchTransitionStatus.APPLIED
    assert applied.patch_set is not None
    assert len(applied.patch_set.patches) == 2
    assert applied.requirement is not None
    assert applied.requirement.version == RequirementVersion(2)
    assert applied.requirement.constraints == ()
    assert applied.requirement.preferences[0].scope is PreferenceScope.FEWER_STOPS


def test_u6h_a_remove_hard_direct_without_soft_preference_remains_remove() -> None:
    current = requirement_with(max_stops_constraint(0))
    ir, proposal = build_deterministic_patch_proposal("直飞不用必须", current)

    assert ir.disposition is ResolutionDisposition.RESOLVED
    assert tuple(operation.action for operation in proposal.operations) == (
        PatchProposalAction.REMOVE_CONSTRAINT,
    )
    assert proposal.operations[0].target_id == ConstraintId("max-stops")


def test_u6h_a_a08_to_a10_correction_multi_target_and_missing_exact_value() -> None:
    correction_ir, correction = build_deterministic_patch_proposal("预算1500，不对，1200", requirement_with())
    assert correction_ir.disposition is ResolutionDisposition.RESOLVED
    assert len(correction.operations) == 1
    assert_price_constraint(correction.operations[0].item, Decimal(1200))

    multi_ir, multi = build_deterministic_patch_proposal("预算改1200，必须直飞", requirement_with())
    assert multi_ir.disposition is ResolutionDisposition.RESOLVED
    assert tuple(operation.action for operation in multi.operations) == (
        PatchProposalAction.ADD_CONSTRAINT,
        PatchProposalAction.ADD_CONSTRAINT,
    )
    assert {operation.item.scope for operation in multi.operations if isinstance(operation.item, HardConstraint)} == {
        ConstraintScope.MAX_PRICE,
        ConstraintScope.MAX_STOPS,
    }

    unclear_ir, unclear = build_deterministic_patch_proposal(
        "预算稍微放宽一点",
        requirement_with(max_price_constraint(1200)),
    )
    assert unclear_ir.disposition is ResolutionDisposition.CLARIFICATION_REQUIRED
    assert unclear.operations == ()
    assert unclear.unresolved_semantics == ("Missing exact value",)


def test_u6h_a_a11_to_a15_ambiguous_partial_or_resolver_paths_do_not_commit() -> None:
    cases = [
        ("把那个限制删掉", ResolutionDisposition.CLARIFICATION_REQUIRED, "Ambiguous target reference"),
        ("预算1200或者1500都行", ResolutionDisposition.CLARIFICATION_REQUIRED, "Alternative values are unsupported"),
        ("如果便宜很多就直飞", ResolutionDisposition.SEMANTIC_RESOLVER_REQUIRED, "Semantic resolver required"),
        ("直飞更重要，别卡那么死", ResolutionDisposition.SEMANTIC_RESOLVER_REQUIRED, "Semantic resolver required"),
        ("预算改1200，日期改下周那个时间", ResolutionDisposition.CLARIFICATION_REQUIRED, "Date reference requires clarification"),
    ]
    current = requirement_with(max_price_constraint(1500), max_stops_constraint(0))

    for message, disposition, unresolved in cases:
        ir, proposal = build_deterministic_patch_proposal(message, current)
        assert ir.disposition is disposition
        assert proposal.operations == ()
        assert unresolved in proposal.unresolved_semantics
        applied = apply_patch_proposal(current, proposal, recorded_at=instant(2))
        assert applied.status is PatchTransitionStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT
        assert applied.requirement is None


def test_u6h_a_a16_t3_preservation_assertion_and_single_commit_via_m3() -> None:
    repository = InMemoryRequirementRepository()
    current = requirement_with(max_price_constraint(1500), max_stops_constraint(0), preference=soft_fewer_stops())
    assert repository.commit_initial(current, operation_id="initial").status is CommitStatus.COMMITTED

    ir, proposal = build_deterministic_patch_proposal("预算改1200，其他不变", current)
    assert ir.disposition is ResolutionDisposition.RESOLVED
    assert ir.assertions[0].assertion_type == "GLOBAL_UNMENTIONED_PRESERVE"

    applied = apply_patch_proposal(current, proposal, recorded_at=instant(2))
    assert applied.status is PatchTransitionStatus.APPLIED
    assert applied.requirement is not None
    committed = commit_requirement_transition(repository, current, applied.requirement, "u6h-a-a16")
    assert committed.status is CommitStatus.COMMITTED
    assert committed.requirement is not None
    assert committed.requirement.version == RequirementVersion(2)
    assert max_price_value(committed.requirement) == Decimal(1200)
    assert max_stops_value(committed.requirement) == 0
    assert committed.requirement.preferences == current.preferences

    pipeline_repository = InMemoryRequirementRepository()
    assert pipeline_repository.commit_initial(current, operation_id="pipeline-initial").status is CommitStatus.COMMITTED
    outcome = execute_patch_requirement_from_current(
        repository=pipeline_repository,
        interpreter=DeterministicPatchHybridInterpreter(),
        interpreter_input=patch_input("预算改1200，其他不变"),
        normalization_context=normalization_context(),
        current=current,
        operation_id="pipeline-u6h-a-a16",
        recorded_at=instant(3),
    )
    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.requirement is not None
    assert outcome.requirement.version == RequirementVersion(2)
    assert max_price_value(outcome.requirement) == Decimal(1200)
    assert max_stops_value(outcome.requirement) == 0
    assert outcome.requirement.preferences == current.preferences


def test_u6h_a_t1_consolidation_deduplicates_corrections_and_blocks_contradictions() -> None:
    duplicate_ir = PatchSemanticIR(
        disposition=ResolutionDisposition.RESOLVED,
        mutations=(
            price_mutation(Decimal(1200)),
            price_mutation(Decimal(1200)),
        ),
        evidence=(value_evidence("ev-1", "1200"),),
    )
    assert len(MutationConsolidator().consolidate(duplicate_ir).mutations) == 1

    corrected_ir = PatchSemanticIR(
        disposition=ResolutionDisposition.RESOLVED,
        mutations=(price_mutation(Decimal(1500)), price_mutation(Decimal(1200))),
        evidence=(value_evidence("ev-1", "1500"), correction_evidence(), value_evidence("ev-2", "1200")),
    )
    corrected = MutationConsolidator().consolidate(corrected_ir)
    assert corrected.disposition is ResolutionDisposition.RESOLVED
    assert corrected.mutations == (price_mutation(Decimal(1200)),)

    contradictory_ir = PatchSemanticIR(
        disposition=ResolutionDisposition.RESOLVED,
        mutations=(price_mutation(Decimal(1500)), price_mutation(Decimal(1200))),
        evidence=(value_evidence("ev-1", "1500"), value_evidence("ev-2", "1200")),
    )
    contradictory = MutationConsolidator().consolidate(contradictory_ir)
    assert contradictory.disposition is ResolutionDisposition.CLARIFICATION_REQUIRED
    assert contradictory.ambiguities[0].code == "CONTRADICTORY_VALUES"


def test_u6h_a_g_llm_0_hybrid_module_has_no_llm_or_network_dependency() -> None:
    source = (
        Path(__file__).parents[3] / "apps/backend/src/flight_agent/application/requirement_patch_hybrid.py"
    ).read_text(encoding="utf-8")
    forbidden = ("deepseek", "openai", "requests", "httpx", "aiohttp", "api_key")
    assert all(token not in source.lower() for token in forbidden)


def requirement_with(
    *constraints: HardConstraint,
    preference: SoftPreference | None = None,
) -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(1),
        constraints=constraints,
        preferences=() if preference is None else (preference,),
    )


def max_price_constraint(value: int, raw_id: str = "max-price") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.MAX_PRICE,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=Money(Decimal(value), "CNY"),
    )


def max_stops_constraint(value: int, raw_id: str = "max-stops") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.MAX_STOPS,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=StopCount(value),
    )


def soft_fewer_stops(raw_id: str = "fewer-stops") -> SoftPreference:
    return SoftPreference(
        PreferenceId(raw_id),
        PreferenceScope.FEWER_STOPS,
        PreferenceImportance.MEDIUM,
    )


def patch_input(source_input: str) -> InterpreterInput:
    return InterpreterInput(
        mode=InterpreterMode.PATCH,
        payload=PatchInterpreterPayload(source_input),
    )


def normalization_context() -> NormalizationContext:
    return NormalizationContext(
        reference_instant=instant(0),
        timezone="Asia/Shanghai",
        locale="zh-CN",
        reference_data_version="fixture-v1",
    )


def price_mutation(value: Decimal) -> SemanticMutation:
    return SemanticMutation(
        target=SemanticTarget.MAX_PRICE,
        operation=SemanticOperation.SET,
        value=Money(value, "CNY"),
        importance_signal=SemanticImportanceSignal.HARD,
    )


def value_evidence(evidence_id: str, value: str) -> SemanticEvidence:
    return SemanticEvidence(evidence_id, SemanticEvidenceKind.VALUE_TEXT, value)


def correction_evidence() -> SemanticEvidence:
    return SemanticEvidence("ev-correction-1", SemanticEvidenceKind.CORRECTION_TEXT, "不对")


def assert_price_constraint(item: object, amount: Decimal) -> None:
    assert isinstance(item, HardConstraint)
    assert item.scope is ConstraintScope.MAX_PRICE
    assert item.operator is ConstraintOperator.AT_OR_BEFORE
    assert item.value == Money(amount, "CNY")


def assert_max_stops_constraint(item: object, stop_count: int) -> None:
    assert isinstance(item, HardConstraint)
    assert item.scope is ConstraintScope.MAX_STOPS
    assert item.operator is ConstraintOperator.AT_OR_BEFORE
    assert item.value == StopCount(stop_count)


def max_price_value(requirement: RequirementState) -> Decimal:
    value = next(constraint.value for constraint in requirement.constraints if constraint.scope is ConstraintScope.MAX_PRICE)
    assert isinstance(value, Money)
    return value.amount


def max_stops_value(requirement: RequirementState) -> int:
    value = next(constraint.value for constraint in requirement.constraints if constraint.scope is ConstraintScope.MAX_STOPS)
    assert isinstance(value, StopCount)
    return value.value


def instant(hour: int) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 28, hour, 0, tzinfo=UTC))
