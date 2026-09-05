"""FLIGGY Mapped-to-Canonical entry wiring for M9-FLIGGY-CANONICAL-U1."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from flight_agent.ports import (
    CommonNormalizer,
    NormalizationContext,
    NormalizationResult,
    ProviderId,
    ProviderMappingResult,
)

FLIGGY_CANONICAL_ENTRY_PROFILE_VERSION = "m9-fliggy-canonical-entry-profile-v1"


@dataclass(frozen=True)
class FliggyCanonicalEntryProfile:
    version: str
    airport_aliases: Mapping[str, str]

    def airport_code(self, value: str) -> str:
        normalized = _normalize_label(value)
        if _is_iata_code(normalized):
            return normalized
        alias = self.airport_aliases.get(normalized)
        if alias is None:
            return value
        return alias.strip().upper()


DEFAULT_FLIGGY_CANONICAL_ENTRY_PROFILE = FliggyCanonicalEntryProfile(
    version=FLIGGY_CANONICAL_ENTRY_PROFILE_VERSION,
    airport_aliases={
        "大兴": "PKX",
        "大兴国际机场": "PKX",
        "浦东": "PVG",
        "浦东国际机场T2": "PVG",
        "虹桥": "SHA",
        "虹桥T2": "SHA",
        "首都": "PEK",
        "首都T2": "PEK",
    },
)


class FliggyCanonicalEntry:
    """Prepare FLIGGY mapped output for the provider-neutral CommonNormalizer."""

    def __init__(
        self,
        *,
        profile: FliggyCanonicalEntryProfile = DEFAULT_FLIGGY_CANONICAL_ENTRY_PROFILE,
        common_normalizer: CommonNormalizer | None = None,
    ) -> None:
        self.profile = profile
        self._common_normalizer = common_normalizer or CommonNormalizer()

    def normalize(
        self,
        mapping_result: ProviderMappingResult,
        context: NormalizationContext,
    ) -> NormalizationResult:
        return self._common_normalizer.normalize(
            prepare_fliggy_mapping_for_canonical_entry(mapping_result, self.profile),
            context,
        )


def prepare_fliggy_mapping_for_canonical_entry(
    mapping_result: ProviderMappingResult,
    profile: FliggyCanonicalEntryProfile = DEFAULT_FLIGGY_CANONICAL_ENTRY_PROFILE,
) -> ProviderMappingResult:
    if mapping_result.provider_id != ProviderId("FLIGGY"):
        return mapping_result
    return ProviderMappingResult(
        provider_id=mapping_result.provider_id,
        acquisition_id=mapping_result.acquisition_id,
        search_plan_id=mapping_result.search_plan_id,
        mapper_version=mapping_result.mapper_version,
        data_status=mapping_result.data_status,
        segments=tuple(
            replace(
                segment,
                departure_airport=profile.airport_code(segment.departure_airport),
                arrival_airport=profile.airport_code(segment.arrival_airport),
            )
            for segment in mapping_result.segments
        ),
        itineraries=mapping_result.itineraries,
        offers=mapping_result.offers,
        issues=mapping_result.issues,
        statistics=mapping_result.statistics,
    )


def _normalize_label(value: str) -> str:
    return " ".join(value.split()).upper()


def _is_iata_code(value: str) -> bool:
    return len(value) == 3 and value.isascii() and value.isalpha()
