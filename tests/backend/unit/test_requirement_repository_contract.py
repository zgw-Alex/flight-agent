from __future__ import annotations

from datetime import UTC, date, datetime

from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.application import commit_requirement_transition
from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    RequirementId,
    RequirementState,
)
from flight_agent.domain.shared import DomainInstant, RequirementVersion
from flight_agent.ports import CommitStatus, RequirementRepository


def test_initial_commit_sets_current_and_history() -> None:
    repository = InMemoryRequirementRepository()
    state = initial_requirement("requirement-1")

    result = repository.commit_initial(state, operation_id="op-initial")

    assert result.status is CommitStatus.COMMITTED
    assert result.requirement == state
    assert repository.get_current(RequirementId("requirement-1")) == state
    assert repository.get_version(RequirementId("requirement-1"), RequirementVersion(1)) == state
    assert repository.history(RequirementId("requirement-1")) == (state,)


def test_subsequent_commits_advance_current_without_overwriting_history() -> None:
    repository = InMemoryRequirementRepository()
    v1 = initial_requirement("requirement-1")
    v2 = next_requirement(v1, RequirementVersion(2), "constraint-date-v2")
    v3 = next_requirement(v2, RequirementVersion(3), "constraint-date-v3")
    repository.commit_initial(v1, operation_id="op-initial")

    v2_result = repository.commit_next(
        v2,
        expected_current_version=RequirementVersion(1),
        operation_id="op-v2",
    )
    v3_result = repository.commit_next(
        v3,
        expected_current_version=RequirementVersion(2),
        operation_id="op-v3",
    )

    assert v2_result.status is CommitStatus.COMMITTED
    assert v3_result.status is CommitStatus.COMMITTED
    assert repository.get_current(v1.requirement_id) == v3
    assert repository.get_version(v1.requirement_id, RequirementVersion(1)) == v1
    assert repository.get_version(v1.requirement_id, RequirementVersion(2)) == v2
    assert repository.history(v1.requirement_id) == (v1, v2, v3)


def test_commit_next_uses_expected_current_version_as_cas_boundary() -> None:
    repository = InMemoryRequirementRepository()
    v1 = initial_requirement("requirement-1")
    v2 = next_requirement(v1, RequirementVersion(2), "constraint-date-v2")
    stale_v2 = next_requirement(v1, RequirementVersion(2), "constraint-date-stale")
    repository.commit_initial(v1, operation_id="op-initial")
    repository.commit_next(v2, expected_current_version=RequirementVersion(1), operation_id="op-v2")

    result = repository.commit_next(
        stale_v2,
        expected_current_version=RequirementVersion(1),
        operation_id="op-stale",
    )

    assert result.status is CommitStatus.CONCURRENCY_CONFLICT
    assert result.requirement is None
    assert repository.get_current(v1.requirement_id) == v2
    assert repository.history(v1.requirement_id) == (v1, v2)


def test_successful_operation_replay_returns_existing_result_without_new_version() -> None:
    repository = InMemoryRequirementRepository()
    v1 = initial_requirement("requirement-1")
    v2 = next_requirement(v1, RequirementVersion(2), "constraint-date-v2")
    repository.commit_initial(v1, operation_id="op-initial")

    first = repository.commit_next(
        v2,
        expected_current_version=RequirementVersion(1),
        operation_id="op-v2",
    )
    replay = repository.commit_next(
        v2,
        expected_current_version=RequirementVersion(1),
        operation_id="op-v2",
    )

    assert first.status is CommitStatus.COMMITTED
    assert replay.status is CommitStatus.REPLAYED
    assert replay.requirement == v2
    assert repository.history(v1.requirement_id) == (v1, v2)


def test_different_operation_stale_write_conflicts_after_replay_window() -> None:
    repository = InMemoryRequirementRepository()
    v1 = initial_requirement("requirement-1")
    v2 = next_requirement(v1, RequirementVersion(2), "constraint-date-v2")
    stale_v2 = next_requirement(v1, RequirementVersion(2), "constraint-date-stale")
    repository.commit_initial(v1, operation_id="op-initial")
    repository.commit_next(v2, expected_current_version=RequirementVersion(1), operation_id="op-v2")

    result = repository.commit_next(
        stale_v2,
        expected_current_version=RequirementVersion(1),
        operation_id="op-different-stale",
    )

    assert result.status is CommitStatus.CONCURRENCY_CONFLICT
    assert repository.get_current(v1.requirement_id) == v2


def test_multi_aggregate_histories_are_isolated() -> None:
    repository = InMemoryRequirementRepository()
    first = initial_requirement("requirement-1")
    second = initial_requirement("requirement-2")
    first_v2 = next_requirement(first, RequirementVersion(2), "constraint-date-v2")

    repository.commit_initial(first, operation_id="first-initial")
    repository.commit_initial(second, operation_id="second-initial")
    repository.commit_next(
        first_v2,
        expected_current_version=RequirementVersion(1),
        operation_id="first-v2",
    )

    assert repository.get_current(first.requirement_id) == first_v2
    assert repository.get_current(second.requirement_id) == second
    assert repository.history(first.requirement_id) == (first, first_v2)
    assert repository.history(second.requirement_id) == (second,)


def test_illegal_initial_duplicate_initial_and_broken_chain_reject_without_partial_mutation() -> None:
    repository = InMemoryRequirementRepository()
    v1 = initial_requirement("requirement-1")
    illegal_initial = RequirementState(
        requirement_id=RequirementId("requirement-2"),
        version=RequirementVersion(2),
        predecessor_version=RequirementVersion(1),
        recorded_at=instant(2),
    )
    broken_next = RequirementState(
        requirement_id=v1.requirement_id,
        version=RequirementVersion(3),
        predecessor_version=RequirementVersion(2),
        recorded_at=instant(3),
    )

    illegal_result = repository.commit_initial(illegal_initial, operation_id="illegal-initial")
    repository.commit_initial(v1, operation_id="op-initial")
    duplicate_result = repository.commit_initial(v1, operation_id="duplicate-initial")
    broken_result = repository.commit_next(
        broken_next,
        expected_current_version=RequirementVersion(1),
        operation_id="broken-next",
    )

    assert illegal_result.status is CommitStatus.REJECTED
    assert duplicate_result.status is CommitStatus.REJECTED
    assert broken_result.status is CommitStatus.REJECTED
    assert repository.get_current(v1.requirement_id) == v1
    assert repository.history(v1.requirement_id) == (v1,)
    assert repository.get_current(RequirementId("requirement-2")) is None


def test_requirement_id_mismatch_and_missing_current_reject_without_orphan_version() -> None:
    repository = InMemoryRequirementRepository()
    missing_current_next = RequirementState(
        requirement_id=RequirementId("requirement-2"),
        version=RequirementVersion(2),
        predecessor_version=RequirementVersion(1),
        recorded_at=instant(2),
    )
    v1 = initial_requirement("requirement-1")
    mismatched_next = RequirementState(
        requirement_id=RequirementId("requirement-other"),
        version=RequirementVersion(2),
        predecessor_version=RequirementVersion(1),
        recorded_at=instant(3),
    )
    repository.commit_initial(v1, operation_id="op-initial")

    missing_result = repository.commit_next(
        missing_current_next,
        expected_current_version=RequirementVersion(1),
        operation_id="missing-current",
    )
    mismatch_result = repository.commit_next(
        mismatched_next,
        expected_current_version=RequirementVersion(1),
        operation_id="mismatch",
    )

    assert missing_result.status is CommitStatus.REJECTED
    assert mismatch_result.status is CommitStatus.REJECTED
    assert repository.get_current(RequirementId("requirement-2")) is None
    assert repository.get_current(RequirementId("requirement-other")) is None


def test_application_transition_helper_skips_semantic_no_op_without_commit_call() -> None:
    repository = SpyRequirementRepository()
    v1 = initial_requirement("requirement-1")

    result = commit_requirement_transition(
        repository,
        current=v1,
        candidate=v1,
        operation_id="noop",
    )

    assert result.status is CommitStatus.NO_CHANGE
    assert repository.commit_next_calls == 0


def test_application_transition_helper_uses_repository_port_for_changed_candidate() -> None:
    repository = SpyRequirementRepository()
    v1 = initial_requirement("requirement-1")
    v2 = next_requirement(v1, RequirementVersion(2), "constraint-date-v2")

    result = commit_requirement_transition(
        repository,
        current=v1,
        candidate=v2,
        operation_id="changed",
    )

    assert result.status is CommitStatus.COMMITTED
    assert repository.commit_next_calls == 1
    assert repository.last_expected_current_version == RequirementVersion(1)


class SpyRequirementRepository:
    def __init__(self) -> None:
        self.commit_next_calls = 0
        self.last_expected_current_version: RequirementVersion | None = None

    def commit_initial(self, requirement: RequirementState, operation_id: str):
        raise AssertionError("not used by this helper")

    def commit_next(
        self,
        requirement: RequirementState,
        expected_current_version: RequirementVersion,
        operation_id: str,
    ):
        self.commit_next_calls += 1
        self.last_expected_current_version = expected_current_version
        from flight_agent.ports import RequirementCommitResult

        return RequirementCommitResult.committed(requirement)

    def get_current(self, requirement_id: RequirementId):
        return None

    def get_version(self, requirement_id: RequirementId, version: RequirementVersion):
        return None

    def history(self, requirement_id: RequirementId):
        return ()


def initial_requirement(raw_id: str) -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId(raw_id),
        recorded_at=instant(1),
        constraints=(
            origin_constraint("constraint-origin", "PVG"),
            destination_constraint("constraint-destination", "LAX"),
            date_constraint("constraint-date"),
        ),
    )


def next_requirement(
    previous: RequirementState,
    version: RequirementVersion,
    date_constraint_id: str,
) -> RequirementState:
    return RequirementState(
        requirement_id=previous.requirement_id,
        version=version,
        predecessor_version=previous.version,
        recorded_at=instant(version.value),
        constraints=(
            origin_constraint("constraint-origin", "PVG"),
            destination_constraint("constraint-destination", "LAX"),
            date_constraint(date_constraint_id),
        ),
    )


def origin_constraint(raw_id: str, airport: str) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.ORIGIN_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def destination_constraint(raw_id: str, airport: str) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.DESTINATION_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def date_constraint(raw_id: str) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.DEPARTURE_DATE,
        operator=ConstraintOperator.EQUALS,
        value=LocalDate(date(2026, 9, 1)),
    )


def instant(hour: int) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 22, hour, 0, tzinfo=UTC))


def _assert_port_compatible(repository: RequirementRepository) -> RequirementRepository:
    return repository
