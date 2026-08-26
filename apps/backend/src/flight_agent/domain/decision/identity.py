"""Typed identities for M6 decision artifacts and runs."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.shared import DomainId


@dataclass(frozen=True)
class ConstraintEvaluationId(DomainId):
    """Opaque identity for a single constraint evaluation artifact."""


@dataclass(frozen=True)
class DerivedFeatureSetId(DomainId):
    """Opaque identity for a derived feature set artifact."""


@dataclass(frozen=True)
class FilterResultId(DomainId):
    """Opaque identity for a filter result artifact."""


@dataclass(frozen=True)
class RankingResultId(DomainId):
    """Opaque identity for a ranking result artifact."""


@dataclass(frozen=True)
class RelaxationResultId(DomainId):
    """Opaque identity for a relaxation result artifact."""


@dataclass(frozen=True)
class DerivedFeatureRunId(DomainId):
    """Opaque identity for a derived feature computation run."""


@dataclass(frozen=True)
class FilterRunId(DomainId):
    """Opaque identity for a filtering run."""


@dataclass(frozen=True)
class RankingRunId(DomainId):
    """Opaque identity for a ranking run."""


@dataclass(frozen=True)
class RecommendationRunId(DomainId):
    """Opaque identity for a recommendation selection run."""


@dataclass(frozen=True)
class RelaxationRunId(DomainId):
    """Opaque identity for a relaxation analysis run."""
