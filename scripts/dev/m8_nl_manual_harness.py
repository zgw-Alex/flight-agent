"""Developer-only M8 natural-language manual test harness."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = REPO_ROOT / "apps" / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from flight_agent.adapters.deepseek_semantic_resolver import (
    deepseek_semantic_resolver_from_config,
)
from flight_agent.application import (
    DeterministicParserHybridInterpreter,
    DeterministicPatchHybridInterpreter,
    SemanticResolverParserHybridInterpreter,
    SemanticResolverPatchHybridInterpreter,
)
from flight_agent.config import Settings
from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    RequirementId,
    RequirementState,
    StopCount,
)
from flight_agent.domain.shared import DomainInstant
from flight_agent.ports import (
    InitialInterpreterPayload,
    InterpreterInput,
    InterpreterMode,
    PatchInterpreterPayload,
    RequirementInterpretationContext,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


CAPABILITY_PARSER = "parser"
CAPABILITY_PATCH = "patch"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.text is not None:
        return _run_once(args.capability, args.text, args.resolver)
    return _run_interactive(args.resolver)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Developer-only M8 natural-language manual test harness."
    )
    parser.add_argument(
        "--capability",
        choices=(CAPABILITY_PARSER, CAPABILITY_PATCH),
        help="Capability to run in non-interactive mode.",
    )
    parser.add_argument("--text", help="Chinese natural-language input for non-interactive mode.")
    parser.add_argument(
        "--resolver",
        choices=("auto", "deterministic", "deepseek"),
        default="auto",
        help=(
            "auto uses DeepSeek only when LLM_REQUIREMENT_INTERPRETER_PROVIDER=deepseek "
            "and DEEPSEEK_API_KEY is configured; deterministic never calls DeepSeek."
        ),
    )
    args = parser.parse_args(argv)
    if (args.capability is None) != (args.text is None):
        parser.error("--capability and --text must be provided together")
    return args


def _run_interactive(resolver_mode: str) -> int:
    print("M8 Natural Language Manual Test Harness")
    print()
    while True:
        print("Capability:")
        print("  1. Initial Parser")
        print("  2. Patch Understanding")
        print("  q. Quit")
        choice = input("> ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            return 0
        capability = { "1": CAPABILITY_PARSER, "2": CAPABILITY_PATCH }.get(choice)
        if capability is None:
            print("Unknown capability.")
            print()
            continue
        print()
        print("Paste Chinese input:")
        text = input("> ")
        print()
        _print_report(run_capability(capability, text, resolver_mode))
        print()


def _run_once(capability: str, text: str, resolver_mode: str) -> int:
    _print_report(run_capability(capability, text, resolver_mode))
    return 0


def run_capability(capability: str, text: str, resolver_mode: str = "auto") -> dict[str, Any]:
    started = time.perf_counter()
    settings = Settings()
    resolved_mode, resolver_note = _resolve_runtime_mode(settings, resolver_mode)
    interpreter = _interpreter_for(capability, resolved_mode, settings)
    context = _patch_context() if capability == CAPABILITY_PATCH else None
    interpreter_input = _interpreter_input(capability, text)
    result = interpreter.interpret(interpreter_input, context)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    ir = getattr(interpreter, "last_ir", None)
    resolver_result = getattr(interpreter, "last_resolver_result", None)
    proposal = result.proposal
    return {
        "capability": "Initial Parser" if capability == CAPABILITY_PARSER else "Patch Understanding",
        "input_text": text,
        "interpretation_status": _status_from(ir, result),
        "routing": _routing_from(ir, resolver_result),
        "deepseek_called": "YES" if resolver_result is not None else "NO",
        "runtime_mode": resolved_mode,
        "runtime_note": resolver_note,
        "result_status": _safe_value(result.status),
        "base_requirement": _base_identity(context),
        "structured_interpretation": _safe_value(ir) if ir is not None else "NOT AVAILABLE",
        "proposal_preview": _safe_value(proposal) if proposal is not None else "NOT AVAILABLE",
        "evidence": _safe_value(getattr(ir, "evidence", ())) if ir is not None else "NOT AVAILABLE",
        "ambiguity_unresolved": _ambiguity_unresolved(ir, proposal),
        "model_identity": _metadata_value(resolver_result, "model_id", settings.deepseek_default_model),
        "prompt_identity": _metadata_value(resolver_result, "prompt_version"),
        "schema_adapter_config_identity": {
            "contract_version": _metadata_value(resolver_result, "contract_version"),
            "adapter_version": _metadata_value(resolver_result, "adapter_version"),
            "provider": _metadata_value(resolver_result, "provider"),
            "configured_provider": settings.llm_requirement_interpreter_provider,
        },
        "invocation_identity": "NOT AVAILABLE",
        "latency_ms": elapsed_ms,
        "authoritative_commit_performed": "NO",
    }


def _resolve_runtime_mode(settings: Settings, resolver_mode: str) -> tuple[str, str]:
    if resolver_mode == "deterministic":
        return "deterministic", "DeepSeek disabled by --resolver deterministic"
    if resolver_mode == "deepseek":
        if settings.deepseek_configured:
            return "deepseek", "DeepSeek enabled by --resolver deepseek"
        return "deterministic", "DeepSeek requested but DEEPSEEK_API_KEY is not configured"
    if settings.real_requirement_interpreter_enabled and settings.deepseek_configured:
        return "deepseek", "DeepSeek enabled by typed settings"
    return "deterministic", "DeepSeek not enabled/configured; deterministic hybrid only"


def _interpreter_for(capability: str, runtime_mode: str, settings: Settings):
    if runtime_mode == "deepseek":
        resolver = deepseek_semantic_resolver_from_config(
            api_key=settings.deepseek_api_key or "",
            base_url=settings.deepseek_base_url,
            model_id=settings.deepseek_default_model,
            timeout_seconds=settings.deepseek_timeout_seconds,
            total_deadline_seconds=settings.deepseek_total_deadline_seconds,
            max_attempts=settings.deepseek_max_attempts,
            invocation_id_factory=lambda: f"m8-manual-{uuid4()}",
        )
        if capability == CAPABILITY_PARSER:
            return SemanticResolverParserHybridInterpreter(resolver)
        return SemanticResolverPatchHybridInterpreter(resolver)
    if capability == CAPABILITY_PARSER:
        return DeterministicParserHybridInterpreter()
    return DeterministicPatchHybridInterpreter()


def _interpreter_input(capability: str, text: str) -> InterpreterInput:
    if capability == CAPABILITY_PARSER:
        return InterpreterInput(InterpreterMode.INITIAL, InitialInterpreterPayload(text))
    return InterpreterInput(InterpreterMode.PATCH, PatchInterpreterPayload(text))


def _patch_context() -> RequirementInterpretationContext:
    current = _fixture_requirement()
    return RequirementInterpretationContext(
        requirement_id=current.requirement_id,
        current_version=current.version,
        constraint_ids=tuple(item.constraint_id for item in current.constraints),
        preference_ids=tuple(item.preference_id for item in current.preferences),
        current_requirement_projection="fixture: requirement-1 v1 with MAX_PRICE=1500 CNY and MAX_STOPS=0",
        current_requirement=current,
    )


def _fixture_requirement() -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=DomainInstant(datetime(2026, 8, 28, 0, 0, tzinfo=UTC)),
        constraints=(
            HardConstraint(
                ConstraintId("origin"),
                ConstraintScope.ORIGIN_AIRPORT,
                ConstraintOperator.EQUALS,
                AirportCode("PEK"),
            ),
            HardConstraint(
                ConstraintId("destination"),
                ConstraintScope.DESTINATION_AIRPORT,
                ConstraintOperator.EQUALS,
                AirportCode("SHA"),
            ),
            HardConstraint(
                ConstraintId("departure-date"),
                ConstraintScope.DEPARTURE_DATE,
                ConstraintOperator.EQUALS,
                LocalDate(date(2026, 9, 10)),
            ),
            HardConstraint(
                ConstraintId("max-price"),
                ConstraintScope.MAX_PRICE,
                ConstraintOperator.AT_OR_BEFORE,
                Money(Decimal(1500), "CNY"),
            ),
            HardConstraint(
                ConstraintId("max-stops"),
                ConstraintScope.MAX_STOPS,
                ConstraintOperator.AT_OR_BEFORE,
                StopCount(0),
            ),
        ),
    )


def _print_report(report: dict[str, Any]) -> None:
    print("[Result]")
    print(f"Capability: {report['capability']}")
    print(f"Input text: {report['input_text']}")
    print(f"Disposition: {report['interpretation_status']}")
    print(f"Routing: {report['routing']}")
    print(f"DeepSeek called: {report['deepseek_called']}")
    print(f"Runtime mode: {report['runtime_mode']}")
    print(f"Runtime note: {report['runtime_note']}")
    if report["base_requirement"] != "NOT AVAILABLE":
        print(f"Base Requirement: {json.dumps(report['base_requirement'], ensure_ascii=False)}")
    print(f"Model identity: {report['model_identity']}")
    print(f"Prompt identity: {report['prompt_identity']}")
    print(
        "Schema/adapter/config identity: "
        f"{json.dumps(report['schema_adapter_config_identity'], ensure_ascii=False)}"
    )
    print(f"Invocation/request identity: {report['invocation_identity']}")
    print(f"Latency: {report['latency_ms']} ms")
    print(f"Authoritative commit performed: {report['authoritative_commit_performed']}")
    print()
    print("[Structured Detail]")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def _status_from(ir: object | None, result: object) -> str:
    for attr in ("disposition", "interpretation_status"):
        value = getattr(ir, attr, None)
        if value is not None:
            return str(_safe_value(value))
    return str(_safe_value(getattr(result, "status", "NOT AVAILABLE")))


def _routing_from(ir: object | None, resolver_result: object | None) -> str:
    if resolver_result is not None:
        return "deepseek_fallback"
    status = _status_from(ir, object())
    if status == "SEMANTIC_RESOLVER_REQUIRED":
        return "semantic_resolver_required_not_called"
    return "deterministic"


def _base_identity(context: RequirementInterpretationContext | None) -> dict[str, Any] | str:
    if context is None:
        return "NOT AVAILABLE"
    return {
        "requirement_id": str(context.requirement_id),
        "version": _safe_value(context.current_version),
        "projection": context.current_requirement_projection,
    }


def _ambiguity_unresolved(ir: object | None, proposal: object | None) -> dict[str, Any]:
    return {
        "ir_issues": _safe_value(getattr(ir, "issues", ())),
        "ir_ambiguities": _safe_value(getattr(ir, "ambiguities", ())),
        "proposal_unresolved_semantics": _safe_value(getattr(proposal, "unresolved_semantics", ())),
        "proposal_ambiguity_reasons": _safe_value(getattr(proposal, "ambiguity_reasons", ())),
        "proposal_insufficient_context": _safe_value(getattr(proposal, "insufficient_context", ())),
    }


def _metadata_value(resolver_result: object | None, key: str, fallback: str = "NOT AVAILABLE") -> str:
    response = getattr(resolver_result, "response", None)
    for meta_key, value in getattr(response, "model_metadata", ()):
        if meta_key == key:
            return value
    return fallback


def _safe_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple | list):
        return [_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if is_dataclass(value):
        return {
            field.name: _safe_value(getattr(value, field.name))
            for field in fields(value)
            if field.name.lower() not in {"api_key", "authorization"}
        }
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
