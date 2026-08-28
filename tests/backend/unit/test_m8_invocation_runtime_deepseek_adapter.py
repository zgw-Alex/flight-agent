from __future__ import annotations

import json
import urllib.error
from collections.abc import Callable
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Any, Self

from flight_agent.adapters.deepseek_llm import (
    DEEPSEEK_ADAPTER_VERSION,
    DeepSeekHTTPTransport,
    DeepSeekRuntimeConfig,
    deepseek_chat_completion_body,
    invocation_config_from_settings,
)
from flight_agent.application.llm_invocation import (
    LLMInvocationRuntime,
    parse_json_output,
    repair_json_representation,
    structured_output_payload,
    validate_structured_output_schema,
)
from flight_agent.application.llm_prompting import (
    INITIAL_REQUIREMENT_PROMPT_FAMILY,
    build_initial_requirement_prompt_context,
    load_runtime_prompt_template,
    render_prompt,
)
from flight_agent.config import Settings
from flight_agent.domain.workflow import ExecutionId
from flight_agent.ports import (
    CapabilityFailure,
    CapabilityFailureKind,
    InitialRequirementInterpretationRequest,
    LLMCapabilityName,
    LLMInvocationConfig,
    LLMInvocationId,
    LLMInvocationRequest,
    LLMInvocationResult,
    LLMInvocationStatus,
    LLMInvocationTelemetry,
    LLMProviderFailureCode,
    LLMProviderName,
    LLMUsageMetadata,
    PromptRenderRequest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_invocation_identity_is_distinct_from_business_execution_id() -> None:
    request = invocation_request()

    assert request.invocation_id == LLMInvocationId("llm-invocation-1")
    assert request.execution_id == ExecutionId("execution-1")
    assert request.execution_id is not None
    assert request.invocation_id.value != request.execution_id.value


def test_deepseek_request_mapping_uses_rendered_prompt_json_mode_and_no_provider_sdk() -> None:
    request = invocation_request()

    body = deepseek_chat_completion_body(request)

    assert body["model"] == "deepseek-v4-flash"
    assert body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"] == request.rendered_prompt.text
    assert "api_key" not in json.dumps(body)


def test_deepseek_auth_header_is_injected_without_telemetry_exposure() -> None:
    captured: dict[str, Any] = {}
    transport = DeepSeekHTTPTransport(
        DeepSeekRuntimeConfig(api_key="secret-test-key"),
        urlopen=successful_urlopen(captured, schema_valid_output()),
        clock=static_clock(),
    )

    result = transport.invoke(invocation_request(), timeout_seconds=2)

    assert result.status is LLMInvocationStatus.SUCCESS
    assert captured["headers"]["Authorization"] == "Bearer secret-test-key"
    assert "secret-test-key" not in repr(result.telemetry)
    assert "Authorization" not in repr(result.telemetry)


def test_model_listing_captures_current_accessible_model_ids_without_secret() -> None:
    captured: dict[str, Any] = {}
    transport = DeepSeekHTTPTransport(
        DeepSeekRuntimeConfig(api_key="secret-test-key"),
        urlopen=successful_urlopen(
            captured,
            {"object": "list", "data": [{"id": "deepseek-v4-flash"}, {"id": "deepseek-v4-pro"}]},
        ),
        clock=static_clock(),
    )

    listing = transport.list_models(timeout_seconds=2)

    assert listing.provider is LLMProviderName.DEEPSEEK
    assert listing.model_ids == ("deepseek-v4-flash", "deepseek-v4-pro")
    assert captured["url"].endswith("/models")
    assert "secret-test-key" not in repr(listing)


def test_invocation_runtime_parses_json_and_validates_u1_schema_surface() -> None:
    runtime = LLMInvocationRuntime(
        FakeTransport(
            (
                invocation_success(schema_valid_output_text()),
            )
        )
    )

    result = runtime.invoke(invocation_request())

    assert result.status is LLMInvocationStatus.SUCCESS
    assert result.parsed_json is not None
    assert validate_structured_output_schema(
        LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION,
        result.parsed_json,
    )


def test_invocation_runtime_accepts_capability_result_output_wrapper() -> None:
    wrapped_output = json.dumps(
        {
            "status": "SUCCESS",
            "metadata": {
                "capability": "INITIAL_REQUIREMENT_INTERPRETATION",
                "output_schema_version": "m8-u1",
                "adapter_version": DEEPSEEK_ADAPTER_VERSION,
            },
            "output": schema_valid_payload(),
            "semantic_validation": {"is_semantically_valid": True, "issues": []},
        }
    )
    runtime = LLMInvocationRuntime(FakeTransport((invocation_success(wrapped_output),)))

    result = runtime.invoke(invocation_request())

    assert result.status is LLMInvocationStatus.SUCCESS
    assert result.parsed_json == schema_valid_payload()
    assert structured_output_payload(
        LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION,
        {"output": schema_valid_payload()},
    ) == schema_valid_payload()


def test_invocation_runtime_accepts_provider_result_proposal_wrapper() -> None:
    wrapped_output = json.dumps(
        {
            "capability": "InitialRequirementProposal",
            "result": {
                "status": "SUCCESS",
                "proposal": schema_valid_payload(),
            },
        }
    )
    runtime = LLMInvocationRuntime(FakeTransport((invocation_success(wrapped_output),)))

    result = runtime.invoke(invocation_request())

    assert result.status is LLMInvocationStatus.SUCCESS
    assert result.parsed_json == schema_valid_payload()


def test_invocation_runtime_accepts_provider_data_wrapper() -> None:
    wrapped_output = json.dumps(
        {
            "status": "SUCCESS",
            "data": schema_valid_payload(),
        }
    )
    runtime = LLMInvocationRuntime(FakeTransport((invocation_success(wrapped_output),)))

    result = runtime.invoke(invocation_request())

    assert result.status is LLMInvocationStatus.SUCCESS
    assert result.parsed_json == schema_valid_payload()


def test_malformed_empty_and_schema_invalid_outputs_are_failure_paths() -> None:
    empty_result = parse_json_output("")
    malformed_result = parse_json_output("{not-json")

    assert empty_result.failure is not None
    assert malformed_result.failure is not None
    assert empty_result.failure.code == LLMProviderFailureCode.EMPTY_OUTPUT.value
    assert malformed_result.failure.code == LLMProviderFailureCode.MALFORMED_OUTPUT.value

    runtime = LLMInvocationRuntime(FakeTransport((invocation_success('{"unexpected": true}'),)))
    result = runtime.invoke(invocation_request())

    assert result.status is LLMInvocationStatus.FAILURE
    assert result.failure == CapabilityFailure(
        kind=CapabilityFailureKind.SCHEMA_INVALID,
        code=LLMProviderFailureCode.SCHEMA_INVALID.value,
        message="Provider output JSON does not match the U1 capability schema surface",
    )


def test_structural_repair_removes_json_fences_without_creating_semantics() -> None:
    repaired, attempted = repair_json_representation('```json\n{"source_input": "x"}\n```')

    assert attempted is True
    assert repaired == '{"source_input": "x"}'
    assert "constraints" not in repaired
    assert "preferences" not in repaired


def test_retry_is_bounded_and_only_for_retryable_failures() -> None:
    retryable_transport = FakeTransport(
        (
            invocation_failure(LLMProviderFailureCode.RATE_LIMITED),
            invocation_success(schema_valid_output_text(), attempt_count=2),
        )
    )
    non_retryable_transport = FakeTransport(
        (
            invocation_failure(LLMProviderFailureCode.AUTH_ERROR),
            invocation_success(schema_valid_output_text(), attempt_count=2),
        )
    )

    retryable = LLMInvocationRuntime(retryable_transport).invoke(invocation_request(max_attempts=2))
    non_retryable = LLMInvocationRuntime(non_retryable_transport).invoke(
        invocation_request(max_attempts=2)
    )

    assert retryable.status is LLMInvocationStatus.SUCCESS
    assert retryable_transport.calls == 2
    assert non_retryable.status is LLMInvocationStatus.FAILURE
    assert non_retryable_transport.calls == 1


def test_deadline_prevents_unbounded_retry() -> None:
    clock = AdvancingClock((0.0, 31.0))
    runtime = LLMInvocationRuntime(
        FakeTransport((invocation_failure(LLMProviderFailureCode.RATE_LIMITED),)),
        monotonic_clock=clock,
    )

    result = runtime.invoke(invocation_request(max_attempts=3))

    assert result.status is LLMInvocationStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == LLMProviderFailureCode.DEADLINE_EXCEEDED.value


def test_deepseek_provider_failure_mapping() -> None:
    cases = (
        (http_error(401), LLMProviderFailureCode.AUTH_ERROR),
        (http_error(429), LLMProviderFailureCode.RATE_LIMITED),
        (http_error(503), LLMProviderFailureCode.PROVIDER_UNAVAILABLE),
        (urllib.error.URLError("offline"), LLMProviderFailureCode.NETWORK_ERROR),
        (TimeoutError(), LLMProviderFailureCode.TIMEOUT),
    )

    for error, expected in cases:
        transport = DeepSeekHTTPTransport(
            DeepSeekRuntimeConfig(api_key="secret-test-key"),
            urlopen=failing_urlopen(error),
            clock=static_clock(),
        )

        result = transport.invoke(invocation_request(), timeout_seconds=2)

        assert result.status is LLMInvocationStatus.FAILURE
        assert result.failure is not None
        assert result.failure.code == expected.value
        assert result.telemetry.failure_code is expected


def test_usage_and_redacted_telemetry_capture_non_sensitive_metadata() -> None:
    transport = DeepSeekHTTPTransport(
        DeepSeekRuntimeConfig(api_key="secret-test-key"),
        urlopen=successful_urlopen(
            {},
            schema_valid_output(),
            usage={"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
        ),
        clock=static_clock(),
    )

    result = transport.invoke(invocation_request(), timeout_seconds=2)

    assert result.telemetry.adapter_version == DEEPSEEK_ADAPTER_VERSION
    assert result.telemetry.usage == LLMUsageMetadata(4, 6, 10)
    assert "secret-test-key" not in repr(result.telemetry)
    assert "synthetic non-sensitive" not in repr(result.telemetry)
    assert result.output_text is not None
    assert result.output_text not in repr(result.telemetry)


def test_settings_secret_injection_reports_configured_without_key_leak(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-test-key")
    monkeypatch.setenv("DEEPSEEK_DEFAULT_MODEL", "deepseek-v4-pro")

    settings = Settings()

    assert settings.deepseek_configured is True
    assert settings.deepseek_default_model == "deepseek-v4-pro"
    assert "secret-test-key" not in repr(settings.model_dump(exclude={"deepseek_api_key"}))


def test_health_and_ordinary_ci_are_separated_from_real_llm_smoke() -> None:
    health_source = (REPO_ROOT / "apps" / "backend" / "src" / "flight_agent" / "api" / "health.py").read_text(
        encoding="utf-8"
    )
    all_ci = (REPO_ROOT / "scripts" / "ci" / "all.ps1").read_text(encoding="utf-8")
    smoke = REPO_ROOT / "scripts" / "ci" / "real-llm-smoke.ps1"

    assert smoke.exists()
    assert "DeepSeek" not in health_source
    assert "deepseek" not in health_source
    assert "real-llm-smoke" not in all_ci


def invocation_request(max_attempts: int = 1) -> LLMInvocationRequest:
    rendered_prompt = render_prompt(
        PromptRenderRequest(
            load_runtime_prompt_template(INITIAL_REQUIREMENT_PROMPT_FAMILY),
            build_initial_requirement_prompt_context(
                InitialRequirementInterpretationRequest("synthetic non-sensitive request")
            ),
        )
    )
    return LLMInvocationRequest(
        invocation_id=LLMInvocationId("llm-invocation-1"),
        execution_id=ExecutionId("execution-1"),
        rendered_prompt=rendered_prompt,
        provider=LLMProviderName.DEEPSEEK,
        config=LLMInvocationConfig(
            model_id="deepseek-v4-flash",
            timeout_seconds=2,
            total_deadline_seconds=30,
            max_attempts=max_attempts,
        ),
        input_context_lineage_ref="test-lineage",
    )


def schema_valid_output_text() -> str:
    return json.dumps(schema_valid_payload())


def schema_valid_output() -> dict[str, Any]:
    return chat_completion_response(schema_valid_output_text())


def schema_valid_payload() -> dict[str, Any]:
    return {
        "constraints": [],
        "preferences": [],
        "unresolved_semantics": [],
        "source_input": "synthetic",
        "evidence": [],
        "ambiguity_reasons": [],
        "insufficient_context": [],
    }


def chat_completion_response(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "choices": [{"message": {"role": "assistant", "content": content}}],
    }


def successful_urlopen(
    captured: dict[str, Any],
    response_body: dict[str, Any],
    usage: dict[str, int] | None = None,
) -> Callable[..., FakeHTTPResponse]:
    if usage is not None:
        response_body = {**response_body, "usage": usage}

    def _urlopen(request, timeout: float) -> FakeHTTPResponse:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return FakeHTTPResponse(response_body)

    return _urlopen


def failing_urlopen(error: Exception) -> Callable[..., FakeHTTPResponse]:
    def _urlopen(request, timeout: float) -> FakeHTTPResponse:
        raise error

    return _urlopen


def http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.deepseek.com/chat/completions",
        code=status,
        msg="simulated",
        hdrs=Message(),
        fp=None,
    )


class FakeHTTPResponse:
    def __init__(self, response_body: dict[str, Any]) -> None:
        self._body = json.dumps(response_body).encode("utf-8")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return BytesIO(self._body).read()


class FakeTransport:
    def __init__(self, results: tuple[LLMInvocationResult, ...]) -> None:
        self._results = list(results)
        self.calls = 0

    def invoke(self, request: LLMInvocationRequest, timeout_seconds: float) -> LLMInvocationResult:
        self.calls += 1
        return self._results.pop(0)


def invocation_success(output_text: str, attempt_count: int = 1) -> LLMInvocationResult:
    return LLMInvocationResult(
        status=LLMInvocationStatus.SUCCESS,
        output_text=output_text,
        telemetry=telemetry(attempt_count=attempt_count),
    )


def invocation_failure(code: LLMProviderFailureCode) -> LLMInvocationResult:
    return LLMInvocationResult(
        status=LLMInvocationStatus.FAILURE,
        failure=CapabilityFailure(
            kind=CapabilityFailureKind.PROVIDER_TRANSPORT_FAILURE,
            code=code.value,
            message="simulated provider failure",
        ),
        telemetry=telemetry(failure_code=code),
    )


def telemetry(
    attempt_count: int = 1,
    failure_code: LLMProviderFailureCode | None = None,
) -> LLMInvocationTelemetry:
    return LLMInvocationTelemetry(
        invocation_id=LLMInvocationId("llm-invocation-1"),
        execution_id=ExecutionId("execution-1"),
        capability=LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION.value,
        provider=LLMProviderName.DEEPSEEK,
        model_id="deepseek-v4-flash",
        prompt_template_version="initial-requirement-v2",
        output_schema_version="m8-u1",
        adapter_version=DEEPSEEK_ADAPTER_VERSION,
        attempt_count=attempt_count,
        latency_ms=1,
        failure_code=failure_code,
    )


def static_clock() -> Callable[[], float]:
    return lambda: 0.0


class AdvancingClock:
    def __init__(self, values: tuple[float, ...]) -> None:
        self._values = list(values)

    def __call__(self) -> float:
        return self._values.pop(0) if self._values else 31.0


def test_invocation_config_from_settings_preserves_typed_boundaries() -> None:
    config = invocation_config_from_settings(
        model_id="deepseek-v4-flash",
        timeout_seconds=3,
        total_deadline_seconds=5,
        max_attempts=2,
    )

    assert config == LLMInvocationConfig(
        model_id="deepseek-v4-flash",
        timeout_seconds=3,
        total_deadline_seconds=5,
        max_attempts=2,
        json_output=True,
        thinking_enabled=False,
        max_tokens=2048,
    )
