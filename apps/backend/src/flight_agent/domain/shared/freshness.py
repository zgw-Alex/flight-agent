"""Freshness primitives shared across structural and offer facts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FreshnessState(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"


@dataclass(frozen=True)
class StructuralFreshness:
    state: FreshnessState


@dataclass(frozen=True)
class OfferFreshness:
    state: FreshnessState
