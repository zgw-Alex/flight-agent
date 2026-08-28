"""DeepSeek HTTP adapter for M8-U3 invocation smoke."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from flight_agent.ports import CapabilityFailure, CapabilityFailureKind, LLMCapabilityName
from flight_agent.ports.llm_invocation import (
    LLMInvocationConfig,
    LLMInvocationRequest,
    LLMInvocationResult,
    LLMInvocationStatus,
    LLMInvocationTelemetry,
    LLMProviderFailureCode,
    LLMProviderName,
    LLMUsageMetadata,
)

DEEPSEEK_ADAPTER_VERSION = "deepseek-http-u3"


@dataclass(frozen=True)
class DeepSeekRuntimeConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"

    def __post_init__(self) -> None:
        if self.api_key.strip() == "":
            raise ValueError("DeepSeek api_key must be provided through secret injection")
        if self.base_url.strip() == "":
            raise ValueError("DeepSeek base_url must be non-empty")

    @property
    def authorization_header(self) -> str:
        return f"Bearer {self.api_key}"


@dataclass(frozen=True)
class DeepSeekModelListing:
    provider: LLMProviderName
    model_ids: tuple[str, ...]
    queried_at: str


class DeepSeekHTTPTransport:
    def __init__(
        self,
        config: DeepSeekRuntimeConfig,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._urlopen = urlopen
        self._clock = clock

    def invoke(self, request: LLMInvocationRequest, timeout_seconds: float) -> LLMInvocationResult:
        started = self._clock()
        body = deepseek_chat_completion_body(request)
        http_request = self._json_request("/chat/completions", body)
        try:
            with self._urlopen(http_request, timeout=timeout_seconds) as response:
                response_body = json.loads(response.read().decode("utf-8"))
        except TimeoutError:
            return self._failure(request, LLMProviderFailureCode.TIMEOUT, started)
        except urllib.error.HTTPError as exc:
            return self._http_failure(request, exc, started)
        except urllib.error.URLError:
            return self._failure(request, LLMProviderFailureCode.NETWORK_ERROR, started)
        except OSError:
            return self._failure(request, LLMProviderFailureCode.NETWORK_ERROR, started)
        except json.JSONDecodeError:
            return self._failure(request, LLMProviderFailureCode.PROVIDER_ERROR, started)

        output = _message_content(response_body)
        if output is None or output.strip() == "":
            return self._failure(request, LLMProviderFailureCode.EMPTY_OUTPUT, started)
        return LLMInvocationResult(
            status=LLMInvocationStatus.SUCCESS,
            output_text=output,
            telemetry=self._telemetry(
                request=request,
                started=started,
                attempt_count=1,
                usage=_usage_metadata(response_body),
            ),
        )

    def list_models(self, timeout_seconds: float) -> DeepSeekModelListing:
        http_request = self._json_request("/models", None, method="GET")
        with self._urlopen(http_request, timeout=timeout_seconds) as response:
            response_body = json.loads(response.read().decode("utf-8"))
        model_ids = tuple(
            item["id"] for item in response_body.get("data", ()) if isinstance(item.get("id"), str)
        )
        return DeepSeekModelListing(
            provider=LLMProviderName.DEEPSEEK,
            model_ids=model_ids,
            queried_at=datetime.now(UTC).isoformat(),
        )

    def _json_request(
        self, path: str, body: dict[str, Any] | None, method: str = "POST"
    ) -> urllib.request.Request:
        data = None if body is None else json.dumps(body).encode("utf-8")
        return urllib.request.Request(
            url=f"{self._config.base_url.rstrip('/')}{path}",
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": self._config.authorization_header,
            },
        )

    def _http_failure(
        self,
        request: LLMInvocationRequest,
        error: urllib.error.HTTPError,
        started: float,
    ) -> LLMInvocationResult:
        if error.code in {401, 403}:
            code = LLMProviderFailureCode.AUTH_ERROR
        elif error.code == 429:
            code = LLMProviderFailureCode.RATE_LIMITED
        elif error.code in {500, 502, 503, 504}:
            code = LLMProviderFailureCode.PROVIDER_UNAVAILABLE
        else:
            code = LLMProviderFailureCode.PROVIDER_ERROR
        return self._failure(request, code, started)

    def _failure(
        self,
        request: LLMInvocationRequest,
        code: LLMProviderFailureCode,
        started: float,
    ) -> LLMInvocationResult:
        return LLMInvocationResult(
            status=LLMInvocationStatus.FAILURE,
            failure=CapabilityFailure(
                kind=CapabilityFailureKind.PROVIDER_TRANSPORT_FAILURE
                if code
                in {
                    LLMProviderFailureCode.TIMEOUT,
                    LLMProviderFailureCode.RATE_LIMITED,
                    LLMProviderFailureCode.AUTH_ERROR,
                    LLMProviderFailureCode.NETWORK_ERROR,
                    LLMProviderFailureCode.PROVIDER_UNAVAILABLE,
                    LLMProviderFailureCode.PROVIDER_ERROR,
                }
                else CapabilityFailureKind.MALFORMED_OUTPUT,
                code=code.value,
                message="DeepSeek invocation failed before capability semantic validation",
            ),
            telemetry=self._telemetry(
                request=request,
                started=started,
                attempt_count=1,
                failure_code=code,
            ),
        )

    def _telemetry(
        self,
        *,
        request: LLMInvocationRequest,
        started: float,
        attempt_count: int,
        usage: LLMUsageMetadata | None = None,
        failure_code: LLMProviderFailureCode | None = None,
    ) -> LLMInvocationTelemetry:
        return LLMInvocationTelemetry(
            invocation_id=request.invocation_id,
            execution_id=request.execution_id,
            capability=request.rendered_prompt.family.capability.value,
            provider=LLMProviderName.DEEPSEEK,
            model_id=request.config.model_id,
            prompt_template_version=request.rendered_prompt.family.prompt_template_version.value,
            output_schema_version=request.rendered_prompt.family.output_schema_version.value,
            adapter_version=DEEPSEEK_ADAPTER_VERSION,
            attempt_count=attempt_count,
            latency_ms=max(0, int((self._clock() - started) * 1000)),
            usage=usage,
            failure_code=failure_code,
        )


def deepseek_chat_completion_body(request: LLMInvocationRequest) -> dict[str, Any]:
    user_instruction = (
        "Return one JSON object only for the provider-neutral capability result. "
        "It must include every dataclass field named in the output schema guidance. "
        "The top-level JSON object must be the capability payload itself; do not wrap "
        "it inside status, data, result, output, proposal, or markdown fences. "
        "Use empty arrays for unknown list fields and preserve the source user_message "
        "when a source field is required."
    )
    if request.rendered_prompt.family.capability is LLMCapabilityName.EXPLANATION_GENERATION:
        user_instruction = (
            "Return one JSON object only with keys draft_text, used_evidence, metadata. "
            "draft_text must include the exact selected_offer_id token from trusted context, "
            "must preserve UNKNOWN literally, and must not mention any offer id that is not selected. "
            "used_evidence must be a non-empty array of approved refs as objects with source and identity."
        )
    if request.rendered_prompt.family.capability is LLMCapabilityName.SEMANTIC_RESOLVER:
        user_instruction = (
            "Return exactly one JSON object with keys request_id, status, relations, "
            "unresolved_items, diagnostics, model_metadata. status must be one of RESOLVED, "
            "AMBIGUOUS, INSUFFICIENT_EVIDENCE, UNSUPPORTED, MODEL_FAILURE. Do not use SUCCESS "
            "or FAILURE. Use only relation_kind values from allowed_output_vocabulary in the "
            "trusted context, and only evidence IDs that appear in the trusted context. "
            "relations must be an array of objects with exactly relation_kind, evidence_ids, "
            "target, value, confidence. unresolved_items must be an array of objects with "
            "exactly code, message, evidence_ids, or [] when there are none. diagnostics must "
            "be an array of strings. model_metadata must be an array of objects with exactly "
            "key and value string fields, or [] when there are none."
        )
    body: dict[str, Any] = {
        "model": request.config.model_id,
        "messages": [
            {
                "role": "system",
                "content": request.rendered_prompt.text,
            },
            {
                "role": "user",
                "content": user_instruction,
            },
        ],
        "stream": False,
        "thinking": {"type": "enabled" if request.config.thinking_enabled else "disabled"},
    }
    if request.config.json_output:
        body["response_format"] = {"type": "json_object"}
    if request.config.max_tokens is not None:
        body["max_tokens"] = request.config.max_tokens
    if request.config.reasoning_effort is not None:
        body["reasoning_effort"] = request.config.reasoning_effort
    return body


def invocation_config_from_settings(
    *,
    model_id: str,
    timeout_seconds: float,
    total_deadline_seconds: float,
    max_attempts: int,
) -> LLMInvocationConfig:
    return LLMInvocationConfig(
        model_id=model_id,
        timeout_seconds=timeout_seconds,
        total_deadline_seconds=total_deadline_seconds,
        max_attempts=max_attempts,
        json_output=True,
        thinking_enabled=False,
        max_tokens=2048,
    )


def _message_content(response_body: dict[str, Any]) -> str | None:
    choices = response_body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


def _usage_metadata(response_body: dict[str, Any]) -> LLMUsageMetadata | None:
    usage = response_body.get("usage")
    if not isinstance(usage, dict):
        return None
    return LLMUsageMetadata(
        input_tokens=_int_or_none(usage.get("prompt_tokens")),
        output_tokens=_int_or_none(usage.get("completion_tokens")),
        total_tokens=_int_or_none(usage.get("total_tokens")),
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
