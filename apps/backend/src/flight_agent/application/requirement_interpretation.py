"""Application entry point for requirement interpretation."""

from __future__ import annotations

from flight_agent.ports import (
    InterpreterInput,
    InterpreterResult,
    RequirementInterpretationContext,
    RequirementInterpreter,
)


def interpret_requirement(
    interpreter: RequirementInterpreter,
    interpreter_input: InterpreterInput,
    context: RequirementInterpretationContext | None = None,
) -> InterpreterResult:
    return interpreter.interpret(interpreter_input, context)
