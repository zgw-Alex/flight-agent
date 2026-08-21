"""Provider-neutral provenance references."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.shared.errors import DomainInvariantViolation
from flight_agent.domain.shared.time import DomainInstant


@dataclass(frozen=True)
class ProvenanceRef:
    """A provider-neutral reference to where a domain fact came from."""

    source_type: str
    source_ref: str
    observed_at: DomainInstant | None = None
    detail_ref: str | None = None

    def __post_init__(self) -> None:
        if self.source_type.strip() == "":
            raise DomainInvariantViolation("ProvenanceRef requires source_type")
        if self.source_ref.strip() == "":
            raise DomainInvariantViolation("ProvenanceRef requires source_ref")
        if self.detail_ref is not None and self.detail_ref.strip() == "":
            raise DomainInvariantViolation("ProvenanceRef detail_ref must be non-empty when provided")
