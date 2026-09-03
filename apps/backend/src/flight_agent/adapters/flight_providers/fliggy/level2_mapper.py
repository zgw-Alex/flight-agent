"""FLIGGY Level-2 expanded-offer evidence mapper for M9-FLIGGY-LEVEL2-MAPPER-U1."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TypeGuard

from flight_agent.domain.flights import PriceSemantics
from flight_agent.domain.shared import DomainValue
from flight_agent.ports import (
    MappedItinerary,
    MappedItineraryRef,
    MappedOffer,
    MappedOfferRef,
    MappedProvenance,
    MappedSegment,
    MappedSegmentRef,
    MapperVersion,
    MappingIssue,
    MappingIssueCategory,
    MappingStatistics,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderMappingResult,
    ProviderRawEvidence,
    ProviderSearchResult,
    raw_evidence_ref,
)
from flight_agent.ports.flight_providers import RawEvidenceValue

FLIGGY_LEVEL2_PROVIDER_MAPPER_VERSION = MapperVersion("m9-fliggy-level2-offer-mapper-v1")

_SUCCESS_EXPANDED = "SUCCESS_EXPANDED"
_LOWER_BOUND_PRICE_TOKENS = ("起", "起售", "起价", "最低", "from")
_BAGGAGE_WEIGHT_TOKENS = ("kg", "公斤", "千克")
_BAGGAGE_PIECES_PATTERN = re.compile(r"([0-9一二两三四五六七八九])\s*件")
_REFUND_FALSE_TOKENS = ("不可退", "不得退", "不支持退", "不能退", "不可退款", "不可退票")
_REFUND_TRUE_TEXTS = {"可退", "支持退票", "可退款", "免费退", "免费退票"}


@dataclass(frozen=True)
class FliggyLevel2ParentContext:
    parent_level1_ref: str
    segments: tuple[MappedSegment, ...]
    itinerary: MappedItinerary

    def __post_init__(self) -> None:
        if self.parent_level1_ref.strip() == "":
            raise ValueError("FliggyLevel2ParentContext requires a parent Level-1 ref")
        if len(self.segments) == 0:
            raise ValueError("FliggyLevel2ParentContext requires at least one segment")
        segment_refs = {segment.mapped_segment_ref for segment in self.segments}
        if any(ref not in segment_refs for ref in self.itinerary.segment_refs):
            raise ValueError("FliggyLevel2ParentContext itinerary must reference provided segments")
        object.__setattr__(self, "segments", tuple(self.segments))


class FliggyLevel2OfferMapper:
    """Map sanitized FLIGGY Level-2 offer rows into existing M4 mapped types."""

    def __init__(
        self,
        parent_contexts: Iterable[FliggyLevel2ParentContext],
        mapper_version: MapperVersion = FLIGGY_LEVEL2_PROVIDER_MAPPER_VERSION,
    ) -> None:
        self._mapper_version = mapper_version
        self._parent_contexts = {context.parent_level1_ref: context for context in parent_contexts}

    @property
    def mapper_version(self) -> MapperVersion:
        return self._mapper_version

    def map(self, provider_result: ProviderSearchResult) -> ProviderMappingResult:
        if provider_result.raw_evidence is None:
            return _empty_mapping_result(provider_result, self.mapper_version)
        if provider_result.execution_status is not ProviderExecutionStatus.SUCCESS:
            return _empty_mapping_result(provider_result, self.mapper_version)

        context = _Level2MappingContext(provider_result.raw_evidence, self._parent_contexts)
        payload = _thaw_raw_value(provider_result.raw_evidence.payload)
        if not isinstance(payload, dict):
            context.issue(
                "raw:payload",
                "payload",
                MappingIssueCategory.UNSUPPORTED_RAW_SHAPE,
                "FLIGGY Level-2 payload root is not an object",
            )
            return context.result(self.mapper_version, ProviderDataStatus.UNUSABLE)

        if payload.get("outcome") != _SUCCESS_EXPANDED:
            return context.result(self.mapper_version, _data_status_for_unmapped_outcome(provider_result))

        offer_rows = payload.get("offer_rows")
        if not isinstance(offer_rows, list):
            context.issue(
                "raw:offer_rows",
                "offer_rows",
                MappingIssueCategory.UNSUPPORTED_RAW_SHAPE,
                "FLIGGY Level-2 offer rows are missing or not a list",
            )
            return context.result(self.mapper_version, ProviderDataStatus.UNUSABLE)

        for index, row in enumerate(offer_rows, start=1):
            context.map_offer_row(row, index)

        return context.result(self.mapper_version, _mapped_data_status(provider_result, context))


class _Level2MappingContext:
    def __init__(
        self,
        raw_evidence: ProviderRawEvidence,
        parent_contexts: dict[str, FliggyLevel2ParentContext],
    ) -> None:
        self.raw_evidence = raw_evidence
        self.parent_contexts = parent_contexts
        self.issues: list[MappingIssue] = []
        self.segments: list[MappedSegment] = []
        self.itineraries: list[MappedItinerary] = []
        self.offers: list[MappedOffer] = []
        self.raw_offer_count = 0
        self.dropped_offer_count = 0

    def map_offer_row(self, row: object, index: int) -> None:
        self.raw_offer_count += 1
        if not isinstance(row, dict):
            self._drop_offer(
                f"fliggy-level2-offer-row:index:{index}",
                "offer_rows",
                "FLIGGY Level-2 offer row is not an object",
            )
            return

        raw_record_ref = _row_record_ref(row, index)
        parent_ref = _string_value(row.get("parent_level1_ref"))
        if parent_ref is None:
            self._drop_offer(raw_record_ref, "parent_level1_ref", "Level-2 row lacks parent Level-1 ref")
            return
        parent = self.parent_contexts.get(parent_ref)
        if parent is None:
            self.issue(
                raw_record_ref,
                "parent_level1_ref",
                MappingIssueCategory.BROKEN_GRAPH_REFERENCE,
                "Level-2 row parent Level-1 ref has no mapped parent context",
            )
            self.dropped_offer_count += 1
            return

        amount = _positive_int(row.get("price_amount"))
        currency = _currency(row.get("price_currency"))
        raw_price_text = _field_text(row, "raw_price_text")
        if amount is None or currency is None or raw_price_text is None:
            self._drop_offer(raw_record_ref, "price", "Level-2 row price amount/currency is not mappable")
            return

        provider_offer_id = _provider_offer_id(parent_ref, _row_identity(row, index), amount)
        baggage_pieces = _checked_baggage_pieces(row, parent)
        row_segments, row_itinerary = self._copy_parent_graph_for_row(parent, provider_offer_id, baggage_pieces)
        offer = MappedOffer(
            mapped_offer_ref=MappedOfferRef(f"mapped-offer:{provider_offer_id}"),
            provider_offer_id=provider_offer_id,
            itinerary_ref=row_itinerary.mapped_itinerary_ref,
            total_amount=DomainValue.known(amount),
            currency=DomainValue.known(currency),
            refundable=_refundable(row),
            booking_reference=DomainValue.not_provided(),
            provenance=self.provenance(raw_record_ref, provider_offer_id),
            price_semantics=_price_semantics(raw_price_text),
        )
        self.segments.extend(row_segments)
        self.itineraries.append(row_itinerary)
        self.offers.append(offer)

    def _copy_parent_graph_for_row(
        self,
        parent: FliggyLevel2ParentContext,
        provider_offer_id: str,
        baggage_pieces: DomainValue[int],
    ) -> tuple[tuple[MappedSegment, ...], MappedItinerary]:
        copied_segments: list[MappedSegment] = []
        segment_ref_map: dict[MappedSegmentRef, MappedSegmentRef] = {}
        for segment in parent.segments:
            provider_segment_id = f"{segment.provider_segment_id}:level2:{provider_offer_id}"
            mapped_segment_ref = MappedSegmentRef(f"mapped-segment:{provider_segment_id}")
            copied_segments.append(
                replace(
                    segment,
                    mapped_segment_ref=mapped_segment_ref,
                    provider_segment_id=provider_segment_id,
                    checked_baggage_pieces=baggage_pieces
                    if len(parent.itinerary.segment_refs) == 1
                    else DomainValue.not_provided(),
                    provenance=self.provenance(parent.parent_level1_ref, provider_segment_id),
                )
            )
            segment_ref_map[segment.mapped_segment_ref] = mapped_segment_ref

        provider_itinerary_id = f"{parent.itinerary.provider_itinerary_id}:level2:{provider_offer_id}"
        itinerary = MappedItinerary(
            mapped_itinerary_ref=MappedItineraryRef(f"mapped-itinerary:{provider_itinerary_id}"),
            provider_itinerary_id=provider_itinerary_id,
            segment_refs=tuple(segment_ref_map[ref] for ref in parent.itinerary.segment_refs),
            provenance=self.provenance(parent.parent_level1_ref, provider_itinerary_id),
        )
        return tuple(copied_segments), itinerary

    def provenance(self, raw_record_ref: str, provider_source_id: str) -> MappedProvenance:
        return MappedProvenance(
            provider_id=self.raw_evidence.provider_id,
            acquisition_id=self.raw_evidence.acquisition_id,
            raw_evidence_refs=raw_evidence_ref(self.raw_evidence),
            raw_record_ref=raw_record_ref,
            provider_source_id=provider_source_id,
        )

    def result(self, mapper_version: MapperVersion, data_status: ProviderDataStatus) -> ProviderMappingResult:
        return ProviderMappingResult(
            provider_id=self.raw_evidence.provider_id,
            acquisition_id=self.raw_evidence.acquisition_id,
            search_plan_id=self.raw_evidence.search_plan_id,
            mapper_version=mapper_version,
            data_status=data_status,
            segments=tuple(self.segments),
            itineraries=tuple(self.itineraries),
            offers=tuple(self.offers),
            issues=tuple(self.issues),
            statistics=MappingStatistics(
                raw_segment_count=0,
                mapped_segment_count=len(self.segments),
                dropped_segment_count=0,
                raw_itinerary_count=0,
                mapped_itinerary_count=len(self.itineraries),
                dropped_itinerary_count=0,
                raw_offer_count=self.raw_offer_count,
                mapped_offer_count=len(self.offers),
                dropped_offer_count=self.dropped_offer_count,
                issue_count=len(self.issues),
            ),
        )

    @property
    def has_drops_or_issues(self) -> bool:
        return self.dropped_offer_count + len(self.issues) > 0

    def issue(
        self,
        raw_record_ref: str,
        raw_path: str,
        category: MappingIssueCategory,
        detail: str,
    ) -> None:
        self.issues.append(
            MappingIssue(
                provider_id=self.raw_evidence.provider_id,
                acquisition_id=self.raw_evidence.acquisition_id,
                raw_record_ref=raw_record_ref,
                raw_path=raw_path,
                category=category,
                detail=detail,
            )
        )

    def _drop_offer(self, raw_record_ref: str, raw_path: str, detail: str) -> None:
        self.issue(raw_record_ref, raw_path, MappingIssueCategory.MALFORMED_REQUIRED_FIELD, detail)
        self.dropped_offer_count += 1


def _empty_mapping_result(
    provider_result: ProviderSearchResult,
    mapper_version: MapperVersion,
) -> ProviderMappingResult:
    return ProviderMappingResult(
        provider_id=provider_result.provider_id,
        acquisition_id=provider_result.acquisition_id,
        search_plan_id=provider_result.search_plan_id,
        mapper_version=mapper_version,
        data_status=provider_result.data_status,
        segments=(),
        itineraries=(),
        offers=(),
        issues=(),
        statistics=MappingStatistics(
            raw_segment_count=0,
            mapped_segment_count=0,
            dropped_segment_count=0,
            raw_itinerary_count=0,
            mapped_itinerary_count=0,
            dropped_itinerary_count=0,
            raw_offer_count=0,
            mapped_offer_count=0,
            dropped_offer_count=0,
            issue_count=0,
        ),
    )


def _data_status_for_unmapped_outcome(provider_result: ProviderSearchResult) -> ProviderDataStatus:
    if provider_result.data_status is ProviderDataStatus.EMPTY:
        return ProviderDataStatus.EMPTY
    return provider_result.data_status


def _mapped_data_status(
    provider_result: ProviderSearchResult,
    context: _Level2MappingContext,
) -> ProviderDataStatus:
    if provider_result.data_status is ProviderDataStatus.EMPTY:
        return ProviderDataStatus.EMPTY
    if context.has_drops_or_issues:
        if len(context.offers) == 0:
            return ProviderDataStatus.UNUSABLE
        return ProviderDataStatus.PARTIAL
    return provider_result.data_status


def _field_text(payload: dict[str, object], field_name: str) -> str | None:
    value = payload.get(field_name)
    if not isinstance(value, dict) or value.get("status") != "OBSERVED":
        return None
    raw_text = value.get("raw_text")
    if not isinstance(raw_text, str) or raw_text.strip() == "":
        return None
    return " ".join(raw_text.split())


def _row_record_ref(row: dict[str, object], index: int) -> str:
    row_ref = _string_value(row.get("offer_row_ref"))
    if row_ref is not None:
        return f"fliggy-level2-offer-row:{row_ref}"
    return f"fliggy-level2-offer-row:index:{index}"


def _row_identity(row: dict[str, object], index: int) -> str:
    row_ref = _string_value(row.get("offer_row_ref"))
    if row_ref is not None:
        return _identity_token(row_ref)
    sequence = _positive_int(row.get("sequence"))
    if sequence is not None:
        return f"sequence-{sequence}"
    return f"index-{index}"


def _provider_offer_id(parent_ref: str, row_identity: str, amount: int) -> str:
    return f"fliggy-level2-offer-{_identity_token(parent_ref)}-{row_identity}-{amount}"


def _identity_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower()
    return token or "unknown"


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _currency(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    currency = value.strip().upper()
    if len(currency) == 3 and currency.isascii() and currency.isalpha():
        return currency
    return None


def _price_semantics(raw_price_text: str) -> PriceSemantics:
    lowered = raw_price_text.lower()
    if any(token in lowered for token in _LOWER_BOUND_PRICE_TOKENS):
        return PriceSemantics.LOWER_BOUND
    return PriceSemantics.EXACT


def _checked_baggage_pieces(
    row: dict[str, object],
    parent: FliggyLevel2ParentContext,
) -> DomainValue[int]:
    if len(parent.itinerary.segment_refs) != 1:
        return DomainValue.not_provided()
    text = _field_text(row, "raw_baggage_text")
    if text is None:
        return DomainValue.not_provided()
    normalized = text.lower()
    if any(token in normalized for token in _BAGGAGE_WEIGHT_TOKENS):
        return DomainValue.not_provided()
    if any(token in text for token in ("无托运行李", "不含托运行李", "无免费托运行李")):
        return DomainValue.known(0)
    match = _BAGGAGE_PIECES_PATTERN.search(text)
    if match is None:
        return DomainValue.not_provided()
    value = _piece_count(match.group(1))
    if value is None:
        return DomainValue.not_provided()
    return DomainValue.known(value)


def _piece_count(value: str) -> int | None:
    digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value.isdigit():
        return int(value)
    return digits.get(value)


def _refundable(row: dict[str, object]) -> DomainValue[bool]:
    text = _field_text(row, "raw_refund_change_rule_text")
    if text is None:
        return DomainValue.not_provided()
    compact = re.sub(r"\s+", "", text)
    if any(token in compact for token in _REFUND_FALSE_TOKENS):
        return DomainValue.known(False)
    if compact in _REFUND_TRUE_TEXTS:
        return DomainValue.known(True)
    return DomainValue.not_provided()


def _string_value(value: object) -> str | None:
    if not isinstance(value, str) or value.strip() == "":
        return None
    return value.strip()


def _thaw_raw_value(value: RawEvidenceValue) -> object:
    if isinstance(value, tuple):
        if all(_is_raw_mapping_item(item) for item in value):
            mapped_value: dict[str, object] = {}
            for item in value:
                if _is_raw_mapping_item(item):
                    mapped_value[item[0]] = _thaw_raw_value(item[1])
            return mapped_value
        return [_thaw_raw_value(item) for item in value]
    return value


def _is_raw_mapping_item(value: object) -> TypeGuard[tuple[str, RawEvidenceValue]]:
    return isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str)
