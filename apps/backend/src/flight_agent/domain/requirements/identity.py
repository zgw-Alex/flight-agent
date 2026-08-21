"""Typed identities for the Requirement bounded module."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.shared import DomainId


@dataclass(frozen=True)
class RequirementId(DomainId):
    """Opaque identity for a requirement evolution chain."""


@dataclass(frozen=True)
class ConstraintId(DomainId):
    """Opaque identity for a hard constraint."""


@dataclass(frozen=True)
class PreferenceId(DomainId):
    """Opaque identity for a soft preference."""
