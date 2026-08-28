"""Provider-neutral LLM invocation runtime for M8-U3."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import fields, is_dataclass
from typing import Any

from flight_agent.ports import (
    CapabilityFailure,
    CapabilityFailureKind,
    ExplanationDraft,
    InitialRequirementProposal,
    LLMCapabilityName,
    PatchRequirementProposal,
)
from flight_agent.ports.llm_invocation import (
    LLMInvocationRequest,
    LLMInvocationResult,
    LLMInvocationStatus,
    LLMInvocationTelemetry,
    LLMProviderFailureCode,
    LLMTransport,
)

RETRYABLE_FAILURES = frozenset(
    {
        LLMProviderFailureCode.TIMEOUT,
        LLMProviderFailureCode.RATE_LIMITED,
        LLMProviderFailureCode.NETWORK_ERROR,
        LLMProviderFailureCode.PROVIDER_UNAVAILABLE,
    }
)


class LLMInvocationRuntime:
    def __init__(
        self,
        transport: LLMTransport,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self._monotonic_clock = monotonic_clock
        self._sleep = sleep

    def invoke(self, request: LLMInvocationRequest) -> LLMInvocationResult:
        started_at = self._monotonic_clock()
        last_result: LLMInvocationResult | None = None

        for attempt in range(1, request.config.max_attempts + 1):
            remaining = request.config.total_deadline_seconds - (
                self._monotonic_clock() - started_at
            )
            if remaining <= 0:
                return _deadline_exceeded(request, attempt)

            result = self._transport.invoke(
                request,
                timeout_seconds=min(request.config.timeout_seconds, remaining),
            )
            last_result = result
            if result.status is LLMInvocationStatus.SUCCESS:
                parsed = parse_json_output(result.output_text or "")
                if parsed.status is LLMInvocationStatus.SUCCESS:
                    structured_payload = structured_output_payload(
                        request.rendered_prompt.family.capability,
                        parsed.parsed_json or {},
                    )
                    if not validate_structured_output_schema(
                        request.rendered_prompt.family.capability,
                        structured_payload,
                    ):
                        return _schema_invalid(result)
                    return LLMInvocationResult(
                        status=LLMInvocationStatus.SUCCESS,
                        output_text=result.output_text,
                        parsed_json=structured_payload,
                        telemetry=result.telemetry,
                    )
                return parsed

            failure_code = _failure_code(result)
            if attempt >= request.config.max_attempts or failure_code not in RETRYABLE_FAILURES:
                return result
            if request.config.retry_backoff_seconds > 0:
                self._sleep(request.config.retry_backoff_seconds)

        if last_result is None:
            return _deadline_exceeded(request, 1)
        return last_result


def parse_json_output(output_text: str) -> LLMInvocationResult:
    repaired_text, repair_attempted = repair_json_representation(output_text)
    if repaired_text.strip() == "":
        return _parse_failure(LLMProviderFailureCode.EMPTY_OUTPUT, repair_attempted)
    try:
        parsed = json.loads(repaired_text)
    except json.JSONDecodeError:
        return _parse_failure(LLMProviderFailureCode.MALFORMED_OUTPUT, repair_attempted)
    if not isinstance(parsed, dict):
        return _parse_failure(LLMProviderFailureCode.SCHEMA_INVALID, repair_attempted)
    return LLMInvocationResult(
        status=LLMInvocationStatus.SUCCESS,
        output_text=repaired_text,
        parsed_json=parsed,
        telemetry=_parse_telemetry(repair_attempted),
    )


def repair_json_representation(output_text: str) -> tuple[str, bool]:
    stripped = output_text.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        return stripped.removeprefix("```json").removesuffix("```").strip(), True
    if stripped.startswith("```") and stripped.endswith("```"):
        return stripped.removeprefix("```").removesuffix("```").strip(), True
    return output_text, False


def validate_structured_output_schema(
    capability: LLMCapabilityName, parsed_json: dict[str, Any]
) -> bool:
    expected = _expected_output_fields(capability)
    return expected.issubset(parsed_json.keys())


def structured_output_payload(
    capability: LLMCapabilityName, parsed_json: dict[str, Any]
) -> dict[str, Any]:
    if validate_structured_output_schema(capability, parsed_json):
        return parsed_json
    for candidate in _structured_output_candidates(parsed_json):
        if validate_structured_output_schema(capability, candidate):
            return candidate
    return parsed_json


def _structured_output_candidates(parsed_json: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    candidates: list[dict[str, Any]] = []
    for key in ("output", "proposal", "data", "requirement_proposal"):
        value = parsed_json.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    result = parsed_json.get("result")
    if isinstance(result, dict):
        for key in ("output", "proposal", "data", "requirement_proposal"):
            value = result.get(key)
            if isinstance(value, dict):
                candidates.append(value)
    return tuple(candidates)


def _expected_output_fields(capability: LLMCapabilityName) -> frozenset[str]:
    if capability is LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION:
        return frozenset(field.name for field in fields(InitialRequirementProposal))
    if capability is LLMCapabilityName.PATCH_UNDERSTANDING:
        return frozenset(field.name for field in fields(PatchRequirementProposal))
    if capability is LLMCapabilityName.SEMANTIC_RESOLVER:
        return frozenset(
            {
                "request_id",
                "status",
                "relations",
                "unresolved_items",
                "diagnostics",
                "model_metadata",
            }
        )
    return frozenset(field.name for field in fields(ExplanationDraft))


def _parse_failure(
    code: LLMProviderFailureCode, repair_attempted: bool
) -> LLMInvocationResult:
    return LLMInvocationResult(
        status=LLMInvocationStatus.FAILURE,
        failure=CapabilityFailure(
            kind=CapabilityFailureKind.MALFORMED_OUTPUT
            if code is LLMProviderFailureCode.MALFORMED_OUTPUT
            else CapabilityFailureKind.SCHEMA_INVALID,
            code=code.value,
            message="Provider output failed deterministic JSON/schema parsing",
        ),
        telemetry=_parse_telemetry(repair_attempted, failure_code=code),
    )


def _parse_telemetry(
    repair_attempted: bool,
    failure_code: LLMProviderFailureCode | None = None,
) -> LLMInvocationTelemetry:
    from flight_agent.ports.llm_invocation import (
        LLMInvocationId,
        LLMProviderName,
    )

    return LLMInvocationTelemetry(
        invocation_id=LLMInvocationId("local-parse"),
        execution_id=None,
        capability="STRUCTURED_OUTPUT_PARSE",
        provider=LLMProviderName.DEEPSEEK,
        model_id="local-parser",
        prompt_template_version="n/a",
        output_schema_version="m8-u1",
        adapter_version="local-parser",
        attempt_count=1,
        latency_ms=0,
        failure_code=failure_code,
        repair_attempted=repair_attempted,
    )


def _failure_code(result: LLMInvocationResult) -> LLMProviderFailureCode | None:
    if result.telemetry.failure_code is not None:
        return result.telemetry.failure_code
    if result.failure is not None:
        try:
            return LLMProviderFailureCode(result.failure.code)
        except ValueError:
            return None
    return None


def _deadline_exceeded(request: LLMInvocationRequest, attempt: int) -> LLMInvocationResult:
    from flight_agent.ports.llm_invocation import LLMInvocationTelemetry

    return LLMInvocationResult(
        status=LLMInvocationStatus.FAILURE,
        failure=CapabilityFailure(
            kind=CapabilityFailureKind.PROVIDER_TRANSPORT_FAILURE,
            code=LLMProviderFailureCode.DEADLINE_EXCEEDED.value,
            message="LLM invocation total deadline was exceeded",
        ),
        telemetry=LLMInvocationTelemetry(
            invocation_id=request.invocation_id,
            execution_id=request.execution_id,
            capability=request.rendered_prompt.family.capability.value,
            provider=request.provider,
            model_id=request.config.model_id,
            prompt_template_version=request.rendered_prompt.family.prompt_template_version.value,
            output_schema_version=request.rendered_prompt.family.output_schema_version.value,
            adapter_version="invocation-runtime",
            attempt_count=attempt,
            latency_ms=0,
            failure_code=LLMProviderFailureCode.DEADLINE_EXCEEDED,
        ),
    )


def _schema_invalid(result: LLMInvocationResult) -> LLMInvocationResult:
    return LLMInvocationResult(
        status=LLMInvocationStatus.FAILURE,
        failure=CapabilityFailure(
            kind=CapabilityFailureKind.SCHEMA_INVALID,
            code=LLMProviderFailureCode.SCHEMA_INVALID.value,
            message="Provider output JSON does not match the U1 capability schema surface",
        ),
        telemetry=result.telemetry,
    )


assert is_dataclass(InitialRequirementProposal)
assert is_dataclass(PatchRequirementProposal)
assert is_dataclass(ExplanationDraft)
