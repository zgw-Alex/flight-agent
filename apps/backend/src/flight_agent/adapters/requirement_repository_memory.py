"""In-memory RequirementRepository adapter for deterministic M3 tests."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.requirements import RequirementId, RequirementState
from flight_agent.domain.shared import RequirementVersion
from flight_agent.ports import RequirementCommitResult


@dataclass(frozen=True)
class _ReplayRecord:
    requirement_id: RequirementId
    version: RequirementVersion


class InMemoryRequirementRepository:
    def __init__(self) -> None:
        self._versions: dict[RequirementId, dict[RequirementVersion, RequirementState]] = {}
        self._current: dict[RequirementId, RequirementVersion] = {}
        self._successful_operations: dict[str, _ReplayRecord] = {}

    def commit_initial(
        self,
        requirement: RequirementState,
        operation_id: str,
    ) -> RequirementCommitResult:
        replay = self._replay(operation_id)
        if replay is not None:
            return replay

        if requirement.version != RequirementVersion(1) or requirement.predecessor_version is not None:
            return RequirementCommitResult.rejected("Initial commit requires v1 with no predecessor")
        if requirement.requirement_id in self._current:
            return RequirementCommitResult.rejected("Initial commit target already exists")

        self._versions[requirement.requirement_id] = {requirement.version: requirement}
        self._current[requirement.requirement_id] = requirement.version
        self._successful_operations[operation_id] = _ReplayRecord(
            requirement_id=requirement.requirement_id,
            version=requirement.version,
        )
        return RequirementCommitResult.committed(requirement)

    def commit_next(
        self,
        requirement: RequirementState,
        expected_current_version: RequirementVersion,
        operation_id: str,
    ) -> RequirementCommitResult:
        replay = self._replay(operation_id)
        if replay is not None:
            return replay

        current_version = self._current.get(requirement.requirement_id)
        if current_version is None:
            return RequirementCommitResult.rejected("Requirement chain does not exist")
        if current_version != expected_current_version:
            return RequirementCommitResult.concurrency_conflict("Current version changed")
        if requirement.predecessor_version != current_version:
            return RequirementCommitResult.rejected("Next commit must reference current predecessor")
        if requirement.version.value != current_version.value + 1:
            return RequirementCommitResult.rejected("Next commit must be the direct next version")
        if requirement.version in self._versions[requirement.requirement_id]:
            return RequirementCommitResult.rejected("Requirement version already exists")

        self._versions[requirement.requirement_id][requirement.version] = requirement
        self._current[requirement.requirement_id] = requirement.version
        self._successful_operations[operation_id] = _ReplayRecord(
            requirement_id=requirement.requirement_id,
            version=requirement.version,
        )
        return RequirementCommitResult.committed(requirement)

    def get_current(self, requirement_id: RequirementId) -> RequirementState | None:
        current_version = self._current.get(requirement_id)
        if current_version is None:
            return None
        return self.get_version(requirement_id, current_version)

    def get_version(
        self,
        requirement_id: RequirementId,
        version: RequirementVersion,
    ) -> RequirementState | None:
        return self._versions.get(requirement_id, {}).get(version)

    def history(self, requirement_id: RequirementId) -> tuple[RequirementState, ...]:
        versions = self._versions.get(requirement_id, {})
        return tuple(versions[version] for version in sorted(versions, key=lambda item: item.value))

    def _replay(self, operation_id: str) -> RequirementCommitResult | None:
        record = self._successful_operations.get(operation_id)
        if record is None:
            return None
        requirement = self.get_version(record.requirement_id, record.version)
        if requirement is None:
            return RequirementCommitResult.rejected("Replay metadata points to missing requirement")
        return RequirementCommitResult.replayed(requirement)
