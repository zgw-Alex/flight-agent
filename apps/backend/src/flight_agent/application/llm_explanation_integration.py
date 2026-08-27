"""M8-U5 explanation lifecycle with evidence validation and fallback."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from flight_agent.application.llm_prompting import (
    EXPLANATION_GENERATION_PROMPT_FAMILY,
    build_explanation_prompt_context,
    load_runtime_prompt_template,
    render_prompt,
)
from flight_agent.application.llm_requirement_integration import (
    LLMBackedCapabilityMetadata,
    LLMCapabilityInvocationMetadata,
)
from flight_agent.domain.flights import OfferId
from flight_agent.domain.shared import DomainInstant
from flight_agent.domain.workflow import (
    EvidenceRef,
    EvidenceSource,
    ExplanationResult,
    ExplanationResultId,
    ExplanationStatement,
    ExplanationStatementKind,
    RecommendationResult,
    RecommendationResultStatus,
)
from flight_agent.ports import (
    CapabilityFailure,
    CapabilityFailureKind,
    CapabilityGenerationMetadata,
    CapabilityResult,
    CapabilityResultStatus,
    CapabilitySemanticIssue,
    CapabilitySemanticValidation,
    ExplanationDraft,
    ExplanationGenerationCapability,
    ExplanationGenerationRequest,
    LLMCapabilityName,
    PromptRenderRequest,
    RenderedPrompt,
    validate_explanation_draft,
)


class ExplanationGenerationSource(str, Enum):
    LLM = "LLM"
    DETERMINISTIC_FALLBACK = "DETERMINISTIC_FALLBACK"


@dataclass(frozen=True)
class ApprovedExplanationEvidence:
    ref: EvidenceRef
    projection: str
    statement_kind: ExplanationStatementKind = ExplanationStatementKind.MATCH

    def __post_init__(self) -> None:
        if self.projection.strip() == "":
            raise ValueError("ApprovedExplanationEvidence projection must be non-empty")


@dataclass(frozen=True)
class ExplanationEvidenceBundle:
    recommendation_result_id: str
    requirement_id: str | None
    requirement_version: int
    selected_offer_id: str | None
    selected_itinerary_id: str | None
    approved_evidence: tuple[ApprovedExplanationEvidence, ...]
    compared_offer_ids: tuple[str, ...] = ()
    unknown_markers: tuple[str, ...] = ()
    comparison_relations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.recommendation_result_id.strip() == "":
            raise ValueError("recommendation_result_id must be non-empty")
        if len(self.approved_evidence) == 0:
            raise ValueError("ExplanationEvidenceBundle requires approved evidence")

    @property
    def evidence_refs(self) -> tuple[EvidenceRef, ...]:
        return tuple(item.ref for item in self.approved_evidence)

    @property
    def projection_lines(self) -> tuple[str, ...]:
        return tuple(item.projection for item in self.approved_evidence)


@dataclass(frozen=True)
class ExplanationValidationResult:
    validation: CapabilitySemanticValidation
    fallback_required: bool


@dataclass(frozen=True)
class ExplanationLLMIntegrationResult:
    explanation_result: ExplanationResult
    draft: ExplanationDraft
    source: ExplanationGenerationSource
    validation: CapabilitySemanticValidation
    invocation_metadata: LLMCapabilityInvocationMetadata | None
    recommendation_result_id: str
    recommendation_unchanged: bool = True

    @property
    def fallback_used(self) -> bool:
        return self.source is ExplanationGenerationSource.DETERMINISTIC_FALLBACK


RenderedPromptConsumer = Callable[[RenderedPrompt], None]


def build_explanation_evidence_bundle(
    recommendation: RecommendationResult,
) -> ExplanationEvidenceBundle:
    approved: list[ApprovedExplanationEvidence] = [
        ApprovedExplanationEvidence(
            EvidenceRef(
                EvidenceSource.RECOMMENDATION,
                recommendation.recommendation_result_id,
                note="Recommendation identity and lineage are deterministic authority",
            ),
            (
                "recommendation_identity: "
                f"id={recommendation.recommendation_result_id.value}; "
                f"status={recommendation.status.value}; "
                f"requirement_version={recommendation.based_on_requirement_version.value}"
            ),
            ExplanationStatementKind.MATCH,
        )
    ]
    selected_offer_id: str | None = None
    selected_itinerary_id: str | None = None
    compared_ids: set[str] = set()
    unknown_markers: set[str] = set()
    comparison_relations: list[str] = []

    if recommendation.status is not RecommendationResultStatus.NO_MATCH:
        item = recommendation.items[0]
        selected_offer_id = item.primary_offer_id.value
        selected_itinerary_id = item.itinerary_id.value
        approved.append(
            ApprovedExplanationEvidence(
                EvidenceRef(
                    EvidenceSource.OFFER,
                    item.primary_offer_id,
                    note="Selected offer identity approved by RecommendationResult",
                ),
                (
                    "selected_candidate_fact: "
                    f"selected_offer_id={selected_offer_id}; "
                    f"selected_itinerary_id={selected_itinerary_id}; "
                    f"roles={','.join(role.value for role in item.roles)}"
                ),
                ExplanationStatementKind.MATCH,
            )
        )
        for evidence in item.evidence:
            approved.append(
                ApprovedExplanationEvidence(
                    evidence,
                    f"approved_item_evidence: {_evidence_ref_key(evidence)}; note={evidence.note or 'NONE'}",
                    ExplanationStatementKind.ADVANTAGE,
                )
            )
            _collect_unknown_markers(evidence.note, unknown_markers)
        for trade_off in item.trade_off_evidence:
            approved.append(
                ApprovedExplanationEvidence(
                    EvidenceRef(
                        EvidenceSource.RECOMMENDATION,
                        recommendation.recommendation_result_id,
                        note=trade_off,
                    ),
                    f"trade_off: {trade_off}",
                    ExplanationStatementKind.TRADE_OFF,
                )
            )
            _collect_unknown_markers(trade_off, unknown_markers)

    for comparison in recommendation.candidate_comparisons:
        compared_ids.add(comparison.left_offer_id.value)
        compared_ids.add(comparison.right_offer_id.value)
        relation = (
            "comparison: "
            f"left_offer_id={comparison.left_offer_id.value}; "
            f"right_offer_id={comparison.right_offer_id.value}; "
            f"price_difference={comparison.price_difference or 'UNKNOWN'}; "
            f"stop_count_difference={comparison.stop_count_difference if comparison.stop_count_difference is not None else 'UNKNOWN'}; "
            f"source_rank_relation={comparison.source_rank_relation or 'UNKNOWN'}"
        )
        comparison_relations.append(relation)
        approved.append(
            ApprovedExplanationEvidence(
                EvidenceRef(
                    EvidenceSource.RECOMMENDATION,
                    recommendation.recommendation_result_id,
                    note=relation,
                ),
                relation,
                ExplanationStatementKind.TRADE_OFF,
            )
        )
        _collect_unknown_markers(relation, unknown_markers)

    unselected_compared = tuple(
        sorted(offer_id for offer_id in compared_ids if offer_id != selected_offer_id)
    )
    return ExplanationEvidenceBundle(
        recommendation_result_id=recommendation.recommendation_result_id.value,
        requirement_id=recommendation.requirement_id.value
        if recommendation.requirement_id is not None
        else None,
        requirement_version=recommendation.based_on_requirement_version.value,
        selected_offer_id=selected_offer_id,
        selected_itinerary_id=selected_itinerary_id,
        approved_evidence=tuple(_dedupe_evidence(approved)),
        compared_offer_ids=unselected_compared,
        unknown_markers=tuple(sorted(unknown_markers)),
        comparison_relations=tuple(comparison_relations),
    )


def execute_llm_explanation(
    *,
    recommendation: RecommendationResult,
    capability: ExplanationGenerationCapability,
    explanation_result_id: ExplanationResultId,
    generated_at: DomainInstant,
) -> ExplanationLLMIntegrationResult:
    original_identity = _recommendation_identity(recommendation)
    bundle = build_explanation_evidence_bundle(recommendation)
    request = ExplanationGenerationRequest(
        recommendation_result_id=recommendation.recommendation_result_id,
        approved_evidence=bundle.evidence_refs,
    )
    rendered_prompt = render_prompt(
        PromptRenderRequest(
            load_runtime_prompt_template(EXPLANATION_GENERATION_PROMPT_FAMILY),
            build_explanation_prompt_context(request, bundle.projection_lines),
        )
    )
    _consume_rendered_prompt(capability, rendered_prompt)
    result = capability.generate_explanation(request)
    draft = result.output if result.status is CapabilityResultStatus.SUCCESS else None
    validation = _failure_validation(result.failure) if draft is None else validate_explanation_result(draft, bundle).validation

    source = ExplanationGenerationSource.LLM
    if draft is None or not validation.is_semantically_valid:
        source = ExplanationGenerationSource.DETERMINISTIC_FALLBACK
        draft = deterministic_explanation_fallback(bundle)
        validation = validate_explanation_result(draft, bundle).validation

    explanation = explanation_result_from_draft(
        explanation_result_id=explanation_result_id,
        recommendation=recommendation,
        draft=draft,
        bundle=bundle,
        generated_at=generated_at,
    )
    return ExplanationLLMIntegrationResult(
        explanation_result=explanation,
        draft=draft,
        source=source,
        validation=validation,
        invocation_metadata=_invocation_metadata(result.metadata),
        recommendation_result_id=recommendation.recommendation_result_id.value,
        recommendation_unchanged=original_identity == _recommendation_identity(recommendation),
    )


def explanation_result_from_draft(
    *,
    explanation_result_id: ExplanationResultId,
    recommendation: RecommendationResult,
    draft: ExplanationDraft,
    bundle: ExplanationEvidenceBundle,
    generated_at: DomainInstant,
) -> ExplanationResult:
    statements = tuple(
        ExplanationStatement(
            _statement_kind_for_ref(evidence, bundle),
            evidence=(evidence,),
            rendered_text=draft.draft_text,
        )
        for evidence in draft.used_evidence
    )
    return ExplanationResult(
        explanation_result_id=explanation_result_id,
        recommendation_result_id=recommendation.recommendation_result_id,
        execution_id=recommendation.execution_id,
        based_on_requirement_version=recommendation.based_on_requirement_version,
        snapshot_id=recommendation.snapshot_id,
        snapshot_version=recommendation.snapshot_version,
        generated_at=generated_at,
        statements=statements,
    )


def validate_explanation_result(
    draft: ExplanationDraft,
    bundle: ExplanationEvidenceBundle,
) -> ExplanationValidationResult:
    issues = list(validate_explanation_draft(draft, bundle.evidence_refs).issues)
    text = draft.draft_text
    lower_text = text.lower()

    if bundle.selected_offer_id is not None and bundle.selected_offer_id not in text:
        issues.append(_issue("RECOMMENDATION_IDENTITY_MISSING", "Draft omitted selected offer identity"))
    for offer_id in bundle.compared_offer_ids:
        if _contains_token(text, offer_id):
            issues.append(_issue("RECOMMENDATION_DRIFT", "Draft names a non-selected offer"))
    for marker in bundle.unknown_markers:
        if marker.lower() in lower_text and "unknown" not in lower_text and "UNKNOWN" not in text:
            issues.append(_issue("UNKNOWN_BECAME_KNOWN", "Draft converts UNKNOWN evidence into a known fact"))
    for relation in bundle.comparison_relations:
        if _comparison_reversed(relation, text):
            issues.append(_issue("COMPARISON_REVERSED", "Draft reverses an approved comparison"))
    if any(marker in lower_text for marker in ("aggregate_score", "ranking_score", "internal score")):
        issues.append(_issue("INTERNAL_SCORE_LEAK", "Draft exposes internal ranking score details"))
    for protected in _PROTECTED_FACT_MARKERS:
        if protected in lower_text and not _approved_marker(bundle, protected):
            issues.append(_issue("FABRICATED_FACT", "Draft includes a protected fact absent from approved evidence"))

    validation = (
        CapabilitySemanticValidation(is_semantically_valid=True)
        if not issues
        else CapabilitySemanticValidation(is_semantically_valid=False, issues=tuple(issues))
    )
    return ExplanationValidationResult(validation=validation, fallback_required=bool(issues))


def deterministic_explanation_fallback(bundle: ExplanationEvidenceBundle) -> ExplanationDraft:
    selected = bundle.selected_offer_id or "NO_MATCH"
    pieces = [
        f"Recommendation {bundle.recommendation_result_id} keeps selected offer {selected}.",
    ]
    for line in bundle.projection_lines:
        if line.startswith(("approved_item_evidence:", "trade_off:", "comparison:")):
            pieces.append(line)
            break
    if bundle.unknown_markers:
        pieces.append("UNKNOWN remains UNKNOWN for " + ", ".join(bundle.unknown_markers) + ".")
    text = " ".join(pieces)
    return ExplanationDraft(
        draft_text=text,
        used_evidence=bundle.evidence_refs[:1],
        metadata=CapabilityGenerationMetadata(
            capability=LLMCapabilityName.EXPLANATION_GENERATION,
            output_schema_version="m8-u1",
            adapter_version="deterministic-fallback-u5",
            model_identity=None,
        ),
    )


def explanation_draft_from_json(
    payload: dict[str, Any],
    metadata: CapabilityGenerationMetadata,
    approved_evidence: tuple[EvidenceRef, ...],
) -> ExplanationDraft:
    draft_text = _string(payload, "draft_text")
    used = _used_evidence(payload.get("used_evidence"), approved_evidence)
    return ExplanationDraft(draft_text=draft_text, used_evidence=used, metadata=metadata)


def validated_explanation_capability_result(
    draft: ExplanationDraft,
    metadata: LLMBackedCapabilityMetadata,
    approved_evidence: tuple[EvidenceRef, ...],
) -> CapabilityResult[ExplanationDraft]:
    validation = validate_explanation_draft(draft, approved_evidence)
    metadata = _metadata_with_validation(metadata, validation)
    if not validation.is_semantically_valid:
        return CapabilityResult.failure_result(
            metadata,
            CapabilityFailure(
                CapabilityFailureKind.SEMANTIC_INVALID,
                validation.issues[0].code,
                validation.issues[0].message,
            ),
        )
    return CapabilityResult.success(metadata, draft, validation)


def _consume_rendered_prompt(
    capability: ExplanationGenerationCapability,
    rendered_prompt: RenderedPrompt,
) -> None:
    consumer = getattr(capability, "consume_rendered_prompt", None)
    if callable(consumer):
        consumer(rendered_prompt)


def _failure_validation(failure: CapabilityFailure | None) -> CapabilitySemanticValidation:
    issue = _issue(
        failure.code if failure is not None else "LLM_EXPLANATION_FAILED",
        failure.message if failure is not None else "LLM explanation generation failed",
    )
    return CapabilitySemanticValidation(is_semantically_valid=False, issues=(issue,))


def _invocation_metadata(
    metadata: CapabilityGenerationMetadata,
) -> LLMCapabilityInvocationMetadata | None:
    if isinstance(metadata, LLMBackedCapabilityMetadata):
        return metadata.invocation
    return None


def _metadata_with_validation(
    metadata: LLMBackedCapabilityMetadata,
    validation: CapabilitySemanticValidation,
) -> LLMBackedCapabilityMetadata:
    if metadata.invocation is None:
        return metadata
    return LLMBackedCapabilityMetadata(
        capability=metadata.capability,
        output_schema_version=metadata.output_schema_version,
        adapter_version=metadata.adapter_version,
        model_identity=metadata.model_identity,
        invocation=LLMCapabilityInvocationMetadata(
            invocation_id=metadata.invocation.invocation_id,
            capability=metadata.invocation.capability,
            model_id=metadata.invocation.model_id,
            prompt_template_version=metadata.invocation.prompt_template_version,
            output_schema_version=metadata.invocation.output_schema_version,
            adapter_version=metadata.invocation.adapter_version,
            attempt_count=metadata.invocation.attempt_count,
            latency_ms=metadata.invocation.latency_ms,
            token_count_observed=metadata.invocation.token_count_observed,
            requirement_id=metadata.invocation.requirement_id,
            requirement_version=metadata.invocation.requirement_version,
            validation_outcome="SEMANTIC_VALID"
            if validation.is_semantically_valid
            else "SEMANTIC_INVALID",
            stale_outcome=metadata.invocation.stale_outcome,
        ),
    )


def _statement_kind_for_ref(
    evidence: EvidenceRef,
    bundle: ExplanationEvidenceBundle,
) -> ExplanationStatementKind:
    for approved in bundle.approved_evidence:
        if approved.ref == evidence:
            return approved.statement_kind
    return ExplanationStatementKind.MATCH


def _used_evidence(value: object, approved_evidence: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
    if not isinstance(value, list):
        raise TypeError("used_evidence must be a list")
    refs_by_key: dict[str, EvidenceRef] = {}
    for ref in approved_evidence:
        refs_by_key.setdefault(_evidence_ref_key(ref), ref)
    refs: list[EvidenceRef] = []
    for item in value:
        key = item if isinstance(item, str) else _ref_key_from_mapping(item)
        if key in refs_by_key:
            refs.append(refs_by_key[key])
            continue
        refs.append(_unapproved_ref(key))
    if not refs:
        raise ValueError("used_evidence must not be empty")
    return tuple(refs)


def _ref_key_from_mapping(value: object) -> str:
    if not isinstance(value, dict):
        raise TypeError("used_evidence entries must be strings or objects")
    source = _string(value, "source")
    identity = _string(value, "identity")
    return f"{source}:{identity}"


def _unapproved_ref(key: str) -> EvidenceRef:
    identity = key.split(":", 1)[1] if ":" in key else key
    return EvidenceRef(EvidenceSource.OFFER, OfferId(identity or "unapproved-offer"))


def _evidence_ref_key(ref: EvidenceRef) -> str:
    return f"{ref.source.value}:{ref.identity.value}"


def _dedupe_evidence(
    evidence: list[ApprovedExplanationEvidence],
) -> tuple[ApprovedExplanationEvidence, ...]:
    seen: set[tuple[EvidenceRef, str]] = set()
    deduped: list[ApprovedExplanationEvidence] = []
    for item in evidence:
        key = (item.ref, item.projection)
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return tuple(deduped)


def _recommendation_identity(recommendation: RecommendationResult) -> tuple[object, ...]:
    return (
        recommendation.recommendation_result_id,
        recommendation.status,
        recommendation.items,
        recommendation.candidate_comparisons,
    )


def _collect_unknown_markers(value: str | None, markers: set[str]) -> None:
    if value is None or "UNKNOWN" not in value.upper():
        return
    for marker in _PROTECTED_FACT_MARKERS:
        if marker in value.lower():
            markers.add(marker)


def _comparison_reversed(relation: str, text: str) -> bool:
    if "price_difference=" not in relation:
        return False
    match = re.search(r"left_offer_id=([^;]+); right_offer_id=([^;]+); price_difference=([^;]+)", relation)
    if match is None:
        return False
    left, right, difference = match.groups()
    if not difference.startswith("-"):
        return False
    lower = text.lower()
    return (
        _contains_token(text, left)
        and _contains_token(text, right)
        and right.lower() in lower
        and any(marker in lower for marker in ("cheaper than " + left.lower(), "less expensive than " + left.lower()))
    )


def _approved_marker(bundle: ExplanationEvidenceBundle, marker: str) -> bool:
    return any(marker in line.lower() for line in bundle.projection_lines)


def _contains_token(text: str, token: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_-]){re.escape(token)}(?![A-Za-z0-9_-])", text) is not None


def _issue(code: str, message: str) -> CapabilitySemanticIssue:
    return CapabilitySemanticIssue(code=code, message=message)


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{key} must be a non-empty string")
    return value


_PROTECTED_FACT_MARKERS = frozenset(
    {
        "baggage",
        "luggage",
        "wifi",
        "meal",
        "refund",
        "lounge",
        "seat selection",
        "20kg",
        "30kg",
    }
)
