"""Provider-neutral LLM invocation contracts for M8-U3."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from flight_agent.domain.workflow import ExecutionId
from flight_agent.ports.llm_capabilities import CapabilityFailure
from flight_agent.ports.llm_prompting import RenderedPrompt


class LLMProviderName(str, Enum):
    DEEPSEEK = "deepseek"


class LLMInvocationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class LLMProviderFailureCode(str, Enum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_ERROR = "AUTH_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    EMPTY_OUTPUT = "EMPTY_OUTPUT"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"


@dataclass(frozen=True)
class LLMInvocationId:
    value: str

    def __post_init__(self) -> None:
        if self.value.strip() == "":
            raise ValueError("LLMInvocationId requires a non-empty value")


@dataclass(frozen=True)
class LLMInvocationConfig:
    model_id: str
    timeout_seconds: float = 15.0
    total_deadline_seconds: float = 30.0
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.0
    json_output: bool = True
    thinking_enabled: bool = False
    reasoning_effort: str | None = None
    max_tokens: int | None = 512

    def __post_init__(self) -> None:
        if self.model_id.strip() == "":
            raise ValueError("model_id must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.total_deadline_seconds <= 0:
            raise ValueError("total_deadline_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")
        if self.reasoning_effort is not None and self.reasoning_effort.strip() == "":
            raise ValueError("reasoning_effort must be non-empty when provided")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens must be positive when provided")


@dataclass(frozen=True)
class LLMInvocationRequest:
    invocation_id: LLMInvocationId
    rendered_prompt: RenderedPrompt
    provider: LLMProviderName
    config: LLMInvocationConfig
    execution_id: ExecutionId | None = None
    input_context_lineage_ref: str | None = None

    def __post_init__(self) -> None:
        if (
            self.input_context_lineage_ref is not None
            and self.input_context_lineage_ref.strip() == ""
        ):
            raise ValueError("input_context_lineage_ref must be non-empty when provided")


@dataclass(frozen=True)
class LLMUsageMetadata:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.output_tokens, self.total_tokens):
            if value is not None and value < 0:
                raise ValueError("Token counts must not be negative")


@dataclass(frozen=True)
class LLMInvocationTelemetry:
    invocation_id: LLMInvocationId
    execution_id: ExecutionId | None
    capability: str
    provider: LLMProviderName
    model_id: str
    prompt_template_version: str
    output_schema_version: str
    adapter_version: str
    attempt_count: int
    latency_ms: int
    usage: LLMUsageMetadata | None = None
    failure_code: LLMProviderFailureCode | None = None
    repair_attempted: bool = False
    smoke_timestamp: str | None = None

    def __post_init__(self) -> None:
        if self.attempt_count < 1:
            raise ValueError("attempt_count must be at least one")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must not be negative")
        if self.smoke_timestamp is not None and self.smoke_timestamp.strip() == "":
            raise ValueError("smoke_timestamp must be non-empty when provided")


@dataclass(frozen=True)
class LLMInvocationResult:
    status: LLMInvocationStatus
    telemetry: LLMInvocationTelemetry
    output_text: str | None = None
    parsed_json: dict[str, Any] | None = None
    failure: CapabilityFailure | None = None

    def __post_init__(self) -> None:
        if self.status is LLMInvocationStatus.SUCCESS:
            if self.output_text is None or self.failure is not None:
                raise ValueError("Successful invocation requires output_text and no failure")
            return
        if self.failure is None or self.output_text is not None or self.parsed_json is not None:
            raise ValueError("Failed invocation requires failure only")


class LLMTransport(Protocol):
    def invoke(self, request: LLMInvocationRequest, timeout_seconds: float) -> LLMInvocationResult:
        """Invoke the provider once and return a provider-neutral result."""
        ...
