from __future__ import annotations

from datetime import UTC, date, datetime, time

from flight_agent.adapters.requirement_repository_memory import (
    InMemoryRequirementRepository,
)
from flight_agent.application import (
    PatchTransitionIssueCode,
    PatchTransitionStatus,
    apply_patch_proposal,
    commit_requirement_transition,
    construct_patch_set,
)
from flight_agent.domain.requirements import (
    AirportCode,
    ClearTarget,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    LocalTime,
    PatchOperation,
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
    PatchProposalAction,
    PatchProposalOperation,
    PatchRequirementProposal,
)


def test_constructs_authoritative_add_with_transition_owned_identity() -> None:
    current = initial_state()
    proposal_item = date_constraint("proposal-owned-id", date(2026, 10, 3))

    result = construct_patch_set(
        current,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.ADD_CONSTRAINT,
                    item=proposal_item,
                ),
            )
        ),
    )

    assert result.accepted
    assert result.patch_set is not None
    patch = result.patch_set.patches[0]
    assert patch.operation is PatchOperation.ADD
    assert isinstance(patch.item, HardConstraint)
    assert patch.item.constraint_id == ConstraintId("constraint-v2-1")
    assert patch.item.constraint_id != proposal_item.constraint_id
    assert result.patch_set.base_requirement_version == RequirementVersion(1)


def test_multiple_adds_allocate_unique_authoritative_identities() -> None:
    current = initial_state()

    result = construct_patch_set(
        current,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.ADD_CONSTRAINT,
                    item=date_constraint("proposal-date-1", date(2026, 10, 3)),
                ),
                PatchProposalOperation(
                    PatchProposalAction.ADD_CONSTRAINT,
                    item=date_constraint("proposal-date-2", date(2026, 10, 4)),
                ),
            )
        ),
    )

    assert result.accepted
    assert result.patch_set is not None
    added_items = result.patch_set.patches
    assert all(isinstance(patch.item, HardConstraint) for patch in added_items)
    assert [patch.item.constraint_id for patch in added_items if isinstance(patch.item, HardConstraint)] == [
        ConstraintId("constraint-v2-1"),
        ConstraintId("constraint-v2-2"),
    ]


def test_replace_keeps_explicit_target_identity_even_when_proposal_item_differs() -> None:
    current = initial_state()

    result = apply_patch_proposal(
        current,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.REPLACE_CONSTRAINT,
                    item=origin_constraint("non-authoritative-id", "SHA"),
                    target_id=ConstraintId("constraint-origin"),
                ),
            )
        ),
        recorded_at=instant(10),
    )

    assert result.status is PatchTransitionStatus.APPLIED
    assert result.requirement is not None
    assert result.requirement.version == RequirementVersion(2)
    assert result.requirement.predecessor_version == RequirementVersion(1)
    assert result.requirement.constraints[0] == origin_constraint("constraint-origin", "SHA")
    assert current.constraints[0] == origin_constraint("constraint-origin", "PVG")


def test_multi_operation_patch_applies_atomically_as_one_version() -> None:
    current = initial_state()

    result = apply_patch_proposal(
        current,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.ADD_CONSTRAINT,
                    item=date_constraint("proposal-date", date(2026, 10, 5)),
                ),
                PatchProposalOperation(
                    PatchProposalAction.REPLACE_PREFERENCE,
                    item=departure_preference("proposal-pref", PreferenceImportance.LOW),
                    target_id=PreferenceId("preference-time"),
                ),
                PatchProposalOperation(
                    PatchProposalAction.REMOVE_CONSTRAINT,
                    target_id=ConstraintId("constraint-destination"),
                ),
            )
        ),
        recorded_at=instant(10),
    )

    assert result.status is PatchTransitionStatus.APPLIED
    assert result.requirement is not None
    assert result.requirement.version == RequirementVersion(2)
    assert ConstraintId("constraint-destination") not in {
        constraint.constraint_id for constraint in result.requirement.constraints
    }
    assert ConstraintId("constraint-v2-1") in {
        constraint.constraint_id for constraint in result.requirement.constraints
    }
    assert result.requirement.preferences == (
        departure_preference("preference-time", PreferenceImportance.LOW),
    )
    assert len(result.patch_set.patches) == 3 if result.patch_set is not None else False


def test_clear_is_limited_to_typed_semantic_scope() -> None:
    current = initial_state()

    result = apply_patch_proposal(
        current,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(PatchProposalAction.CLEAR_PREFERENCES),
            )
        ),
        recorded_at=instant(10),
    )

    assert result.status is PatchTransitionStatus.APPLIED
    assert result.requirement is not None
    assert result.requirement.constraints == current.constraints
    assert result.requirement.preferences == ()
    assert result.patch_set is not None
    assert result.patch_set.patches[0].clear_target is ClearTarget.SOFT_PREFERENCES


def test_missing_and_wrong_typed_targets_reject_before_apply() -> None:
    current = initial_state()

    missing = construct_patch_set(
        current,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.REMOVE_CONSTRAINT,
                    target_id=ConstraintId("missing"),
                ),
            )
        ),
    )
    wrong_type = construct_patch_set(
        current,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.REMOVE_CONSTRAINT,
                    target_id=PreferenceId("preference-time"),
                ),
            )
        ),
    )

    assert [issue.code for issue in missing.issues] == [PatchTransitionIssueCode.NOT_FOUND]
    assert [issue.code for issue in wrong_type.issues] == [PatchTransitionIssueCode.TYPE_MISMATCH]


def test_ambiguous_target_needs_clarification_before_commit() -> None:
    result = apply_patch_proposal(
        initial_state(),
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.REMOVE_CONSTRAINT,
                ),
            )
        ),
        recorded_at=instant(10),
    )

    assert result.status is PatchTransitionStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT
    assert result.requirement is None
    assert [issue.code for issue in result.issues] == [PatchTransitionIssueCode.AMBIGUOUS_TARGET]


def test_operation_set_conflicts_reject_without_partial_authority() -> None:
    current = initial_state()

    result = apply_patch_proposal(
        current,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(PatchProposalAction.CLEAR_CONSTRAINTS),
                PatchProposalOperation(
                    PatchProposalAction.ADD_CONSTRAINT,
                    item=date_constraint("proposal-date", date(2026, 10, 6)),
                ),
            )
        ),
        recorded_at=instant(10),
    )

    assert result.status is PatchTransitionStatus.INVALID_TRANSITION
    assert result.requirement is None
    assert [issue.code for issue in result.issues] == [PatchTransitionIssueCode.OPERATION_CONFLICT]
    assert current.version == RequirementVersion(1)
    assert current.constraints == (origin_constraint(), destination_constraint())


def test_semantic_no_op_returns_existing_state_without_new_version() -> None:
    current = initial_state()

    result = apply_patch_proposal(
        current,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.REPLACE_CONSTRAINT,
                    item=origin_constraint("proposal-origin", "PVG"),
                    target_id=ConstraintId("constraint-origin"),
                ),
            )
        ),
        recorded_at=instant(10),
    )

    assert result.status is PatchTransitionStatus.NO_CHANGE
    assert result.requirement is current
    assert current.version == RequirementVersion(1)


def test_valid_patch_can_commit_through_repository_cas_and_replay_once() -> None:
    repository = InMemoryRequirementRepository()
    v1 = initial_state()
    repository.commit_initial(v1, operation_id="initial")
    application = apply_patch_proposal(
        v1,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.ADD_CONSTRAINT,
                    item=date_constraint("proposal-date", date(2026, 10, 7)),
                ),
            )
        ),
        recorded_at=instant(10),
    )

    assert application.status is PatchTransitionStatus.APPLIED
    assert application.requirement is not None
    committed = commit_requirement_transition(repository, v1, application.requirement, "patch-1")
    replay = commit_requirement_transition(repository, v1, application.requirement, "patch-1")

    assert committed.status is CommitStatus.COMMITTED
    assert replay.status is CommitStatus.REPLAYED
    assert repository.get_current(v1.requirement_id) == application.requirement
    assert repository.history(v1.requirement_id) == (v1, application.requirement)


def test_stale_competing_patch_is_left_to_repository_cas() -> None:
    repository = InMemoryRequirementRepository()
    v1 = initial_state()
    repository.commit_initial(v1, operation_id="initial")
    first = apply_patch_proposal(
        v1,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.ADD_CONSTRAINT,
                    item=date_constraint("first-proposal", date(2026, 10, 7)),
                ),
            )
        ),
        recorded_at=instant(10),
    )
    second = apply_patch_proposal(
        v1,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.ADD_CONSTRAINT,
                    item=date_constraint("second-proposal", date(2026, 10, 8)),
                ),
            )
        ),
        recorded_at=instant(11),
    )
    assert first.requirement is not None
    assert second.requirement is not None

    committed = commit_requirement_transition(repository, v1, first.requirement, "patch-1")
    stale = commit_requirement_transition(repository, v1, second.requirement, "patch-2")

    assert committed.status is CommitStatus.COMMITTED
    assert stale.status is CommitStatus.CONCURRENCY_CONFLICT
    assert repository.history(v1.requirement_id) == (v1, first.requirement)


def initial_state() -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(1),
        constraints=(
            origin_constraint(),
            destination_constraint(),
        ),
        preferences=(departure_preference(),),
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


def date_constraint(raw_id: str, departure_date: date) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.DEPARTURE_DATE,
        operator=ConstraintOperator.EQUALS,
        value=LocalDate(departure_date),
    )


def departure_preference(
    raw_id: str = "preference-time",
    importance: PreferenceImportance = PreferenceImportance.HIGH,
) -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId(raw_id),
        scope=PreferenceScope.DEPARTURE_TIME,
        importance=importance,
        value=ValueRange(LocalTime(time(8, 0)), LocalTime(time(11, 0))),
    )


def instant(hour: int) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 22, hour, 0, tzinfo=UTC))
