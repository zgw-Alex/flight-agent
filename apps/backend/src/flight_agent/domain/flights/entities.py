"""Canonical flight graph entities."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from flight_agent.domain.flights.identity import ItineraryId, OfferId, SegmentId
from flight_agent.domain.shared import (
    DomainInstant,
    DomainInvariantViolation,
    DomainValue,
    OfferFreshness,
    ProvenanceRef,
)


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if self.amount <= Decimal(0):
            raise DomainInvariantViolation("Money amount must be positive")
        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise DomainInvariantViolation("Money currency requires a three-letter code")
        object.__setattr__(self, "currency", currency)


@dataclass(frozen=True)
class FlightSegment:
    segment_id: SegmentId
    marketing_carrier: str
    flight_number: str
    departure_airport: str
    arrival_airport: str
    departure_at: DomainInstant
    arrival_at: DomainInstant
    operating_carrier: DomainValue[str]
    aircraft_type: DomainValue[str]
    provenance: tuple[ProvenanceRef, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("marketing_carrier", self.marketing_carrier)
        _require_non_empty("flight_number", self.flight_number)
        departure = _airport_code(self.departure_airport, "departure_airport")
        arrival = _airport_code(self.arrival_airport, "arrival_airport")
        if departure == arrival:
            raise DomainInvariantViolation("FlightSegment airports must differ")
        if self.arrival_at.value <= self.departure_at.value:
            raise DomainInvariantViolation("FlightSegment arrival must be after departure")
        object.__setattr__(self, "departure_airport", departure)
        object.__setattr__(self, "arrival_airport", arrival)
        object.__setattr__(self, "provenance", tuple(self.provenance))


@dataclass(frozen=True, init=False)
class Itinerary:
    itinerary_id: ItineraryId
    segment_ids: tuple[SegmentId, ...]
    provenance: tuple[ProvenanceRef, ...]

    def __init__(
        self,
        itinerary_id: ItineraryId,
        segment_ids: tuple[SegmentId, ...],
        provenance: tuple[ProvenanceRef, ...] = (),
    ) -> None:
        segment_ids_tuple = tuple(segment_ids)
        if len(segment_ids_tuple) == 0:
            raise DomainInvariantViolation("Itinerary requires at least one SegmentId")
        object.__setattr__(self, "itinerary_id", itinerary_id)
        object.__setattr__(self, "segment_ids", segment_ids_tuple)
        object.__setattr__(self, "provenance", tuple(provenance))


@dataclass(frozen=True, init=False)
class Offer:
    offer_id: OfferId
    itinerary_id: ItineraryId
    total_price: Money
    offer_freshness: OfferFreshness
    booking_reference: DomainValue[str]
    provenance: tuple[ProvenanceRef, ...]

    def __init__(
        self,
        offer_id: OfferId,
        itinerary_id: ItineraryId,
        total_price: Money,
        offer_freshness: OfferFreshness,
        booking_reference: DomainValue[str],
        provenance: tuple[ProvenanceRef, ...] = (),
    ) -> None:
        object.__setattr__(self, "offer_id", offer_id)
        object.__setattr__(self, "itinerary_id", itinerary_id)
        object.__setattr__(self, "total_price", total_price)
        object.__setattr__(self, "offer_freshness", offer_freshness)
        object.__setattr__(self, "booking_reference", booking_reference)
        object.__setattr__(self, "provenance", tuple(provenance))


def _require_non_empty(name: str, value: str) -> None:
    if value.strip() == "":
        raise DomainInvariantViolation(f"{name} must be non-empty")


def _airport_code(value: str, name: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isascii() or not code.isalpha():
        raise DomainInvariantViolation(f"{name} requires a three-letter airport code")
    return code
