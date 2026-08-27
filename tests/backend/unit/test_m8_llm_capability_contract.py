from __future__ import annotations

import pytest

from flight_agent.adapters.llm_fake import (
    FakeExplanationFixture,
    FakeExplanationLLM,
    FakeInitialRequirementFixture,
    FakeInitialRequirementLLM,
    FakePatchUnderstandingFixture,
    FakePatchUnderstandingLLM,
)
from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    RequirementState,
    SoftPreference,
)
from flight_agent.domain.shared import RequirementVersion
from flight_agent.domain.workflow import EvidenceRef, EvidenceSource, RecommendationResultId
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
    InitialRequirementInterpretationCapability,
    InitialRequirementInterpretationRequest,
    InitialRequirementProposal,
    LLMCapabilityName,
    PatchProposalAction,
    PatchProposalOperation,
    PatchRequirementProposal,
    PatchUnderstandingCapability,
    PatchUnderstandingRequest,
    ProposalEvidence,
    SourceSpanHint,
    validate_explanation_draft,
    validate_initial_requirement_proposal,
    validate_patch_proposal,
)


def test_three_narrow_capability_ports_are_independently_replaceable() -> None:
    initial: InitialRequirementInterpretationCapability = FakeInitialRequirementLLM(
        (
            FakeInitialRequirementFixture(
                "上海到洛杉矶，越便宜越好",
                CapabilityResult.success(
                    metadata(LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION),
                    clear_initial_proposal(),
                ),
            ),
        )
    )
    patch: PatchUnderstandingCapability = FakePatchUnderstandingLLM(
        (
            FakePatchUnderstandingFixture(
                user_message="改成从虹桥出发",
                requirement_projection="origin=PVG",
                result=CapabilityResult.success(
                    metadata(LLMCapabilityName.PATCH_UNDERSTANDING),
                    patch_proposal(),
                ),
            ),
        )
    )
    explanation: ExplanationGenerationCapability = FakeExplanationLLM(
        (
            FakeExplanationFixture(
                "recommendation-1",
                CapabilityResult.success(
                    metadata(LLMCapabilityName.EXPLANATION_GENERATION),
                    explanation_draft(),
                ),
            ),
        )
    )

    initial_result = initial.interpret_initial_requirement(
        InitialRequirementInterpretationRequest("上海到洛杉矶，越便宜越好")
    )
    patch_result = patch.understand_patch(
        PatchUnderstandingRequest(
            user_message="改成从虹桥出发",
            requirement_id=RequirementId("requirement-1"),
            based_on_requirement_version=RequirementVersion(1),
            current_requirement_projection="origin=PVG",
        )
    )
    explanation_result = explanation.generate_explanation(
        ExplanationGenerationRequest(
            RecommendationResultId("recommendation-1"),
            approved_evidence=(approved_evidence(),),
        )
    )

    assert initial_result.status is CapabilityResultStatus.SUCCESS
    assert patch_result.status is CapabilityResultStatus.SUCCESS
    assert explanation_result.status is CapabilityResultStatus.SUCCESS
    assert isinstance(initial_result.output, InitialRequirementProposal)
    assert isinstance(patch_result.output, PatchRequirementProposal)
    assert isinstance(explanation_result.output, ExplanationDraft)


def test_initial_requirement_proposal_is_structured_but_not_requirement_authority() -> None:
    proposal = clear_initial_proposal()

    assert proposal.constraints == (origin_constraint(),)
    assert proposal.preferences == (cheap_preference(),)
    assert proposal.evidence == (
        ProposalEvidence(
            source_input="上海到洛杉矶，越便宜越好",
            span=SourceSpanHint(start=0, end=2, text="上海"),
        ),
    )
    assert validate_initial_requirement_proposal(proposal).is_semantically_valid
    assert not isinstance(proposal, RequirementState)
    assert not hasattr(proposal, "apply")
    assert not hasattr(proposal, "commit")


def test_schema_valid_initial_output_can_still_be_semantic_invalid() -> None:
    proposal = InitialRequirementProposal(source_input="随便安排")

    validation = validate_initial_requirement_proposal(proposal)

    assert not validation.is_semantically_valid
    assert validation.issues == (
        CapabilitySemanticIssue(
            code="EMPTY_PROPOSAL",
            message="Initial requirement proposal contains no semantic content",
        ),
    )


def test_ambiguous_and_insufficient_context_are_first_class_results() -> None:
    ambiguous_proposal = InitialRequirementProposal(
        source_input="去华盛顿",
        ambiguity_reasons=("CITY_OR_AIRPORT_AMBIGUOUS",),
    )
    insufficient_patch = PatchRequirementProposal(
        source_input="改成那个便宜的",
        insufficient_context=("MISSING_TARGET_REFERENCE",),
    )

    ambiguous = CapabilityResult.ambiguous(
        metadata(LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION),
        ambiguous_proposal,
        validate_initial_requirement_proposal(ambiguous_proposal),
    )
    insufficient = CapabilityResult.insufficient_context(
        metadata(LLMCapabilityName.PATCH_UNDERSTANDING),
        insufficient_patch,
        validate_patch_proposal(insufficient_patch),
    )

    assert ambiguous.status is CapabilityResultStatus.AMBIGUOUS
    assert insufficient.status is CapabilityResultStatus.INSUFFICIENT_CONTEXT
    assert ambiguous.failure is None
    assert insufficient.failure is None


def test_patch_proposal_binds_base_requirement_lineage_but_has_no_commit_authority() -> None:
    proposal = patch_proposal()

    assert proposal.based_on_requirement_id == RequirementId("requirement-1")
    assert proposal.based_on_requirement_version == RequirementVersion(1)
    assert validate_patch_proposal(proposal).is_semantically_valid
    assert not hasattr(proposal, "apply")
    assert not hasattr(proposal, "commit")


def test_patch_schema_valid_without_base_lineage_is_semantic_invalid() -> None:
    proposal = PatchRequirementProposal(
        operations=(
            PatchProposalOperation(
                PatchProposalAction.REPLACE_CONSTRAINT,
                target_id=ConstraintId("constraint-origin"),
                item=origin_constraint("SHA"),
            ),
        ),
        source_input="改成虹桥",
    )

    validation = validate_patch_proposal(proposal)

    assert not validation.is_semantically_valid
    assert validation.issues == (
        CapabilitySemanticIssue(
            code="MISSING_BASE_LINEAGE",
            message="Patch proposal must bind to a base Requirement id and version",
        ),
    )


def test_explanation_draft_uses_only_approved_evidence_and_cannot_mutate_recommendation() -> None:
    draft = explanation_draft()

    validation = validate_explanation_draft(draft, approved_evidence=(approved_evidence(),))

    assert validation.is_semantically_valid
    assert not hasattr(draft, "status")
    assert not hasattr(draft, "items")
    assert not hasattr(draft, "recommendation_result_id")


def test_explanation_draft_with_unapproved_evidence_is_semantic_invalid() -> None:
    draft = ExplanationDraft(
        draft_text="这条推荐便宜。",
        used_evidence=(
            EvidenceRef(EvidenceSource.REQUIREMENT, RequirementId("requirement-1")),
        ),
        metadata=metadata(LLMCapabilityName.EXPLANATION_GENERATION),
    )

    validation = validate_explanation_draft(draft, approved_evidence=(approved_evidence(),))

    assert not validation.is_semantically_valid
    assert validation.issues == (
        CapabilitySemanticIssue(
            code="UNAPPROVED_EVIDENCE",
            message="ExplanationDraft references evidence outside the approved bundle",
        ),
    )


def test_failure_result_distinguishes_provider_transport_schema_and_semantic_failures() -> None:
    for kind in CapabilityFailureKind:
        result: CapabilityResult[InitialRequirementProposal] = CapabilityResult.failure_result(
            metadata(LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION),
            CapabilityFailure(kind=kind, code=kind.value, message="simulated failure"),
        )

        assert result.status is CapabilityResultStatus.FAILURE
        assert result.failure is not None
        assert result.failure.kind is kind
        assert result.output is None


def test_capability_result_rejects_success_without_semantic_validity() -> None:
    with pytest.raises(ValueError):
        CapabilityResult(
            status=CapabilityResultStatus.SUCCESS,
            metadata=metadata(LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION),
            output=InitialRequirementProposal(source_input="empty"),
            semantic_validation=CapabilitySemanticValidation(
                is_semantically_valid=False,
                issues=(
                    CapabilitySemanticIssue(
                        code="EMPTY_PROPOSAL",
                        message="empty proposal is schema-valid but not semantic-valid",
                    ),
                ),
            ),
        )


def metadata(capability: LLMCapabilityName) -> CapabilityGenerationMetadata:
    return CapabilityGenerationMetadata(
        capability=capability,
        output_schema_version="m8-u1",
        adapter_version="fake-llm-u1",
    )


def clear_initial_proposal() -> InitialRequirementProposal:
    return InitialRequirementProposal(
        constraints=(origin_constraint(),),
        preferences=(cheap_preference(),),
        source_input="上海到洛杉矶，越便宜越好",
        evidence=(
            ProposalEvidence(
                source_input="上海到洛杉矶，越便宜越好",
                span=SourceSpanHint(start=0, end=2, text="上海"),
            ),
        ),
    )


def patch_proposal() -> PatchRequirementProposal:
    return PatchRequirementProposal(
        operations=(
            PatchProposalOperation(
                PatchProposalAction.REPLACE_CONSTRAINT,
                target_id=ConstraintId("constraint-origin"),
                item=origin_constraint("SHA"),
            ),
        ),
        source_input="改成从虹桥出发",
        based_on_requirement_id=RequirementId("requirement-1"),
        based_on_requirement_version=RequirementVersion(1),
        evidence=(
            ProposalEvidence(
                source_input="改成从虹桥出发",
                span=SourceSpanHint(start=2, end=4, text="虹桥"),
            ),
        ),
    )


def explanation_draft() -> ExplanationDraft:
    return ExplanationDraft(
        draft_text="这条推荐来自已批准的推荐证据。",
        used_evidence=(approved_evidence(),),
        metadata=metadata(LLMCapabilityName.EXPLANATION_GENERATION),
    )


def approved_evidence() -> EvidenceRef:
    return EvidenceRef(EvidenceSource.RECOMMENDATION, RecommendationResultId("recommendation-1"))


def origin_constraint(airport: str = "PVG") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-origin"),
        scope=ConstraintScope.ORIGIN_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def cheap_preference() -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId("preference-price"),
        scope=PreferenceScope.PRICE,
        importance=PreferenceImportance.HIGH,
    )
