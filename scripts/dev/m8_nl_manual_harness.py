"""Developer-only M8 natural-language manual test harness."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, fields, is_dataclass
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


@dataclass(frozen=True)
class RuntimeSelection:
    requested_resolver: str
    selected_resolver: str
    configured_provider: str
    settings_requirement_interpreter_provider: str
    deepseek_runtime_available: bool
    fallback_used: bool
    runtime_note: str


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
    runtime = _resolve_runtime(settings, resolver_mode)
    interpreter = _interpreter_for(capability, runtime.selected_resolver, settings)
    context = _patch_context() if capability == CAPABILITY_PATCH else None
    interpreter_input = _interpreter_input(capability, text)
    result = interpreter.interpret(interpreter_input, context)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    ir = getattr(interpreter, "last_ir", None)
    resolver_result = getattr(interpreter, "last_resolver_result", None)
    proposal = result.proposal
    actual_provider = _actual_provider(runtime, resolver_result)
    return {
        "capability": "Initial Parser" if capability == CAPABILITY_PARSER else "Patch Understanding",
        "input_text": text,
        "interpretation_status": _status_from(ir, result),
        "routing": _routing_from(ir, resolver_result, runtime),
        "deepseek_called": "YES" if resolver_result is not None else "NO",
        "requested_resolver": runtime.requested_resolver,
        "runtime_mode": runtime.selected_resolver,
        "selected_resolver": runtime.selected_resolver,
        "configured_provider": runtime.configured_provider,
        "settings_requirement_interpreter_provider": runtime.settings_requirement_interpreter_provider,
        "actual_provider": actual_provider,
        "deepseek_runtime_available": "YES" if runtime.deepseek_runtime_available else "NO",
        "fallback_used": "YES" if runtime.fallback_used else "NO",
        "runtime_note": runtime.runtime_note,
        "result_status": _safe_value(result.status),
        "base_requirement": _base_identity(context),
        "structured_interpretation": _safe_value(ir) if ir is not None else "NOT AVAILABLE",
        "proposal_preview": _safe_value(proposal) if proposal is not None else "NOT AVAILABLE",
        "evidence": _safe_value(getattr(ir, "evidence", ())) if ir is not None else "NOT AVAILABLE",
        "ambiguity_unresolved": _ambiguity_unresolved(ir, proposal),
        "configured_model": settings.deepseek_default_model,
        "actual_invoked_model": _metadata_value(resolver_result, "model_id"),
        "model_identity": _metadata_value(resolver_result, "model_id"),
        "prompt_identity": _metadata_value(resolver_result, "prompt_version"),
        "schema_adapter_config_identity": {
            "contract_version": _metadata_value(resolver_result, "contract_version"),
            "adapter_version": _metadata_value(resolver_result, "adapter_version"),
            "actual_provider": actual_provider,
            "configured_provider": runtime.configured_provider,
            "settings_requirement_interpreter_provider": runtime.settings_requirement_interpreter_provider,
        },
        "invocation_identity": _invocation_identity(resolver_result),
        "safe_runtime_failure": _safe_runtime_failure(resolver_result),
        "latency_ms": elapsed_ms,
        "authoritative_commit_performed": "NO",
    }


def _resolve_runtime(settings: Settings, resolver_mode: str) -> RuntimeSelection:
    settings_provider = settings.llm_requirement_interpreter_provider
    if resolver_mode == "deterministic":
        return RuntimeSelection(
            requested_resolver=resolver_mode,
            selected_resolver="deterministic",
            configured_provider="deterministic",
            settings_requirement_interpreter_provider=settings_provider,
            deepseek_runtime_available=settings.deepseek_configured,
            fallback_used=False,
            runtime_note="DeepSeek disabled by --resolver deterministic",
        )
    if resolver_mode == "deepseek":
        if settings.deepseek_configured:
            return RuntimeSelection(
                requested_resolver=resolver_mode,
                selected_resolver="deepseek",
                configured_provider="deepseek",
                settings_requirement_interpreter_provider=settings_provider,
                deepseek_runtime_available=True,
                fallback_used=False,
                runtime_note="DeepSeek runtime selected by --resolver deepseek",
            )
        return RuntimeSelection(
            requested_resolver=resolver_mode,
            selected_resolver="deterministic",
            configured_provider="deepseek",
            settings_requirement_interpreter_provider=settings_provider,
            deepseek_runtime_available=False,
            fallback_used=True,
            runtime_note="DeepSeek requested but DEEPSEEK_API_KEY is not configured; deterministic fallback used",
        )
    if settings.real_requirement_interpreter_enabled and settings.deepseek_configured:
        return RuntimeSelection(
            requested_resolver=resolver_mode,
            selected_resolver="deepseek",
            configured_provider=settings_provider,
            settings_requirement_interpreter_provider=settings_provider,
            deepseek_runtime_available=True,
            fallback_used=False,
            runtime_note="DeepSeek enabled by typed settings",
        )
    return RuntimeSelection(
        requested_resolver=resolver_mode,
        selected_resolver="deterministic",
        configured_provider=settings_provider,
        settings_requirement_interpreter_provider=settings_provider,
        deepseek_runtime_available=settings.deepseek_configured,
        fallback_used=False,
        runtime_note="DeepSeek not enabled/configured; deterministic hybrid only",
    )


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
    print(f"Requested resolver: {report['requested_resolver']}")
    print(f"Runtime mode: {report['runtime_mode']}")
    print(f"Configured provider: {report['configured_provider']}")
    print(
        "Settings requirement interpreter provider: "
        f"{report['settings_requirement_interpreter_provider']}"
    )
    print(f"Actual provider: {report['actual_provider']}")
    print(f"DeepSeek runtime available: {report['deepseek_runtime_available']}")
    print(f"Fallback used: {report['fallback_used']}")
    print(f"Runtime note: {report['runtime_note']}")
    if report["base_requirement"] != "NOT AVAILABLE":
        print(f"Base Requirement: {json.dumps(report['base_requirement'], ensure_ascii=False)}")
    print(f"Configured model: {report['configured_model']}")
    print(f"Actual invoked model: {report['actual_invoked_model']}")
    print(f"Prompt identity: {report['prompt_identity']}")
    print(
        "Schema/adapter/config identity: "
        f"{json.dumps(report['schema_adapter_config_identity'], ensure_ascii=False)}"
    )
    print(f"Invocation/request identity: {report['invocation_identity']}")
    print(f"Safe runtime failure: {report['safe_runtime_failure']}")
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


def _routing_from(ir: object | None, resolver_result: object | None, runtime: RuntimeSelection) -> str:
    if resolver_result is not None:
        return "deepseek_resolver_invoked"
    status = _status_from(ir, object())
    if status == "SEMANTIC_RESOLVER_REQUIRED":
        if runtime.requested_resolver == "deepseek" and not runtime.deepseek_runtime_available:
            return "semantic_resolver_required_but_deepseek_unavailable"
        return "semantic_resolver_required_not_called"
    if runtime.selected_resolver == "deepseek":
        return "deterministic_front_half_no_resolver_call"
    return "deterministic"


def _actual_provider(runtime: RuntimeSelection, resolver_result: object | None) -> str:
    provider = _metadata_value(resolver_result, "provider")
    if provider != "NOT AVAILABLE":
        return provider
    if resolver_result is not None and runtime.selected_resolver == "deepseek":
        return "DEEPSEEK"
    if runtime.selected_resolver == "deepseek":
        return "NOT_INVOKED"
    return "DETERMINISTIC_ONLY"


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


def _invocation_identity(resolver_result: object | None) -> str:
    response = getattr(resolver_result, "response", None)
    request_id = getattr(response, "request_id", None)
    if isinstance(request_id, str) and request_id.strip():
        return request_id
    return "NOT AVAILABLE"


def _safe_runtime_failure(resolver_result: object | None) -> dict[str, Any] | str:
    failure = getattr(resolver_result, "failure", None)
    if failure is None:
        return "NONE"
    return {
        "kind": _safe_value(getattr(failure, "kind", "NOT AVAILABLE")),
        "code": _safe_value(getattr(failure, "code", "NOT AVAILABLE")),
        "message": _safe_value(getattr(failure, "message", "NOT AVAILABLE")),
        "retryable": _safe_value(getattr(failure, "retryable", "NOT AVAILABLE")),
    }


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
