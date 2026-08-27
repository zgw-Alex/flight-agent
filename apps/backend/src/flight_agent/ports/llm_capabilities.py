"""Provider-neutral LLM capability contracts for M8-U1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeVar

from flight_agent.domain.requirements import RequirementId
from flight_agent.domain.shared import RequirementVersion
from flight_agent.domain.workflow import EvidenceRef, RecommendationResultId
from flight_agent.ports.requirement_interpreter import (
    InitialRequirementProposal,
    PatchRequirementProposal,
)


class LLMCapabilityName(str, Enum):
    INITIAL_REQUIREMENT_INTERPRETATION = "INITIAL_REQUIREMENT_INTERPRETATION"
    PATCH_UNDERSTANDING = "PATCH_UNDERSTANDING"
    EXPLANATION_GENERATION = "EXPLANATION_GENERATION"


class CapabilityResultStatus(str, Enum):
    SUCCESS = "SUCCESS"
    AMBIGUOUS = "AMBIGUOUS"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    FAILURE = "FAILURE"


class CapabilityFailureKind(str, Enum):
    PROVIDER_TRANSPORT_FAILURE = "PROVIDER_TRANSPORT_FAILURE"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    SEMANTIC_INVALID = "SEMANTIC_INVALID"
    STALE_CONTEXT = "STALE_CONTEXT"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class CapabilityGenerationMetadata:
    capability: LLMCapabilityName
    output_schema_version: str
    adapter_version: str
    model_identity: str | None = None

    def __post_init__(self) -> None:
        if self.output_schema_version.strip() == "":
            raise ValueError("output_schema_version must be non-empty")
        if self.adapter_version.strip() == "":
            raise ValueError("adapter_version must be non-empty")
        if self.model_identity is not None and self.model_identity.strip() == "":
            raise ValueError("model_identity must be non-empty when provided")


@dataclass(frozen=True)
class CapabilityFailure:
    kind: CapabilityFailureKind
    code: str
    message: str

    def __post_init__(self) -> None:
        if self.code.strip() == "" or self.message.strip() == "":
            raise ValueError("CapabilityFailure code and message must be non-empty")


@dataclass(frozen=True)
class CapabilitySemanticIssue:
    code: str
    message: str

    def __post_init__(self) -> None:
        if self.code.strip() == "" or self.message.strip() == "":
            raise ValueError("CapabilitySemanticIssue code and message must be non-empty")


@dataclass(frozen=True)
class CapabilitySemanticValidation:
    is_semantically_valid: bool
    issues: tuple[CapabilitySemanticIssue, ...] = ()

    def __post_init__(self) -> None:
        if self.is_semantically_valid and self.issues:
            raise ValueError("Semantic-valid result must not carry issues")
        if not self.is_semantically_valid and not self.issues:
            raise ValueError("Semantic-invalid result requires at least one issue")


TOutput = TypeVar("TOutput")


@dataclass(frozen=True)
class CapabilityResult[TOutput]:
    status: CapabilityResultStatus
    metadata: CapabilityGenerationMetadata
    output: TOutput | None = None
    semantic_validation: CapabilitySemanticValidation | None = None
    failure: CapabilityFailure | None = None

    @classmethod
    def success(
        cls,
        metadata: CapabilityGenerationMetadata,
        output: TOutput,
        semantic_validation: CapabilitySemanticValidation | None = None,
    ) -> CapabilityResult[TOutput]:
        validation = semantic_validation or CapabilitySemanticValidation(is_semantically_valid=True)
        return cls(
            status=CapabilityResultStatus.SUCCESS,
            metadata=metadata,
            output=output,
            semantic_validation=validation,
        )

    @classmethod
    def ambiguous(
        cls,
        metadata: CapabilityGenerationMetadata,
        output: TOutput,
        semantic_validation: CapabilitySemanticValidation,
    ) -> CapabilityResult[TOutput]:
        return cls(
            status=CapabilityResultStatus.AMBIGUOUS,
            metadata=metadata,
            output=output,
            semantic_validation=semantic_validation,
        )

    @classmethod
    def insufficient_context(
        cls,
        metadata: CapabilityGenerationMetadata,
        output: TOutput,
        semantic_validation: CapabilitySemanticValidation,
    ) -> CapabilityResult[TOutput]:
        return cls(
            status=CapabilityResultStatus.INSUFFICIENT_CONTEXT,
            metadata=metadata,
            output=output,
            semantic_validation=semantic_validation,
        )

    @classmethod
    def failure_result(
        cls, metadata: CapabilityGenerationMetadata, failure: CapabilityFailure
    ) -> CapabilityResult[TOutput]:
        return cls(
            status=CapabilityResultStatus.FAILURE,
            metadata=metadata,
            failure=failure,
        )

    def __post_init__(self) -> None:
        if self.status is CapabilityResultStatus.FAILURE:
            if self.failure is None or self.output is not None or self.semantic_validation is not None:
                raise ValueError("Failure result must carry only a failure envelope")
            return

        if self.output is None or self.semantic_validation is None or self.failure is not None:
            raise ValueError("Non-failure result requires output and semantic validation only")
        if self.status is CapabilityResultStatus.SUCCESS and not self.semantic_validation.is_semantically_valid:
            raise ValueError("SUCCESS result requires semantic-valid output")
        if self.status in {
            CapabilityResultStatus.AMBIGUOUS,
            CapabilityResultStatus.INSUFFICIENT_CONTEXT,
        } and self.semantic_validation.is_semantically_valid:
            raise ValueError("Ambiguous or insufficient-context result must not be semantic-valid")


@dataclass(frozen=True)
class InitialRequirementInterpretationRequest:
    user_message: str
    locale: str = "zh-CN"
    reference_time: str | None = None

    def __post_init__(self) -> None:
        if self.user_message.strip() == "":
            raise ValueError("user_message must be non-empty")
        if self.locale.strip() == "":
            raise ValueError("locale must be non-empty")


@dataclass(frozen=True)
class PatchUnderstandingRequest:
    user_message: str
    requirement_id: RequirementId
    based_on_requirement_version: RequirementVersion
    current_requirement_projection: str
    constraint_ids: tuple[str, ...] = ()
    preference_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.user_message.strip() == "":
            raise ValueError("user_message must be non-empty")
        if self.current_requirement_projection.strip() == "":
            raise ValueError("current_requirement_projection must be non-empty")


@dataclass(frozen=True)
class ExplanationGenerationRequest:
    recommendation_result_id: RecommendationResultId
    approved_evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if len(self.approved_evidence) == 0:
            raise ValueError("Explanation generation requires approved evidence")


@dataclass(frozen=True)
class ExplanationDraft:
    draft_text: str
    used_evidence: tuple[EvidenceRef, ...]
    metadata: CapabilityGenerationMetadata

    def __post_init__(self) -> None:
        if self.draft_text.strip() == "":
            raise ValueError("ExplanationDraft draft_text must be non-empty")
        if len(self.used_evidence) == 0:
            raise ValueError("ExplanationDraft requires at least one used evidence ref")
        if self.metadata.capability is not LLMCapabilityName.EXPLANATION_GENERATION:
            raise ValueError("ExplanationDraft metadata must belong to explanation generation")


class InitialRequirementInterpretationCapability(Protocol):
    def interpret_initial_requirement(
        self, request: InitialRequirementInterpretationRequest
    ) -> CapabilityResult[InitialRequirementProposal]:
        """Return a non-authoritative semantic proposal for M3 validation."""
        ...


class PatchUnderstandingCapability(Protocol):
    def understand_patch(
        self, request: PatchUnderstandingRequest
    ) -> CapabilityResult[PatchRequirementProposal]:
        """Return a non-authoritative patch proposal with base Requirement lineage."""
        ...


class ExplanationGenerationCapability(Protocol):
    def generate_explanation(
        self, request: ExplanationGenerationRequest
    ) -> CapabilityResult[ExplanationDraft]:
        """Return language rendering from approved deterministic evidence."""
        ...


def validate_initial_requirement_proposal(
    proposal: InitialRequirementProposal,
) -> CapabilitySemanticValidation:
    if _has_unresolved_outcome(proposal.unresolved_semantics, proposal.ambiguity_reasons):
        return CapabilitySemanticValidation(
            is_semantically_valid=False,
            issues=(
                CapabilitySemanticIssue(
                    code="AMBIGUOUS_REQUIREMENT",
                    message="Initial requirement proposal contains unresolved semantics",
                ),
            ),
        )
    if proposal.insufficient_context:
        return CapabilitySemanticValidation(
            is_semantically_valid=False,
            issues=(
                CapabilitySemanticIssue(
                    code="INSUFFICIENT_CONTEXT",
                    message="Initial requirement proposal lacks required context",
                ),
            ),
        )
    if not proposal.constraints and not proposal.preferences:
        return CapabilitySemanticValidation(
            is_semantically_valid=False,
            issues=(
                CapabilitySemanticIssue(
                    code="EMPTY_PROPOSAL",
                    message="Initial requirement proposal contains no semantic content",
                ),
            ),
        )
    return CapabilitySemanticValidation(is_semantically_valid=True)


def validate_patch_proposal(proposal: PatchRequirementProposal) -> CapabilitySemanticValidation:
    if _has_unresolved_outcome(proposal.unresolved_semantics, proposal.ambiguity_reasons):
        return CapabilitySemanticValidation(
            is_semantically_valid=False,
            issues=(
                CapabilitySemanticIssue(
                    code="AMBIGUOUS_PATCH",
                    message="Patch proposal contains unresolved references",
                ),
            ),
        )
    if proposal.insufficient_context:
        return CapabilitySemanticValidation(
            is_semantically_valid=False,
            issues=(
                CapabilitySemanticIssue(
                    code="INSUFFICIENT_CONTEXT",
                    message="Patch proposal lacks required context",
                ),
            ),
        )
    if proposal.based_on_requirement_id is None or proposal.based_on_requirement_version is None:
        return CapabilitySemanticValidation(
            is_semantically_valid=False,
            issues=(
                CapabilitySemanticIssue(
                    code="MISSING_BASE_LINEAGE",
                    message="Patch proposal must bind to a base Requirement id and version",
                ),
            ),
        )
    if not proposal.operations:
        return CapabilitySemanticValidation(
            is_semantically_valid=False,
            issues=(
                CapabilitySemanticIssue(
                    code="EMPTY_PATCH",
                    message="Patch proposal contains no proposed semantic operations",
                ),
            ),
        )
    return CapabilitySemanticValidation(is_semantically_valid=True)


def validate_explanation_draft(
    draft: ExplanationDraft, approved_evidence: tuple[EvidenceRef, ...]
) -> CapabilitySemanticValidation:
    approved = frozenset(approved_evidence)
    used = frozenset(draft.used_evidence)
    if not used.issubset(approved):
        return CapabilitySemanticValidation(
            is_semantically_valid=False,
            issues=(
                CapabilitySemanticIssue(
                    code="UNAPPROVED_EVIDENCE",
                    message="ExplanationDraft references evidence outside the approved bundle",
                ),
            ),
        )
    return CapabilitySemanticValidation(is_semantically_valid=True)


def _has_unresolved_outcome(
    unresolved_semantics: tuple[str, ...], ambiguity_reasons: tuple[str, ...]
) -> bool:
    return bool(unresolved_semantics or ambiguity_reasons)
