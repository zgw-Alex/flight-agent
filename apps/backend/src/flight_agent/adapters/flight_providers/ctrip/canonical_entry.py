"""Thin CTRIP mapped-to-canonical entry through the provider-neutral normalizer."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.adapters.flight_providers.ctrip.browser_probe import CTRIP_PROVIDER_ID
from flight_agent.domain.shared import DomainInvariantViolation
from flight_agent.ports import (
    CommonNormalizer,
    MappingIssue,
    NormalizationContext,
    NormalizationResult,
    ProviderMappingResult,
)


@dataclass(frozen=True)
class CtripCanonicalEntryResult:
    """Provider-local envelope that keeps mapped issues beside normalized output."""

    mapping_result: ProviderMappingResult
    normalization_result: NormalizationResult
    provider_mapping_issues: tuple[MappingIssue, ...]


class CtripCanonicalEntry:
    """Pass CTRIP mapped data into the existing CommonNormalizer without reinterpretation."""

    def __init__(
        self,
        *,
        common_normalizer: CommonNormalizer,
        normalization_context: NormalizationContext,
    ) -> None:
        self._common_normalizer = common_normalizer
        self._normalization_context = normalization_context

    def canonicalize(self, mapping_result: ProviderMappingResult) -> CtripCanonicalEntryResult:
        if mapping_result.provider_id.value != CTRIP_PROVIDER_ID:
            raise DomainInvariantViolation("CTRIP canonical entry requires CTRIP mapped input")

        normalization_result = self._common_normalizer.normalize(
            mapping_result,
            self._normalization_context,
        )
        return CtripCanonicalEntryResult(
            mapping_result=mapping_result,
            normalization_result=normalization_result,
            provider_mapping_issues=mapping_result.issues,
        )
