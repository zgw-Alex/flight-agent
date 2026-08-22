from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import NoReturn

from flight_agent.adapters.requirement_interpreter_fake import (
    FakeRequirementInterpreter,
    RequirementInterpreterFixture,
)
from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.application import (
    AirportCanonicalization,
    NormalizationContext,
    PostCommitProcessingStatus,
    RequirementPipelineOutcomeStatus,
    RequirementValidationIssueCode,
    SearchReadinessStatus,
    execute_initial_requirement,
    execute_patch_requirement,
    execute_patch_requirement_from_current,
)
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
    CommitStatus,
    InitialInterpreterPayload,
    InitialRequirementProposal,
    InterpreterInput,
    InterpreterMode,
    InterpreterResult,
    PatchInterpreterPayload,
    PatchProposalAction,
    PatchProposalOperation,
    PatchRequirementProposal,
)


def test_gs01_initial_ready_requirement_commits_v1_and_ready() -> None:
    repository = InMemoryRequirementRepository()

    outcome = execute_initial_requirement(
        repository=repository,
        interpreter=interpreter(
            initial_fixture("ready", InitialRequirementProposal(constraints=ready_constraints()))
        ),
        interpreter_input=initial_input("ready"),
        normalization_context=normalization_context(),
        requirement_id=RequirementId("requirement-1"),
        operation_id="gs-01",
        recorded_at=instant(1),
    )

    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.commit_status is CommitStatus.COMMITTED
    assert outcome.requirement == repository.get_current(RequirementId("requirement-1"))
    assert outcome.requirement is not None
    assert outcome.requirement.version == RequirementVersion(1)
    assert outcome.validation is not None
    assert outcome.validation.readiness is SearchReadinessStatus.READY


def test_gs02_initial_incomplete_but_committable_is_not_ready_after_commit() -> None:
    repository = InMemoryRequirementRepository()

    outcome = execute_initial_requirement(
        repository=repository,
        interpreter=interpreter(
            initial_fixture(
                "incomplete",
                InitialRequirementProposal(constraints=(origin_constraint(), destination_constraint())),
            )
        ),
        interpreter_input=initial_input("incomplete"),
        normalization_context=normalization_context(),
        requirement_id=RequirementId("requirement-1"),
        operation_id="gs-02",
        recorded_at=instant(1),
    )

    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.requirement == repository.get_current(RequirementId("requirement-1"))
    assert outcome.validation is not None
    assert outcome.validation.readiness is SearchReadinessStatus.NOT_READY
    assert [issue.code for issue in outcome.validation.issues] == [
        RequirementValidationIssueCode.MISSING_DEPARTURE_DATE
    ]


def test_gs03_ambiguous_before_commit_does_not_create_authority() -> None:
    repository = InMemoryRequirementRepository()

    outcome = execute_initial_requirement(
        repository=repository,
        interpreter=interpreter(
            initial_fixture("ambiguous", InitialRequirementProposal(constraints=ready_constraints("SHA")))
        ),
        interpreter_input=initial_input("ambiguous"),
        normalization_context=normalization_context(ambiguous_airports=(AirportCode("SHA"),)),
        requirement_id=RequirementId("requirement-1"),
        operation_id="gs-03",
        recorded_at=instant(1),
    )

    assert outcome.status is RequirementPipelineOutcomeStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT
    assert outcome.requirement is None
    assert repository.get_current(RequirementId("requirement-1")) is None


def test_gs04_valid_patch_creates_v2_and_preserves_v1_immutability() -> None:
    repository, v1 = committed_ready_requirement()
    v1_constraints = v1.constraints

    outcome = execute_patch_requirement(
        repository=repository,
        interpreter=interpreter(
            patch_fixture(
                "change-date",
                PatchRequirementProposal(
                    operations=(
                        PatchProposalOperation(
                            PatchProposalAction.REPLACE_CONSTRAINT,
                            item=date_constraint("proposal-date", date(2026, 10, 1)),
                            target_id=ConstraintId("constraint-date"),
                        ),
                    )
                ),
            )
        ),
        interpreter_input=patch_input("change-date"),
        normalization_context=normalization_context(),
        requirement_id=v1.requirement_id,
        operation_id="gs-04",
        recorded_at=instant(2),
    )

    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.requirement is not None
    assert outcome.requirement.version == RequirementVersion(2)
    assert outcome.requirement.predecessor_version == RequirementVersion(1)
    assert repository.get_current(v1.requirement_id) == outcome.requirement
    persisted_v1 = repository.get_version(v1.requirement_id, RequirementVersion(1))
    assert persisted_v1 is not None
    assert persisted_v1.constraints == v1_constraints


def test_gs05_multi_operation_atomic_patch_commits_one_next_version() -> None:
    repository, v1 = committed_ready_requirement()

    outcome = execute_patch_requirement(
        repository=repository,
        interpreter=interpreter(
            patch_fixture(
                "multi",
                PatchRequirementProposal(
                    operations=(
                        PatchProposalOperation(
                            PatchProposalAction.ADD_PREFERENCE,
                            item=departure_preference("proposal-pref"),
                        ),
                        PatchProposalOperation(
                            PatchProposalAction.REPLACE_CONSTRAINT,
                            item=origin_constraint("proposal-origin", "SHA"),
                            target_id=ConstraintId("constraint-origin"),
                        ),
                        PatchProposalOperation(
                            PatchProposalAction.REMOVE_CONSTRAINT,
                            target_id=ConstraintId("constraint-date"),
                        ),
                    )
                ),
            )
        ),
        interpreter_input=patch_input("multi"),
        normalization_context=normalization_context(),
        requirement_id=v1.requirement_id,
        operation_id="gs-05",
        recorded_at=instant(2),
    )

    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.requirement is not None
    assert outcome.requirement.version == RequirementVersion(2)
    assert len(repository.history(v1.requirement_id)) == 2
    assert ConstraintId("constraint-date") not in {
        constraint.constraint_id for constraint in outcome.requirement.constraints
    }


def test_gs06_semantic_no_op_does_not_call_commit_next_or_grow_history() -> None:
    repository, v1 = committed_ready_requirement()

    outcome = execute_patch_requirement(
        repository=repository,
        interpreter=interpreter(
            patch_fixture(
                "noop",
                PatchRequirementProposal(
                    operations=(
                        PatchProposalOperation(
                            PatchProposalAction.REPLACE_CONSTRAINT,
                            item=origin_constraint("proposal-origin", "PVG"),
                            target_id=ConstraintId("constraint-origin"),
                        ),
                    )
                ),
            )
        ),
        interpreter_input=patch_input("noop"),
        normalization_context=normalization_context(),
        requirement_id=v1.requirement_id,
        operation_id="gs-06",
        recorded_at=instant(2),
    )

    assert outcome.status is RequirementPipelineOutcomeStatus.NO_CHANGE
    assert outcome.requirement == v1
    assert repository.history(v1.requirement_id) == (v1,)


def test_gs07_idempotent_replay_returns_existing_authority_without_new_version() -> None:
    repository, v1 = committed_ready_requirement()
    patch = patch_fixture(
        "add-date",
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.ADD_PREFERENCE,
                    item=departure_preference("proposal-pref"),
                ),
            )
        ),
    )
    first = execute_patch_requirement(
        repository=repository,
        interpreter=interpreter(patch),
        interpreter_input=patch_input("add-date"),
        normalization_context=normalization_context(),
        requirement_id=v1.requirement_id,
        operation_id="gs-07",
        recorded_at=instant(2),
    )
    replay = execute_patch_requirement(
        repository=repository,
        interpreter=interpreter(patch),
        interpreter_input=patch_input("add-date"),
        normalization_context=normalization_context(),
        requirement_id=v1.requirement_id,
        operation_id="gs-07",
        recorded_at=instant(3),
    )

    assert first.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert replay.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert replay.commit_status is CommitStatus.REPLAYED
    assert replay.requirement == first.requirement
    assert len(repository.history(v1.requirement_id)) == 2


def test_gs08_stale_competing_patch_conflicts_at_repository_cas() -> None:
    repository, v1 = committed_ready_requirement()
    first = execute_patch_requirement_from_current(
        repository=repository,
        interpreter=interpreter(patch_fixture("first", replace_origin_patch("SHA"))),
        interpreter_input=patch_input("first"),
        normalization_context=normalization_context(),
        current=v1,
        operation_id="gs-08-first",
        recorded_at=instant(2),
    )
    stale = execute_patch_requirement_from_current(
        repository=repository,
        interpreter=interpreter(patch_fixture("stale", replace_origin_patch("HKG"))),
        interpreter_input=patch_input("stale"),
        normalization_context=normalization_context(),
        current=v1,
        operation_id="gs-08-stale",
        recorded_at=instant(3),
    )

    assert first.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert stale.status is RequirementPipelineOutcomeStatus.CONCURRENCY_CONFLICT
    assert len(repository.history(v1.requirement_id)) == 2


def test_gs09_invalid_patch_target_is_rejected_and_authority_unchanged() -> None:
    repository, v1 = committed_ready_requirement()

    outcome = execute_patch_requirement(
        repository=repository,
        interpreter=interpreter(
            patch_fixture(
                "missing-target",
                PatchRequirementProposal(
                    operations=(
                        PatchProposalOperation(
                            PatchProposalAction.REMOVE_CONSTRAINT,
                            target_id=ConstraintId("missing"),
                        ),
                    )
                ),
            )
        ),
        interpreter_input=patch_input("missing-target"),
        normalization_context=normalization_context(),
        requirement_id=v1.requirement_id,
        operation_id="gs-09",
        recorded_at=instant(2),
    )

    assert outcome.status is RequirementPipelineOutcomeStatus.INVALID_TRANSITION
    assert repository.history(v1.requirement_id) == (v1,)


def test_gs10_multi_operation_partial_failure_rejects_without_partial_authority() -> None:
    repository, v1 = committed_ready_requirement()

    outcome = execute_patch_requirement(
        repository=repository,
        interpreter=interpreter(
            patch_fixture(
                "partial-failure",
                PatchRequirementProposal(
                    operations=(
                        PatchProposalOperation(
                            PatchProposalAction.ADD_PREFERENCE,
                            item=departure_preference("proposal-pref"),
                        ),
                        PatchProposalOperation(
                            PatchProposalAction.REMOVE_CONSTRAINT,
                            target_id=ConstraintId("missing"),
                        ),
                    )
                ),
            )
        ),
        interpreter_input=patch_input("partial-failure"),
        normalization_context=normalization_context(),
        requirement_id=v1.requirement_id,
        operation_id="gs-10",
        recorded_at=instant(2),
    )

    assert outcome.status is RequirementPipelineOutcomeStatus.INVALID_TRANSITION
    assert repository.history(v1.requirement_id) == (v1,)


def test_gs11_post_commit_validation_failure_does_not_rollback_authority() -> None:
    repository = InMemoryRequirementRepository()

    outcome = execute_initial_requirement(
        repository=repository,
        interpreter=interpreter(
            initial_fixture("ready", InitialRequirementProposal(constraints=ready_constraints()))
        ),
        interpreter_input=initial_input("ready"),
        normalization_context=normalization_context(),
        requirement_id=RequirementId("requirement-1"),
        operation_id="gs-11",
        recorded_at=instant(1),
        validator=failing_validator,
    )

    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.post_commit_processing is PostCommitProcessingStatus.FAILED
    assert repository.get_current(RequirementId("requirement-1")) == outcome.requirement


def test_gs12_commit_not_ready_is_clarification_after_commit() -> None:
    repository = InMemoryRequirementRepository()

    outcome = execute_initial_requirement(
        repository=repository,
        interpreter=interpreter(
            initial_fixture("not-ready", InitialRequirementProposal(constraints=(origin_constraint(),)))
        ),
        interpreter_input=initial_input("not-ready"),
        normalization_context=normalization_context(),
        requirement_id=RequirementId("requirement-1"),
        operation_id="gs-12",
        recorded_at=instant(1),
    )

    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.requirement == repository.get_current(RequirementId("requirement-1"))
    assert outcome.validation is not None
    assert outcome.validation.readiness is SearchReadinessStatus.NOT_READY


def test_gs13_commit_requirement_conflict_is_not_invalid_transition() -> None:
    repository = InMemoryRequirementRepository()

    outcome = execute_initial_requirement(
        repository=repository,
        interpreter=interpreter(
            initial_fixture(
                "conflict",
                InitialRequirementProposal(
                    constraints=(
                        origin_constraint("constraint-origin", "PVG"),
                        destination_constraint("constraint-destination", "PVG"),
                        date_constraint(),
                    )
                ),
            )
        ),
        interpreter_input=initial_input("conflict"),
        normalization_context=normalization_context(),
        requirement_id=RequirementId("requirement-1"),
        operation_id="gs-13",
        recorded_at=instant(1),
    )

    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.validation is not None
    assert RequirementValidationIssueCode.ORIGIN_DESTINATION_CONFLICT in {
        issue.code for issue in outcome.validation.issues
    }


def committed_ready_requirement() -> tuple[InMemoryRequirementRepository, RequirementState]:
    repository = InMemoryRequirementRepository()
    v1 = RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(1),
        constraints=ready_constraints(),
    )
    repository.commit_initial(v1, operation_id="initial")
    return repository, v1


def interpreter(*fixtures: RequirementInterpreterFixture) -> FakeRequirementInterpreter:
    return FakeRequirementInterpreter(fixtures)


def initial_fixture(source_input: str, proposal: InitialRequirementProposal) -> RequirementInterpreterFixture:
    return RequirementInterpreterFixture(
        source_input=source_input,
        mode=InterpreterMode.INITIAL,
        result=InterpreterResult.success(proposal),
    )


def patch_fixture(source_input: str, proposal: PatchRequirementProposal) -> RequirementInterpreterFixture:
    return RequirementInterpreterFixture(
        source_input=source_input,
        mode=InterpreterMode.PATCH,
        result=InterpreterResult.success(proposal),
    )


def initial_input(source_input: str) -> InterpreterInput:
    return InterpreterInput(
        mode=InterpreterMode.INITIAL,
        payload=InitialInterpreterPayload(source_input),
    )


def patch_input(source_input: str) -> InterpreterInput:
    return InterpreterInput(
        mode=InterpreterMode.PATCH,
        payload=PatchInterpreterPayload(source_input),
    )


def normalization_context(
    ambiguous_airports: tuple[AirportCode, ...] = (),
) -> NormalizationContext:
    return NormalizationContext(
        reference_instant=instant(0),
        timezone="Asia/Shanghai",
        locale="zh-CN",
        reference_data_version="fixture-v1",
        canonical_airports=(
            AirportCanonicalization(AirportCode("PVG"), AirportCode("PVG")),
            AirportCanonicalization(AirportCode("LAX"), AirportCode("LAX")),
            AirportCanonicalization(AirportCode("HKG"), AirportCode("HKG")),
        ),
        ambiguous_airports=ambiguous_airports,
    )


def ready_constraints(origin: str = "PVG") -> tuple[HardConstraint, ...]:
    return (
        origin_constraint("constraint-origin", origin),
        destination_constraint(),
        date_constraint(),
    )


def replace_origin_patch(airport: str) -> PatchRequirementProposal:
    return PatchRequirementProposal(
        operations=(
            PatchProposalOperation(
                PatchProposalAction.REPLACE_CONSTRAINT,
                item=origin_constraint("proposal-origin", airport),
                target_id=ConstraintId("constraint-origin"),
            ),
        )
    )


def origin_constraint(raw_id: str = "constraint-origin", airport: str = "PVG") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.ORIGIN_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def destination_constraint(raw_id: str = "constraint-destination", airport: str = "LAX") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.DESTINATION_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def date_constraint(raw_id: str = "constraint-date", value: date | None = None) -> HardConstraint:
    departure_date = value if value is not None else date(2026, 9, 1)
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.DEPARTURE_DATE,
        operator=ConstraintOperator.EQUALS,
        value=LocalDate(departure_date),
    )


def departure_preference(raw_id: str) -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId(raw_id),
        scope=PreferenceScope.DEPARTURE_TIME,
        importance=PreferenceImportance.HIGH,
        value=ValueRange(LocalTime(time(8, 0)), LocalTime(time(11, 0))),
    )


def failing_validator(requirement: RequirementState) -> NoReturn:
    raise RuntimeError(f"validator failed for {requirement.version.value}")


def instant(hour: int) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 22, hour, 0, tzinfo=UTC))
