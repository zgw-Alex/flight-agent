"""Requirement interpreter capability boundary for M3."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from flight_agent.domain.requirements import (
    ConstraintId,
    HardConstraint,
    PreferenceId,
    RequirementId,
    SoftPreference,
)
from flight_agent.domain.shared import RequirementVersion


class InterpreterMode(str, Enum):
    INITIAL = "INITIAL"
    PATCH = "PATCH"


@dataclass(frozen=True)
class InitialInterpreterPayload:
    source_input: str


@dataclass(frozen=True)
class PatchInterpreterPayload:
    source_input: str


InterpreterPayload = InitialInterpreterPayload | PatchInterpreterPayload


@dataclass(frozen=True)
class InterpreterInput:
    mode: InterpreterMode
    payload: InterpreterPayload

    def __post_init__(self) -> None:
        if self.mode is InterpreterMode.INITIAL and not isinstance(
            self.payload, InitialInterpreterPayload
        ):
            raise ValueError("INITIAL interpreter input requires InitialInterpreterPayload")
        if self.mode is InterpreterMode.PATCH and not isinstance(
            self.payload, PatchInterpreterPayload
        ):
            raise ValueError("PATCH interpreter input requires PatchInterpreterPayload")

    @property
    def source_input(self) -> str:
        return self.payload.source_input


@dataclass(frozen=True)
class RequirementInterpretationContext:
    requirement_id: RequirementId
    current_version: RequirementVersion
    constraint_ids: tuple[ConstraintId, ...] = ()
    preference_ids: tuple[PreferenceId, ...] = ()


@dataclass(frozen=True)
class InitialRequirementProposal:
    constraints: tuple[HardConstraint, ...] = ()
    preferences: tuple[SoftPreference, ...] = ()
    unresolved_semantics: tuple[str, ...] = ()
    source_input: str = ""


class PatchProposalAction(str, Enum):
    ADD_CONSTRAINT = "ADD_CONSTRAINT"
    ADD_PREFERENCE = "ADD_PREFERENCE"
    REPLACE_CONSTRAINT = "REPLACE_CONSTRAINT"
    REPLACE_PREFERENCE = "REPLACE_PREFERENCE"
    REMOVE_CONSTRAINT = "REMOVE_CONSTRAINT"
    REMOVE_PREFERENCE = "REMOVE_PREFERENCE"
    CLEAR_CONSTRAINTS = "CLEAR_CONSTRAINTS"
    CLEAR_PREFERENCES = "CLEAR_PREFERENCES"


@dataclass(frozen=True)
class PatchProposalOperation:
    action: PatchProposalAction
    item: HardConstraint | SoftPreference | None = None
    target_id: ConstraintId | PreferenceId | None = None


@dataclass(frozen=True)
class PatchRequirementProposal:
    operations: tuple[PatchProposalOperation, ...] = ()
    unresolved_semantics: tuple[str, ...] = ()
    source_input: str = ""


RequirementProposal = InitialRequirementProposal | PatchRequirementProposal


@dataclass(frozen=True)
class InterpreterFailure:
    code: str
    message: str
    source_input: str


class InterpreterResultStatus(str, Enum):
    SUCCESS = "SUCCESS"
    UNRESOLVED = "UNRESOLVED"
    FAILURE = "FAILURE"


@dataclass(frozen=True)
class InterpreterResult:
    status: InterpreterResultStatus
    proposal: RequirementProposal | None = None
    failure: InterpreterFailure | None = None

    @classmethod
    def success(cls, proposal: RequirementProposal) -> InterpreterResult:
        return cls(status=InterpreterResultStatus.SUCCESS, proposal=proposal)

    @classmethod
    def unresolved(cls, proposal: RequirementProposal) -> InterpreterResult:
        return cls(status=InterpreterResultStatus.UNRESOLVED, proposal=proposal)

    @classmethod
    def failure_result(cls, failure: InterpreterFailure) -> InterpreterResult:
        return cls(status=InterpreterResultStatus.FAILURE, failure=failure)

    def __post_init__(self) -> None:
        if self.status is InterpreterResultStatus.FAILURE:
            if self.failure is None or self.proposal is not None:
                raise ValueError("FAILURE interpreter result must carry only InterpreterFailure")
            return
        if self.proposal is None or self.failure is not None:
            raise ValueError("Successful interpreter result must carry only RequirementProposal")


class RequirementInterpreter(Protocol):
    def interpret(
        self,
        interpreter_input: InterpreterInput,
        context: RequirementInterpretationContext | None = None,
    ) -> InterpreterResult:
        """Return a non-authoritative proposal for downstream deterministic handling."""
        ...
