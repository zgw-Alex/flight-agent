"""Fixture-driven fake requirement interpreter for M3-U1 tests."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.ports import (
    InterpreterFailure,
    InterpreterInput,
    InterpreterMode,
    InterpreterResult,
    RequirementInterpretationContext,
)


@dataclass(frozen=True)
class RequirementInterpreterFixture:
    source_input: str
    mode: InterpreterMode
    result: InterpreterResult


class FakeRequirementInterpreter:
    def __init__(self, fixtures: tuple[RequirementInterpreterFixture, ...]) -> None:
        self._fixtures = {(fixture.mode, fixture.source_input): fixture for fixture in fixtures}

    def interpret(
        self,
        interpreter_input: InterpreterInput,
        context: RequirementInterpretationContext | None = None,
    ) -> InterpreterResult:
        if interpreter_input.mode is InterpreterMode.PATCH and context is None:
            return InterpreterResult.failure_result(
                InterpreterFailure(
                    code="PATCH_CONTEXT_REQUIRED",
                    message="PATCH interpretation requires current requirement context",
                    source_input=interpreter_input.source_input,
                )
            )

        fixture = self._fixtures.get((interpreter_input.mode, interpreter_input.source_input))
        if fixture is None:
            return InterpreterResult.failure_result(
                InterpreterFailure(
                    code="FIXTURE_NOT_FOUND",
                    message="No deterministic requirement interpreter fixture matched the input",
                    source_input=interpreter_input.source_input,
                )
            )
        return fixture.result
