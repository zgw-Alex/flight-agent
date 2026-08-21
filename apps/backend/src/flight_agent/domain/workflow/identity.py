"""Typed identities for workflow and generated artifacts."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.shared import DomainId


@dataclass(frozen=True)
class ExecutionId(DomainId):
    """Opaque identity for an agent execution."""


@dataclass(frozen=True)
class RecommendationResultId(DomainId):
    """Opaque identity for a recommendation result artifact."""


@dataclass(frozen=True)
class ExplanationResultId(DomainId):
    """Opaque identity for an explanation result artifact."""


@dataclass(frozen=True)
class PublicationId(DomainId):
    """Opaque identity for a published recommendation artifact."""
