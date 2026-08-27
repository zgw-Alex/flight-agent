"""Runtime prompt registry, rendering, and deterministic context builders."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from flight_agent.domain.workflow import EvidenceRef
from flight_agent.ports import (
    ExplanationDraft,
    ExplanationGenerationRequest,
    InitialRequirementInterpretationRequest,
    InitialRequirementProposal,
    LLMCapabilityName,
    PatchProposalAction,
    PatchRequirementProposal,
    PatchUnderstandingRequest,
)
from flight_agent.ports.llm_prompting import (
    OutputSchemaVersion,
    PromptContextField,
    PromptContextProjection,
    PromptFamilyId,
    PromptRenderRequest,
    PromptSection,
    PromptSectionRole,
    PromptTemplateVersion,
    RenderedPrompt,
    RuntimePromptFamily,
    RuntimePromptTemplate,
)

PROMPT_ASSET_ROOT = Path(__file__).resolve().parents[3] / "resources" / "prompts" / "m8"
M8_U2_OUTPUT_SCHEMA_VERSION = OutputSchemaVersion("m8-u1")

INITIAL_REQUIREMENT_PROMPT_FAMILY = RuntimePromptFamily(
    family_id=PromptFamilyId("m8.initial_requirement_interpretation"),
    capability=LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION,
    prompt_template_version=PromptTemplateVersion("initial-requirement-v1"),
    output_schema_version=M8_U2_OUTPUT_SCHEMA_VERSION,
    asset_path="initial_requirement_interpretation_v1.md",
)
PATCH_UNDERSTANDING_PROMPT_FAMILY = RuntimePromptFamily(
    family_id=PromptFamilyId("m8.patch_understanding"),
    capability=LLMCapabilityName.PATCH_UNDERSTANDING,
    prompt_template_version=PromptTemplateVersion("patch-understanding-v1"),
    output_schema_version=M8_U2_OUTPUT_SCHEMA_VERSION,
    asset_path="patch_understanding_v1.md",
)
EXPLANATION_GENERATION_PROMPT_FAMILY = RuntimePromptFamily(
    family_id=PromptFamilyId("m8.explanation_generation"),
    capability=LLMCapabilityName.EXPLANATION_GENERATION,
    prompt_template_version=PromptTemplateVersion("explanation-generation-v1"),
    output_schema_version=M8_U2_OUTPUT_SCHEMA_VERSION,
    asset_path="explanation_generation_v1.md",
)

RUNTIME_PROMPT_FAMILIES = (
    INITIAL_REQUIREMENT_PROMPT_FAMILY,
    PATCH_UNDERSTANDING_PROMPT_FAMILY,
    EXPLANATION_GENERATION_PROMPT_FAMILY,
)


def load_runtime_prompt_template(family: RuntimePromptFamily) -> RuntimePromptTemplate:
    prompt_path = PROMPT_ASSET_ROOT / family.asset_path
    source = prompt_path.read_text(encoding="utf-8")
    sections = _parse_prompt_asset(source)
    return RuntimePromptTemplate(
        family=family,
        capability_instruction=sections["CAPABILITY_INSTRUCTION"],
        contract_constraints=sections["CONTRACT_CONSTRAINTS"],
    )


def render_prompt(request: PromptRenderRequest) -> RenderedPrompt:
    schema_guidance = output_schema_guidance(request.template.family.capability)
    return RenderedPrompt(
        family=request.template.family,
        sections=(
            PromptSection(
                PromptSectionRole.CAPABILITY_INSTRUCTION,
                request.template.capability_instruction,
            ),
            PromptSection(
                PromptSectionRole.CONTRACT_CONSTRAINTS,
                request.template.contract_constraints,
            ),
            PromptSection(PromptSectionRole.OUTPUT_SCHEMA_GUIDANCE, schema_guidance),
            PromptSection(
                PromptSectionRole.STRUCTURED_TRUSTED_CONTEXT,
                _render_fields(request.context.trusted_context),
            ),
            PromptSection(
                PromptSectionRole.UNTRUSTED_PAYLOAD,
                _render_fields(request.context.untrusted_payload),
            ),
        ),
    )


def build_initial_requirement_prompt_context(
    request: InitialRequirementInterpretationRequest,
    allowed_requirement_projection: str = "M3 requirement proposal semantics only",
) -> PromptContextProjection:
    trusted = [
        PromptContextField("locale", request.locale),
        PromptContextField("allowed_requirement_projection", allowed_requirement_projection),
    ]
    if request.reference_time is not None:
        trusted.append(PromptContextField("reference_time", request.reference_time))
    return PromptContextProjection(
        capability=LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION,
        trusted_context=tuple(trusted),
        untrusted_payload=(PromptContextField("user_message", request.user_message),),
    )


def build_patch_prompt_context(
    request: PatchUnderstandingRequest,
    recent_semantic_context: tuple[str, ...] = (),
) -> PromptContextProjection:
    trusted = (
        PromptContextField("requirement_id", request.requirement_id.value),
        PromptContextField(
            "based_on_requirement_version", str(request.based_on_requirement_version.value)
        ),
        PromptContextField(
            "current_requirement_projection", request.current_requirement_projection
        ),
        PromptContextField("constraint_ids", _join_or_none(request.constraint_ids)),
        PromptContextField("preference_ids", _join_or_none(request.preference_ids)),
        PromptContextField("recent_semantic_context", _join_or_none(recent_semantic_context)),
    )
    return PromptContextProjection(
        capability=LLMCapabilityName.PATCH_UNDERSTANDING,
        trusted_context=trusted,
        untrusted_payload=(PromptContextField("user_message", request.user_message),),
    )


def build_explanation_prompt_context(
    request: ExplanationGenerationRequest,
    approved_evidence_projection: tuple[str, ...],
) -> PromptContextProjection:
    if len(approved_evidence_projection) == 0:
        raise ValueError("approved_evidence_projection must be non-empty")
    trusted = (
        PromptContextField("recommendation_result_id", request.recommendation_result_id.value),
        PromptContextField("approved_evidence_refs", _render_evidence_refs(request.approved_evidence)),
        PromptContextField("approved_evidence_projection", "\n".join(approved_evidence_projection)),
    )
    return PromptContextProjection(
        capability=LLMCapabilityName.EXPLANATION_GENERATION,
        trusted_context=trusted,
        untrusted_payload=(),
    )


def output_schema_guidance(capability: LLMCapabilityName) -> str:
    if capability is LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION:
        return "\n".join(
            (
                _schema_guidance_for(
                    "InitialRequirementProposal",
                    InitialRequirementProposal,
                    ("SUCCESS", "AMBIGUOUS", "INSUFFICIENT_CONTEXT", "FAILURE"),
                ),
                _proposal_json_guidance(),
            )
        )
    if capability is LLMCapabilityName.PATCH_UNDERSTANDING:
        actions = ", ".join(action.value for action in PatchProposalAction)
        guidance = _schema_guidance_for(
            "PatchRequirementProposal",
            PatchRequirementProposal,
            ("SUCCESS", "AMBIGUOUS", "INSUFFICIENT_CONTEXT", "FAILURE"),
        )
        return f"{guidance}\nPatchProposalAction enum values: {actions}\n{_patch_json_guidance()}"
    return _schema_guidance_for(
        "ExplanationDraft",
        ExplanationDraft,
        ("SUCCESS", "AMBIGUOUS", "INSUFFICIENT_CONTEXT", "FAILURE"),
    ) + (
        "\nReturn JSON with keys draft_text, used_evidence, metadata. "
        "draft_text must mention only approved evidence projection facts and preserve UNKNOWN. "
        "draft_text must include the exact selected_offer_id token from approved evidence when one exists. "
        "used_evidence must be a non-empty array of approved evidence refs using "
        "{source, identity} objects, for example {\"source\": \"RECOMMENDATION\", "
        "\"identity\": \"recommendation-1\"}. "
        "metadata may be an object but runtime metadata is authoritative."
    )


def runtime_prompt_family_by_capability(capability: LLMCapabilityName) -> RuntimePromptFamily:
    for family in RUNTIME_PROMPT_FAMILIES:
        if family.capability is capability:
            return family
    raise ValueError(f"No runtime prompt family registered for {capability.value}")


def _parse_prompt_asset(source: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {
        "CAPABILITY_INSTRUCTION": [],
        "CONTRACT_CONSTRAINTS": [],
    }
    current: str | None = None
    for line in source.splitlines():
        stripped = line.strip()
        if stripped == "## CAPABILITY_INSTRUCTION":
            current = "CAPABILITY_INSTRUCTION"
            continue
        if stripped == "## CONTRACT_CONSTRAINTS":
            current = "CONTRACT_CONSTRAINTS"
            continue
        if stripped.startswith("## "):
            current = None
            continue
        if current is not None:
            sections[current].append(line)

    parsed = {key: "\n".join(value).strip() for key, value in sections.items()}
    missing = [key for key, value in parsed.items() if value == ""]
    if missing:
        raise ValueError(f"Runtime prompt asset missing sections: {', '.join(missing)}")
    return parsed


def _render_fields(fields: tuple[PromptContextField, ...]) -> str:
    if not fields:
        return "{}"
    return "\n".join(f"{field.name}: {field.value}" for field in fields)


def _render_evidence_refs(evidence_refs: tuple[EvidenceRef, ...]) -> str:
    return "\n".join(f"{ref.source.value}:{ref.identity.value}" for ref in evidence_refs)


def _join_or_none(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "NONE"


def _schema_guidance_for(
    output_type_name: str, output_type: type, status_values: tuple[str, ...]
) -> str:
    field_names = ", ".join(field.name for field in fields(output_type))
    statuses = ", ".join(status_values)
    return (
        f"Output contract: CapabilityResult[{output_type_name}]\n"
        f"CapabilityResult status values: {statuses}\n"
        f"{output_type_name} dataclass fields: {field_names}"
    )


def _proposal_json_guidance() -> str:
    return (
        "Return JSON with keys constraints, preferences, unresolved_semantics, source_input, "
        "evidence, ambiguity_reasons, insufficient_context. "
        "HardConstraint item shape: {constraint_id, scope, operator, value}. "
        "Allowed constraint scopes: ORIGIN_AIRPORT, DESTINATION_AIRPORT, DEPARTURE_DATE, "
        "DEPARTURE_TIME, CABIN_CLASS, PASSENGER_COUNT, MAX_PRICE. "
        "Use AirportCode value strings like PEK/PVG/SHA, LocalDate YYYY-MM-DD, LocalTime HH:MM:SS, "
        "Money {amount, currency}. SoftPreference item shape: {preference_id, scope, "
        "importance, value}. Use empty arrays for unknown list fields."
    )


def _patch_json_guidance() -> str:
    return (
        "Return JSON with keys operations, unresolved_semantics, source_input, "
        "based_on_requirement_id, based_on_requirement_version, evidence, ambiguity_reasons, "
        "insufficient_context. Patch operation shape: {action, target_id, item}. "
        "For REPLACE/REMOVE use a target_id from trusted context. For ADD omit target_id. "
        "Item uses the same HardConstraint or SoftPreference JSON shape."
    )
