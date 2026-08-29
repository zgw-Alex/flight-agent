"""Evidence-closed semantic resolver contracts for M8-U6H."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

SEMANTIC_RESOLVER_CONTRACT_VERSION = "m8-u6h-e-v1.0"
SEMANTIC_RESOLVER_PROMPT_VERSION_V1 = "m8-u6h-c-semantic-resolver-prompt-v1"
SEMANTIC_RESOLVER_PROMPT_VERSION_V2 = "m8-u6h-e-semantic-resolver-prompt-v2"
SEMANTIC_RESOLVER_PROMPT_VERSION = SEMANTIC_RESOLVER_PROMPT_VERSION_V2


class SemanticResolverTaskKind(str, Enum):
    PARSER = "PARSER"
    PATCH = "PATCH"


class SemanticResolverStatus(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNSUPPORTED = "UNSUPPORTED"
    MODEL_FAILURE = "MODEL_FAILURE"


class SemanticResolverFailureKind(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    CONFIGURATION = "CONFIGURATION"
    AUTHENTICATION = "AUTHENTICATION"
    BILLING = "BILLING"
    RATE_LIMIT = "RATE_LIMIT"
    TRANSIENT = "TRANSIENT"
    TIMEOUT = "TIMEOUT"
    MODEL_CONTRACT = "MODEL_CONTRACT"
    EVIDENCE_CLOSURE = "EVIDENCE_CLOSURE"
    UNSUPPORTED = "UNSUPPORTED"
    AMBIGUOUS = "AMBIGUOUS"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class SemanticResolverEvidence:
    evidence_id: str
    kind: str
    source_text: str
    normalized_text: str | None = None

    def __post_init__(self) -> None:
        if self.evidence_id.strip() == "":
            raise ValueError("evidence_id must be non-empty")
        if self.kind.strip() == "":
            raise ValueError("evidence kind must be non-empty")
        if self.source_text.strip() == "":
            raise ValueError("source_text must be non-empty")


@dataclass(frozen=True)
class SemanticResolverRelation:
    relation_kind: str
    evidence_ids: tuple[str, ...]
    target: str | None = None
    value: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.relation_kind.strip() == "":
            raise ValueError("relation_kind must be non-empty")
        if not self.evidence_ids:
            raise ValueError("relation evidence_ids must be non-empty")
        if len(frozenset(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("relation evidence_ids must be unique")
        if self.target is not None and self.target.strip() == "":
            raise ValueError("relation target must be non-empty when provided")
        if self.value is not None and self.value.strip() == "":
            raise ValueError("relation value must be non-empty when provided")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("relation confidence must be between 0 and 1")


@dataclass(frozen=True)
class SemanticResolverUnresolvedItem:
    code: str
    message: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.code.strip() == "" or self.message.strip() == "":
            raise ValueError("unresolved item code and message must be non-empty")
        if len(frozenset(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("unresolved evidence_ids must be unique")


@dataclass(frozen=True)
class SemanticResolverResponse:
    request_id: str
    status: SemanticResolverStatus
    relations: tuple[SemanticResolverRelation, ...] = ()
    unresolved_items: tuple[SemanticResolverUnresolvedItem, ...] = ()
    diagnostics: tuple[str, ...] = ()
    model_metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.request_id.strip() == "":
            raise ValueError("response request_id must be non-empty")
        if self.status is SemanticResolverStatus.RESOLVED and not self.relations:
            raise ValueError("RESOLVED semantic resolver response requires at least one relation")
        if self.status is not SemanticResolverStatus.RESOLVED and self.relations:
            raise ValueError("non-RESOLVED semantic resolver response must not carry relations")
        for diagnostic in self.diagnostics:
            if diagnostic.strip() == "":
                raise ValueError("diagnostics must not contain blank values")
        for key, value in self.model_metadata:
            if key.strip() == "" or value.strip() == "":
                raise ValueError("model metadata entries must be non-empty")


@dataclass(frozen=True)
class SemanticResolverRequest:
    request_id: str
    contract_version: str
    task_kind: SemanticResolverTaskKind
    evidence: tuple[SemanticResolverEvidence, ...]
    unresolved_question: str
    allowed_output_vocabulary: tuple[str, ...]
    deterministic_context: tuple[tuple[str, str], ...] = ()
    prompt_version: str = SEMANTIC_RESOLVER_PROMPT_VERSION
    trace_metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.request_id.strip() == "":
            raise ValueError("request_id must be non-empty")
        if self.contract_version != SEMANTIC_RESOLVER_CONTRACT_VERSION:
            raise ValueError("unsupported semantic resolver contract version")
        if not self.evidence:
            raise ValueError("semantic resolver request requires deterministic evidence")
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(frozenset(evidence_ids)) != len(evidence_ids):
            raise ValueError("semantic resolver evidence IDs must be unique")
        if self.unresolved_question.strip() == "":
            raise ValueError("unresolved_question must be non-empty")
        if not self.allowed_output_vocabulary:
            raise ValueError("allowed_output_vocabulary must be non-empty")
        if len(frozenset(self.allowed_output_vocabulary)) != len(self.allowed_output_vocabulary):
            raise ValueError("allowed_output_vocabulary must be unique")
        for value in self.allowed_output_vocabulary:
            if value.strip() == "":
                raise ValueError("allowed_output_vocabulary must not contain blanks")
        if self.prompt_version.strip() == "":
            raise ValueError("prompt_version must be non-empty")
        for key, value in (*self.deterministic_context, *self.trace_metadata):
            if key.strip() == "" or value.strip() == "":
                raise ValueError("metadata entries must be non-empty")


@dataclass(frozen=True)
class SemanticResolverFailure:
    kind: SemanticResolverFailureKind
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.code.strip() == "" or self.message.strip() == "":
            raise ValueError("failure code and message must be non-empty")


@dataclass(frozen=True)
class SemanticResolverResult:
    response: SemanticResolverResponse | None = None
    failure: SemanticResolverFailure | None = None

    @classmethod
    def success(cls, response: SemanticResolverResponse) -> SemanticResolverResult:
        return cls(response=response)

    @classmethod
    def failed(cls, failure: SemanticResolverFailure) -> SemanticResolverResult:
        return cls(failure=failure)

    def __post_init__(self) -> None:
        if (self.response is None) == (self.failure is None):
            raise ValueError("semantic resolver result requires exactly one response or failure")


class SemanticResolver(Protocol):
    def resolve(self, request: SemanticResolverRequest) -> SemanticResolverResult:
        """Resolve relationships among deterministic evidence without creating facts."""
        ...
