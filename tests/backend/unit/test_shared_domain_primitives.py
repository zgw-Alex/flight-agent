from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timezone, timedelta

import pytest

from flight_agent.domain.shared import (
    DomainId,
    DomainInstant,
    DomainInvariantViolation,
    DomainValue,
    FreshnessState,
    OfferFreshness,
    ProvenanceRef,
    RequirementVersion,
    SnapshotVersion,
    StructuralFreshness,
    ValueState,
)


class RequirementId(DomainId):
    pass


class SnapshotId(DomainId):
    pass


def test_domain_value_constructs_all_four_valid_states() -> None:
    assert DomainValue.known("PVG").state is ValueState.KNOWN
    assert DomainValue[str].unknown().state is ValueState.UNKNOWN
    assert DomainValue[str].not_provided().state is ValueState.NOT_PROVIDED
    assert DomainValue[str].not_applicable().state is ValueState.NOT_APPLICABLE


def test_known_domain_value_exposes_valid_non_empty_value() -> None:
    value = DomainValue.known("SHA")

    assert value.value == "SHA"


@pytest.mark.parametrize("missing", [None, "", "   ", b"", [], {}, set()])
def test_known_domain_value_rejects_missing_or_empty_value(missing: object) -> None:
    with pytest.raises(DomainInvariantViolation):
        DomainValue(ValueState.KNOWN, missing)


@pytest.mark.parametrize(
    "state",
    [ValueState.UNKNOWN, ValueState.NOT_PROVIDED, ValueState.NOT_APPLICABLE],
)
def test_non_known_domain_value_rejects_business_value(state: ValueState) -> None:
    with pytest.raises(DomainInvariantViolation):
        DomainValue(state, "unexpected")


def test_missing_domain_value_does_not_substitute_none() -> None:
    value = DomainValue[str].unknown()

    with pytest.raises(DomainInvariantViolation):
        _ = value.value


def test_domain_value_is_immutable() -> None:
    value = DomainValue.known("PEK")

    with pytest.raises(FrozenInstanceError):
        value.state = ValueState.UNKNOWN  # type: ignore[misc]


def test_typed_identity_construction_and_value_semantics() -> None:
    assert RequirementId("req-1") == RequirementId("req-1")
    assert RequirementId("req-1") != RequirementId("req-2")


def test_wrong_typed_identity_is_not_interchangeable() -> None:
    assert RequirementId("same-opaque-value") != SnapshotId("same-opaque-value")


@pytest.mark.parametrize("raw", ["", "   "])
def test_typed_identity_rejects_empty_value(raw: str) -> None:
    with pytest.raises(DomainInvariantViolation):
        RequirementId(raw)


def test_identity_is_immutable() -> None:
    identity = RequirementId("req-1")

    with pytest.raises(FrozenInstanceError):
        identity.value = "req-2"  # type: ignore[misc]


def test_requirement_and_snapshot_versions_are_independent_value_objects() -> None:
    assert RequirementVersion(1) == RequirementVersion(1)
    assert SnapshotVersion(1) == SnapshotVersion(1)
    assert RequirementVersion(1) != SnapshotVersion(1)


@pytest.mark.parametrize("raw", [0, -1, True])
def test_versions_reject_invalid_values(raw: int | bool) -> None:
    with pytest.raises(DomainInvariantViolation):
        RequirementVersion(raw)  # type: ignore[arg-type]
    with pytest.raises(DomainInvariantViolation):
        SnapshotVersion(raw)  # type: ignore[arg-type]


def test_version_is_immutable() -> None:
    version = RequirementVersion(1)

    with pytest.raises(FrozenInstanceError):
        version.value = 2  # type: ignore[misc]


def test_domain_instant_accepts_timezone_aware_datetime() -> None:
    instant = DomainInstant(datetime(2026, 8, 21, 9, 30, tzinfo=UTC))

    assert instant.value.tzinfo is UTC


def test_domain_instant_rejects_naive_datetime() -> None:
    with pytest.raises(DomainInvariantViolation):
        DomainInstant(datetime(2026, 8, 21, 9, 30))


def test_domain_instant_has_value_semantics_and_is_immutable() -> None:
    first = DomainInstant(datetime(2026, 8, 21, 9, 30, tzinfo=UTC))
    second = DomainInstant(datetime(2026, 8, 21, 17, 30, tzinfo=timezone(timedelta(hours=8))))

    assert first == second
    with pytest.raises(FrozenInstanceError):
        first.value = datetime(2026, 8, 21, 10, 30, tzinfo=UTC)  # type: ignore[misc]


def test_provenance_ref_constructs_provider_neutral_reference() -> None:
    provenance = ProvenanceRef(
        source_type="provider-search",
        source_ref="search-run-1",
        observed_at=DomainInstant(datetime(2026, 8, 21, 9, 30, tzinfo=UTC)),
        detail_ref="offer-row-7",
    )

    assert provenance.source_type == "provider-search"
    assert not hasattr(provenance, "raw_payload")


@pytest.mark.parametrize(
    ("source_type", "source_ref", "detail_ref"),
    [("", "ref", None), ("type", "", None), ("type", "ref", "")],
)
def test_provenance_ref_rejects_missing_structure(
    source_type: str, source_ref: str, detail_ref: str | None
) -> None:
    with pytest.raises(DomainInvariantViolation):
        ProvenanceRef(source_type=source_type, source_ref=source_ref, detail_ref=detail_ref)


def test_provenance_ref_is_immutable() -> None:
    provenance = ProvenanceRef(source_type="manual-entry", source_ref="note-1")

    with pytest.raises(FrozenInstanceError):
        provenance.source_ref = "note-2"  # type: ignore[misc]


def test_structural_and_offer_freshness_are_independent_dimensions() -> None:
    structural = StructuralFreshness(FreshnessState.FRESH)
    offer = OfferFreshness(FreshnessState.STALE)

    assert structural.state is FreshnessState.FRESH
    assert offer.state is FreshnessState.STALE
    assert structural != offer


def test_freshness_primitives_do_not_define_ttl_or_refresh_policy() -> None:
    assert not hasattr(StructuralFreshness(FreshnessState.FRESH), "ttl")
    assert not hasattr(OfferFreshness(FreshnessState.STALE), "refresh")
