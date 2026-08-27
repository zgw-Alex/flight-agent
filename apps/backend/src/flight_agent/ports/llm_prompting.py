"""Provider-neutral runtime prompt and context contracts for M8-U2."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.ports.llm_capabilities import LLMCapabilityName


class PromptSectionRole(str, Enum):
    CAPABILITY_INSTRUCTION = "CAPABILITY_INSTRUCTION"
    CONTRACT_CONSTRAINTS = "CONTRACT_CONSTRAINTS"
    OUTPUT_SCHEMA_GUIDANCE = "OUTPUT_SCHEMA_GUIDANCE"
    STRUCTURED_TRUSTED_CONTEXT = "STRUCTURED_TRUSTED_CONTEXT"
    UNTRUSTED_PAYLOAD = "UNTRUSTED_PAYLOAD"


@dataclass(frozen=True)
class PromptFamilyId:
    value: str

    def __post_init__(self) -> None:
        if self.value.strip() == "":
            raise ValueError("PromptFamilyId requires a non-empty value")


@dataclass(frozen=True)
class PromptTemplateVersion:
    value: str

    def __post_init__(self) -> None:
        if self.value.strip() == "":
            raise ValueError("PromptTemplateVersion requires a non-empty value")


@dataclass(frozen=True)
class OutputSchemaVersion:
    value: str

    def __post_init__(self) -> None:
        if self.value.strip() == "":
            raise ValueError("OutputSchemaVersion requires a non-empty value")


@dataclass(frozen=True)
class RuntimePromptFamily:
    family_id: PromptFamilyId
    capability: LLMCapabilityName
    prompt_template_version: PromptTemplateVersion
    output_schema_version: OutputSchemaVersion
    asset_path: str

    def __post_init__(self) -> None:
        if self.asset_path.strip() == "":
            raise ValueError("RuntimePromptFamily asset_path must be non-empty")


@dataclass(frozen=True)
class RuntimePromptTemplate:
    family: RuntimePromptFamily
    capability_instruction: str
    contract_constraints: str

    def __post_init__(self) -> None:
        if self.capability_instruction.strip() == "":
            raise ValueError("RuntimePromptTemplate capability_instruction must be non-empty")
        if self.contract_constraints.strip() == "":
            raise ValueError("RuntimePromptTemplate contract_constraints must be non-empty")


@dataclass(frozen=True)
class PromptContextField:
    name: str
    value: str

    def __post_init__(self) -> None:
        if self.name.strip() == "":
            raise ValueError("PromptContextField name must be non-empty")
        if self.value.strip() == "":
            raise ValueError("PromptContextField value must be non-empty")


@dataclass(frozen=True)
class PromptContextProjection:
    capability: LLMCapabilityName
    trusted_context: tuple[PromptContextField, ...]
    untrusted_payload: tuple[PromptContextField, ...]

    def __post_init__(self) -> None:
        trusted_names = tuple(field.name for field in self.trusted_context)
        untrusted_names = tuple(field.name for field in self.untrusted_payload)
        if len(frozenset(trusted_names)) != len(trusted_names):
            raise ValueError("Trusted context field names must be unique")
        if len(frozenset(untrusted_names)) != len(untrusted_names):
            raise ValueError("Untrusted payload field names must be unique")
        if frozenset(trusted_names).intersection(untrusted_names):
            raise ValueError("Trusted and untrusted field names must not overlap")


@dataclass(frozen=True)
class PromptRenderRequest:
    template: RuntimePromptTemplate
    context: PromptContextProjection

    def __post_init__(self) -> None:
        if self.template.family.capability is not self.context.capability:
            raise ValueError("Prompt template capability must match context capability")


@dataclass(frozen=True)
class PromptSection:
    role: PromptSectionRole
    content: str

    def __post_init__(self) -> None:
        if self.content.strip() == "":
            raise ValueError("PromptSection content must be non-empty")


@dataclass(frozen=True)
class RenderedPrompt:
    family: RuntimePromptFamily
    sections: tuple[PromptSection, ...]

    def __post_init__(self) -> None:
        roles = tuple(section.role for section in self.sections)
        if roles != tuple(PromptSectionRole):
            raise ValueError("RenderedPrompt sections must preserve the M8 prompt layer order")

    @property
    def text(self) -> str:
        return "\n\n".join(
            f"## {section.role.value}\n{section.content}" for section in self.sections
        )
