"""Component-specific decision policy version foundation."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.shared import DomainInvariantViolation


@dataclass(frozen=True)
class DecisionPolicyVersion:
    """Opaque version for one decision component policy."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or self.value.strip() == "":
            raise DomainInvariantViolation("DecisionPolicyVersion requires a non-empty value")


@dataclass(frozen=True)
class FeatureDefinitionVersion:
    """Opaque version for one feature definition's calculation semantics."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or self.value.strip() == "":
            raise DomainInvariantViolation("FeatureDefinitionVersion requires a non-empty value")


@dataclass(frozen=True)
class DecisionPolicySet:
    """Independent policy versions for the M6 decision pipeline components."""

    derived_feature_policy_version: DecisionPolicyVersion
    filter_policy_version: DecisionPolicyVersion
    ranking_policy_version: DecisionPolicyVersion
    recommendation_policy_version: DecisionPolicyVersion
    relaxation_policy_version: DecisionPolicyVersion
    decision_pipeline_version: DecisionPolicyVersion | None = None
