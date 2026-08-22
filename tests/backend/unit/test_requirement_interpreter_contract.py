from __future__ import annotations

import pytest

from flight_agent.adapters.requirement_interpreter_fake import (
    FakeRequirementInterpreter,
    RequirementInterpreterFixture,
)
from flight_agent.application import interpret_requirement
from flight_agent.domain.requirements import (
    AirportCode,
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
)
from flight_agent.domain.shared import RequirementVersion
from flight_agent.ports import (
    InitialInterpreterPayload,
    InitialRequirementProposal,
    InterpreterFailure,
    InterpreterInput,
    InterpreterMode,
    InterpreterResult,
    InterpreterResultStatus,
    PatchInterpreterPayload,
    PatchProposalAction,
    PatchProposalOperation,
    PatchRequirementProposal,
    RequirementInterpretationContext,
)


def test_initial_interpreter_fixture_returns_non_authoritative_proposal() -> None:
    interpreter = FakeRequirementInterpreter((initial_ready_fixture(),))

    result = interpret_requirement(
        interpreter,
        InterpreterInput(
            mode=InterpreterMode.INITIAL,
            payload=InitialInterpreterPayload("initial-ready"),
        ),
    )

    assert result.status is InterpreterResultStatus.SUCCESS
    assert isinstance(result.proposal, InitialRequirementProposal)
    assert result.proposal.constraints == (origin_constraint(), destination_constraint())
    assert not isinstance(result.proposal, RequirementState)
    assert result.proposal.source_input == "initial-ready"


def test_patch_interpreter_fixture_uses_read_only_context_and_references_existing_identity() -> (
    None
):
    interpreter = FakeRequirementInterpreter((patch_fixture(),))
    context = RequirementInterpretationContext(
        requirement_id=RequirementId("requirement-1"),
        current_version=RequirementVersion(1),
        constraint_ids=(ConstraintId("constraint-origin"),),
        preference_ids=(PreferenceId("preference-window"),),
    )

    result = interpret_requirement(
        interpreter,
        InterpreterInput(
            mode=InterpreterMode.PATCH,
            payload=PatchInterpreterPayload("patch-destination"),
        ),
        context,
    )

    assert result.status is InterpreterResultStatus.SUCCESS
    assert isinstance(result.proposal, PatchRequirementProposal)
    assert result.proposal.operations == (
        PatchProposalOperation(
            action=PatchProposalAction.ADD_CONSTRAINT,
            item=destination_constraint(),
        ),
        PatchProposalOperation(
            action=PatchProposalAction.REPLACE_PREFERENCE,
            target_id=PreferenceId("preference-window"),
            item=airport_preference(),
        ),
    )


def test_patch_interpreter_rejects_missing_context_before_proposal() -> None:
    interpreter = FakeRequirementInterpreter((patch_fixture(),))

    result = interpret_requirement(
        interpreter,
        InterpreterInput(
            mode=InterpreterMode.PATCH,
            payload=PatchInterpreterPayload("patch-destination"),
        ),
    )

    assert result.status is InterpreterResultStatus.FAILURE
    assert result.failure == InterpreterFailure(
        code="PATCH_CONTEXT_REQUIRED",
        message="PATCH interpretation requires current requirement context",
        source_input="patch-destination",
    )
    assert result.proposal is None


def test_unresolved_semantics_and_failure_are_distinct_result_shapes() -> None:
    unresolved = InterpreterResult.unresolved(
        InitialRequirementProposal(
            unresolved_semantics=("AMBIGUOUS_AIRPORT",),
            source_input="ambiguous-city",
        )
    )
    failure = InterpreterResult.failure_result(
        InterpreterFailure(
            code="INTERPRETER_UNAVAILABLE",
            message="fixture simulates interpreter failure",
            source_input="explicit-failure",
        )
    )

    assert unresolved.status is InterpreterResultStatus.UNRESOLVED
    assert unresolved.proposal is not None
    assert unresolved.failure is None
    assert failure.status is InterpreterResultStatus.FAILURE
    assert failure.proposal is None
    assert failure.failure is not None


def test_fake_interpreter_returns_unresolved_and_explicit_failure_fixtures() -> None:
    interpreter = FakeRequirementInterpreter(
        (
            RequirementInterpreterFixture(
                source_input="ambiguous-city",
                mode=InterpreterMode.INITIAL,
                result=InterpreterResult.unresolved(
                    InitialRequirementProposal(
                        unresolved_semantics=("AMBIGUOUS_AIRPORT",),
                        source_input="ambiguous-city",
                    )
                ),
            ),
            RequirementInterpreterFixture(
                source_input="explicit-failure",
                mode=InterpreterMode.INITIAL,
                result=InterpreterResult.failure_result(
                    InterpreterFailure(
                        code="INTERPRETER_UNAVAILABLE",
                        message="fixture simulates interpreter failure",
                        source_input="explicit-failure",
                    )
                ),
            ),
        )
    )

    unresolved = interpret_requirement(
        interpreter,
        InterpreterInput(
            mode=InterpreterMode.INITIAL,
            payload=InitialInterpreterPayload("ambiguous-city"),
        ),
    )
    failure = interpret_requirement(
        interpreter,
        InterpreterInput(
            mode=InterpreterMode.INITIAL,
            payload=InitialInterpreterPayload("explicit-failure"),
        ),
    )

    assert unresolved.status is InterpreterResultStatus.UNRESOLVED
    assert failure.status is InterpreterResultStatus.FAILURE


def test_interpreter_input_rejects_typed_payload_mismatch() -> None:
    with pytest.raises(ValueError):
        InterpreterInput(
            mode=InterpreterMode.INITIAL,
            payload=PatchInterpreterPayload("wrong-payload"),
        )

    with pytest.raises(ValueError):
        InterpreterInput(
            mode=InterpreterMode.PATCH,
            payload=InitialInterpreterPayload("wrong-payload"),
        )


def test_fake_interpreter_is_deterministic_for_replay() -> None:
    interpreter = FakeRequirementInterpreter((initial_ready_fixture(),))
    interpreter_input = InterpreterInput(
        mode=InterpreterMode.INITIAL,
        payload=InitialInterpreterPayload("initial-ready"),
    )

    first = interpret_requirement(interpreter, interpreter_input)
    second = interpret_requirement(interpreter, interpreter_input)

    assert first == second


def initial_ready_fixture() -> RequirementInterpreterFixture:
    return RequirementInterpreterFixture(
        source_input="initial-ready",
        mode=InterpreterMode.INITIAL,
        result=InterpreterResult.success(
            InitialRequirementProposal(
                constraints=(origin_constraint(), destination_constraint()),
                preferences=(airport_preference(),),
                source_input="initial-ready",
            )
        ),
    )


def patch_fixture() -> RequirementInterpreterFixture:
    return RequirementInterpreterFixture(
        source_input="patch-destination",
        mode=InterpreterMode.PATCH,
        result=InterpreterResult.success(
            PatchRequirementProposal(
                operations=(
                    PatchProposalOperation(
                        action=PatchProposalAction.ADD_CONSTRAINT,
                        item=destination_constraint(),
                    ),
                    PatchProposalOperation(
                        action=PatchProposalAction.REPLACE_PREFERENCE,
                        target_id=PreferenceId("preference-window"),
                        item=airport_preference(),
                    ),
                ),
                source_input="patch-destination",
            )
        ),
    )


def origin_constraint() -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-origin"),
        scope=ConstraintScope.ORIGIN_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode("PVG"),
    )


def destination_constraint() -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-destination"),
        scope=ConstraintScope.DESTINATION_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode("LAX"),
    )


def airport_preference() -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId("preference-window"),
        scope=PreferenceScope.AIRPORT_MATCH,
        importance=PreferenceImportance.MEDIUM,
        value=AirportCode("SHA"),
    )
