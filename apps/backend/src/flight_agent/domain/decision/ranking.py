"""M5 minimal lower-price ranking and M6 complete ranking engine."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol

from flight_agent.domain.decision.evaluation import OfferBackedItineraryCandidate
from flight_agent.domain.decision.features import (
    STOP_COUNT,
    TOTAL_PRICE,
    DerivedFeatureSet,
    FeatureKey,
    FeatureScalar,
    FeatureValue,
    FeatureValueType,
)
from flight_agent.domain.decision.filtering import CompleteFilterResult, FilterResult
from flight_agent.domain.decision.identity import RankingResultId, RankingRunId
from flight_agent.domain.decision.policy import DecisionPolicyVersion
from flight_agent.domain.flights import (
    CandidateSnapshot,
    CandidateSnapshotId,
    ItineraryId,
    Money,
    OfferId,
)
from flight_agent.domain.requirements import (
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    RequirementState,
    SoftPreference,
)
from flight_agent.domain.shared import (
    DomainInvariantViolation,
    DomainValue,
    RequirementVersion,
    SnapshotVersion,
    ValueState,
)
from flight_agent.domain.workflow import EvidenceRef, EvidenceSource

NEUTRAL_SCORE = Decimal(0)
DEGENERATE_NORMALIZED_VALUE = Decimal(1)


@dataclass(frozen=True)
class RankedCandidate:
    offer_id: OfferId
    itinerary_id: ItineraryId
    rank_position: int
    basis: str
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class RankingResult:
    snapshot_id: str
    ranked_candidates: tuple[RankedCandidate, ...]


class LowerPriceRanking:
    def rank(self, *, snapshot: CandidateSnapshot, filter_result: FilterResult) -> RankingResult:
        eligible = {
            offer_id
            for offer_id in filter_result.eligible_offer_ids
        }
        offers_by_rank = sorted(
            (offer for offer in snapshot.offers if offer.offer_id in eligible),
            key=lambda offer: (offer.total_price.amount, offer.offer_id.value),
        )
        return RankingResult(
            snapshot_id=snapshot.snapshot_id.value,
            ranked_candidates=tuple(
                RankedCandidate(
                    offer_id=offer.offer_id,
                    itinerary_id=offer.itinerary_id,
                    rank_position=index,
                    basis="lower price is better",
                    evidence=(EvidenceRef(EvidenceSource.OFFER, offer.offer_id),),
                )
                for index, offer in enumerate(offers_by_rank, start=1)
            ),
        )


class RankingViewKind(str, Enum):
    QUALIFIED = "QUALIFIED"
    UNCERTAIN = "UNCERTAIN"


class RankingPreferenceDirection(str, Enum):
    LOWER_IS_BETTER = "LOWER_IS_BETTER"
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"


class PreferenceContributionStatus(str, Enum):
    EVALUATED = "EVALUATED"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"


class NormalizationScope(str, Enum):
    POOL_RELATIVE = "POOL_RELATIVE"


@dataclass(frozen=True, init=False)
class RankingPreferencePolicy:
    preference_scope: PreferenceScope
    feature_key: FeatureKey
    direction: RankingPreferenceDirection
    normalizer_version: DecisionPolicyVersion

    def __init__(
        self,
        preference_scope: PreferenceScope,
        feature_key: FeatureKey,
        direction: RankingPreferenceDirection,
        normalizer_version: DecisionPolicyVersion,
    ) -> None:
        object.__setattr__(self, "preference_scope", preference_scope)
        object.__setattr__(self, "feature_key", feature_key)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "normalizer_version", normalizer_version)


@dataclass(frozen=True, init=False)
class RankingPolicySet:
    policy_version: DecisionPolicyVersion
    preference_policies: tuple[RankingPreferencePolicy, ...]
    low_importance_weight: Decimal
    medium_importance_weight: Decimal
    high_importance_weight: Decimal
    missing_feature_policy: str
    aggregation_rule: str
    tie_break_rule: tuple[str, ...]
    uncertain_pool_policy: str

    def __init__(
        self,
        policy_version: DecisionPolicyVersion,
        preference_policies: tuple[RankingPreferencePolicy, ...],
        *,
        low_importance_weight: Decimal = Decimal("0.5"),
        medium_importance_weight: Decimal = Decimal(1),
        high_importance_weight: Decimal = Decimal(2),
        missing_feature_policy: str = "available-weight-renormalization",
        aggregation_rule: str = "sum(weighted_contribution)/sum(evaluated_weight)",
        tie_break_rule: tuple[str, ...] = (
            "aggregate_score_desc",
            "evaluated_preference_coverage_desc",
            "canonical_candidate_identity_asc",
        ),
        uncertain_pool_policy: str = "rank-separately-preserve-unknown-eligibility",
    ) -> None:
        policies_tuple = tuple(preference_policies)
        scopes = tuple(policy.preference_scope for policy in policies_tuple)
        if len(frozenset(scopes)) != len(scopes):
            raise DomainInvariantViolation("RankingPolicySet requires unique preference scopes")
        if any(weight <= Decimal(0) for weight in (low_importance_weight, medium_importance_weight, high_importance_weight)):
            raise DomainInvariantViolation("RankingPolicySet importance weights must be positive")
        object.__setattr__(self, "policy_version", policy_version)
        object.__setattr__(self, "preference_policies", tuple(sorted(policies_tuple, key=lambda policy: policy.preference_scope.value)))
        object.__setattr__(self, "low_importance_weight", low_importance_weight)
        object.__setattr__(self, "medium_importance_weight", medium_importance_weight)
        object.__setattr__(self, "high_importance_weight", high_importance_weight)
        object.__setattr__(self, "missing_feature_policy", missing_feature_policy)
        object.__setattr__(self, "aggregation_rule", aggregation_rule)
        object.__setattr__(self, "tie_break_rule", tuple(tie_break_rule))
        object.__setattr__(self, "uncertain_pool_policy", uncertain_pool_policy)

    def policy_for(self, preference_scope: PreferenceScope) -> RankingPreferencePolicy:
        for policy in self.preference_policies:
            if policy.preference_scope is preference_scope:
                return policy
        raise DomainInvariantViolation(f"Unsupported ranking preference scope: {preference_scope.value}")

    def resolve_weight(self, importance: PreferenceImportance) -> Decimal:
        if importance is PreferenceImportance.LOW:
            return self.low_importance_weight
        if importance is PreferenceImportance.MEDIUM:
            return self.medium_importance_weight
        if importance is PreferenceImportance.HIGH:
            return self.high_importance_weight
        raise DomainInvariantViolation(f"Unsupported preference importance: {importance.value}")


@dataclass(frozen=True)
class PoolRelativeNormalizationEvidence:
    preference_scope: PreferenceScope
    feature_key: FeatureKey
    value_type: FeatureValueType
    known_value_count: int
    min_value: Decimal | None
    max_value: Decimal | None
    normalization_scope: NormalizationScope
    normalizer_version: DecisionPolicyVersion


@dataclass(frozen=True)
class NormalizedRankingValue:
    preference_id: PreferenceId
    preference_scope: PreferenceScope
    feature_key: FeatureKey
    candidate: OfferBackedItineraryCandidate
    value: DomainValue[Decimal]
    direction: RankingPreferenceDirection
    normalization_scope: NormalizationScope
    normalizer_version: DecisionPolicyVersion


@dataclass(frozen=True)
class PreferenceContribution:
    preference_id: PreferenceId
    preference_scope: PreferenceScope
    feature_key: FeatureKey
    candidate: OfferBackedItineraryCandidate
    status: PreferenceContributionStatus
    raw_feature_value: FeatureValue | None
    normalized_value: NormalizedRankingValue
    resolved_weight: Decimal
    weighted_contribution: DomainValue[Decimal]
    missing_reason: str | None = None


@dataclass(frozen=True)
class PreferenceCoverage:
    evaluated_preference_count: int
    total_applicable_preference_count: int
    evaluated_weight: Decimal
    total_applicable_weight: Decimal
    missing_preference_evidence: tuple[PreferenceId, ...]

    @property
    def evaluated_preference_coverage(self) -> Decimal:
        if self.total_applicable_preference_count == 0:
            return Decimal(1)
        return Decimal(self.evaluated_preference_count) / Decimal(self.total_applicable_preference_count)

    @property
    def evaluated_weight_coverage(self) -> Decimal:
        if self.total_applicable_weight == Decimal(0):
            return Decimal(1)
        return self.evaluated_weight / self.total_applicable_weight


@dataclass(frozen=True)
class TieBreakEvidence:
    aggregate_score: Decimal
    evaluated_preference_coverage: Decimal
    canonical_candidate_identity: str


@dataclass(frozen=True)
class RankingEntry:
    candidate: OfferBackedItineraryCandidate
    rank_position: int
    aggregate_score: Decimal
    preference_contributions: tuple[PreferenceContribution, ...]
    coverage: PreferenceCoverage
    tie_break_evidence: TieBreakEvidence


@dataclass(frozen=True)
class RankingRun:
    run_id: RankingRunId
    requirement_id: RequirementId
    requirement_version: RequirementVersion
    snapshot_id: CandidateSnapshotId
    snapshot_version: SnapshotVersion
    derived_feature_set_id: str
    filter_result_id: str
    ranking_view_kind: RankingViewKind
    ranking_policy_version: DecisionPolicyVersion
    applicable_preference_ids: tuple[PreferenceId, ...]
    pool_relative_normalization: tuple[PoolRelativeNormalizationEvidence, ...]


@dataclass(frozen=True, init=False)
class CompleteRankingResult:
    ranking_result_id: RankingResultId
    run_id: RankingRunId
    requirement_id: RequirementId
    requirement_version: RequirementVersion
    snapshot_id: CandidateSnapshotId
    snapshot_version: SnapshotVersion
    derived_feature_set_id: str
    filter_result_id: str
    ranking_view_kind: RankingViewKind
    ranking_policy_version: DecisionPolicyVersion
    entries: tuple[RankingEntry, ...]
    pool_relative_normalization: tuple[PoolRelativeNormalizationEvidence, ...]

    def __init__(
        self,
        ranking_result_id: RankingResultId,
        run_id: RankingRunId,
        requirement_id: RequirementId,
        requirement_version: RequirementVersion,
        snapshot_id: CandidateSnapshotId,
        snapshot_version: SnapshotVersion,
        derived_feature_set_id: str,
        filter_result_id: str,
        ranking_view_kind: RankingViewKind,
        ranking_policy_version: DecisionPolicyVersion,
        entries: tuple[RankingEntry, ...],
        pool_relative_normalization: tuple[PoolRelativeNormalizationEvidence, ...],
    ) -> None:
        entries_tuple = tuple(entries)
        if any(entry.rank_position != index for index, entry in enumerate(entries_tuple, start=1)):
            raise DomainInvariantViolation("RankingResult entries must have contiguous rank positions")
        object.__setattr__(self, "ranking_result_id", ranking_result_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "requirement_version", requirement_version)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "snapshot_version", snapshot_version)
        object.__setattr__(self, "derived_feature_set_id", derived_feature_set_id)
        object.__setattr__(self, "filter_result_id", filter_result_id)
        object.__setattr__(self, "ranking_view_kind", ranking_view_kind)
        object.__setattr__(self, "ranking_policy_version", ranking_policy_version)
        object.__setattr__(self, "entries", entries_tuple)
        object.__setattr__(self, "pool_relative_normalization", tuple(pool_relative_normalization))


class PreferenceNormalizer(Protocol):
    @property
    def preference_scope(self) -> PreferenceScope:
        ...

    @property
    def feature_key(self) -> FeatureKey:
        ...

    @property
    def value_type(self) -> FeatureValueType:
        ...

    @property
    def normalizer_version(self) -> DecisionPolicyVersion:
        ...

    def build_evidence(
        self,
        *,
        candidates: tuple[OfferBackedItineraryCandidate, ...],
        feature_set: DerivedFeatureSet,
    ) -> PoolRelativeNormalizationEvidence:
        ...

    def normalize(
        self,
        *,
        preference: SoftPreference,
        candidate: OfferBackedItineraryCandidate,
        feature_set: DerivedFeatureSet,
        policy: RankingPreferencePolicy,
        evidence: PoolRelativeNormalizationEvidence,
    ) -> PreferenceContribution:
        ...


@dataclass(frozen=True, init=False)
class NormalizerRegistry:
    normalizers: tuple[PreferenceNormalizer, ...]
    registry_version: DecisionPolicyVersion

    def __init__(
        self,
        normalizers: tuple[PreferenceNormalizer, ...],
        registry_version: DecisionPolicyVersion,
    ) -> None:
        normalizers_tuple = tuple(normalizers)
        scopes = tuple(normalizer.preference_scope for normalizer in normalizers_tuple)
        if len(frozenset(scopes)) != len(scopes):
            raise DomainInvariantViolation("NormalizerRegistry requires unique preference scopes")
        object.__setattr__(self, "normalizers", tuple(sorted(normalizers_tuple, key=lambda item: item.preference_scope.value)))
        object.__setattr__(self, "registry_version", registry_version)

    def get(self, preference_scope: PreferenceScope) -> PreferenceNormalizer:
        for normalizer in self.normalizers:
            if normalizer.preference_scope is preference_scope:
                return normalizer
        raise DomainInvariantViolation(f"Unsupported ranking preference scope: {preference_scope.value}")


@dataclass(frozen=True)
class PoolRelativeFeatureNormalizer:
    preference_scope: PreferenceScope
    feature_key: FeatureKey
    value_type: FeatureValueType
    normalizer_version: DecisionPolicyVersion

    def build_evidence(
        self,
        *,
        candidates: tuple[OfferBackedItineraryCandidate, ...],
        feature_set: DerivedFeatureSet,
    ) -> PoolRelativeNormalizationEvidence:
        known_values = tuple(
            scalar
            for candidate in candidates
            for scalar in (_known_decimal_feature(feature_set, candidate, self.feature_key, self.value_type),)
            if scalar is not None
        )
        return PoolRelativeNormalizationEvidence(
            preference_scope=self.preference_scope,
            feature_key=self.feature_key,
            value_type=self.value_type,
            known_value_count=len(known_values),
            min_value=min(known_values) if len(known_values) > 0 else None,
            max_value=max(known_values) if len(known_values) > 0 else None,
            normalization_scope=NormalizationScope.POOL_RELATIVE,
            normalizer_version=self.normalizer_version,
        )

    def normalize(
        self,
        *,
        preference: SoftPreference,
        candidate: OfferBackedItineraryCandidate,
        feature_set: DerivedFeatureSet,
        policy: RankingPreferencePolicy,
        evidence: PoolRelativeNormalizationEvidence,
    ) -> PreferenceContribution:
        raw_feature_value = _feature_value_for(feature_set, candidate, self.feature_key)
        normalized = NormalizedRankingValue(
            preference_id=preference.preference_id,
            preference_scope=preference.scope,
            feature_key=self.feature_key,
            candidate=candidate,
            value=DomainValue.not_provided(),
            direction=policy.direction,
            normalization_scope=NormalizationScope.POOL_RELATIVE,
            normalizer_version=self.normalizer_version,
        )
        missing_reason = _missing_reason(raw_feature_value, self.value_type)
        if missing_reason is not None:
            return PreferenceContribution(
                preference_id=preference.preference_id,
                preference_scope=preference.scope,
                feature_key=self.feature_key,
                candidate=candidate,
                status=PreferenceContributionStatus.MISSING_EVIDENCE,
                raw_feature_value=raw_feature_value,
                normalized_value=normalized,
                resolved_weight=Decimal(0),
                weighted_contribution=DomainValue.not_provided(),
                missing_reason=missing_reason,
            )
        if evidence.min_value is None or evidence.max_value is None:
            raise DomainInvariantViolation("Pool-relative normalization requires known pool evidence")
        if raw_feature_value is None:
            raise DomainInvariantViolation("Known ranking contribution requires FeatureValue evidence")
        raw_decimal = _feature_scalar_to_decimal(raw_feature_value.value.value, self.value_type)
        normalized_decimal = _normalize_decimal(raw_decimal, evidence.min_value, evidence.max_value, policy.direction)
        evaluated_normalized = NormalizedRankingValue(
            preference_id=preference.preference_id,
            preference_scope=preference.scope,
            feature_key=self.feature_key,
            candidate=candidate,
            value=DomainValue.known(normalized_decimal),
            direction=policy.direction,
            normalization_scope=NormalizationScope.POOL_RELATIVE,
            normalizer_version=self.normalizer_version,
        )
        return PreferenceContribution(
            preference_id=preference.preference_id,
            preference_scope=preference.scope,
            feature_key=self.feature_key,
            candidate=candidate,
            status=PreferenceContributionStatus.EVALUATED,
            raw_feature_value=raw_feature_value,
            normalized_value=evaluated_normalized,
            resolved_weight=Decimal(0),
            weighted_contribution=DomainValue.known(Decimal(0)),
        )


class CompleteRankingEngine:
    def __init__(self, *, normalizer_registry: NormalizerRegistry) -> None:
        self.normalizer_registry = normalizer_registry

    def rank(
        self,
        *,
        ranking_result_id: RankingResultId,
        ranking_run_id: RankingRunId,
        requirement: RequirementState,
        snapshot: CandidateSnapshot,
        feature_set: DerivedFeatureSet,
        filter_result: CompleteFilterResult,
        ranking_view_kind: RankingViewKind,
        ranking_policy_set: RankingPolicySet,
    ) -> tuple[RankingRun, CompleteRankingResult]:
        _validate_lineage(requirement, snapshot, feature_set, filter_result)
        candidates = _candidates_for_view(filter_result, ranking_view_kind)
        preferences = _applicable_preferences(requirement.preferences, ranking_policy_set, self.normalizer_registry)
        normalization = _normalization_evidence(candidates, preferences, feature_set, ranking_policy_set, self.normalizer_registry)
        unranked_entries = tuple(
            _entry_for_candidate(
                candidate=candidate,
                preferences=preferences,
                feature_set=feature_set,
                ranking_policy_set=ranking_policy_set,
                normalizer_registry=self.normalizer_registry,
                normalization=normalization,
            )
            for candidate in candidates
        )
        ordered_entries = tuple(
            _with_rank(entry, rank_position=index)
            for index, entry in enumerate(
                sorted(
                    unranked_entries,
                    key=lambda entry: (
                        -entry.aggregate_score,
                        -entry.coverage.evaluated_preference_coverage,
                        entry.tie_break_evidence.canonical_candidate_identity,
                    ),
                ),
                start=1,
            )
        )
        run = RankingRun(
            run_id=ranking_run_id,
            requirement_id=requirement.requirement_id,
            requirement_version=requirement.version,
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.version,
            derived_feature_set_id=feature_set.feature_set_id.value,
            filter_result_id=filter_result.filter_result_id.value,
            ranking_view_kind=ranking_view_kind,
            ranking_policy_version=ranking_policy_set.policy_version,
            applicable_preference_ids=tuple(preference.preference_id for preference in preferences),
            pool_relative_normalization=normalization,
        )
        result = CompleteRankingResult(
            ranking_result_id=ranking_result_id,
            run_id=ranking_run_id,
            requirement_id=requirement.requirement_id,
            requirement_version=requirement.version,
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.version,
            derived_feature_set_id=feature_set.feature_set_id.value,
            filter_result_id=filter_result.filter_result_id.value,
            ranking_view_kind=ranking_view_kind,
            ranking_policy_version=ranking_policy_set.policy_version,
            entries=ordered_entries,
            pool_relative_normalization=normalization,
        )
        return run, result


def m6_default_ranking_policy_set() -> RankingPolicySet:
    return RankingPolicySet(
        policy_version=DecisionPolicyVersion("ranking-policy-v1"),
        preference_policies=(
            RankingPreferencePolicy(
                preference_scope=PreferenceScope.PRICE,
                feature_key=TOTAL_PRICE,
                direction=RankingPreferenceDirection.LOWER_IS_BETTER,
                normalizer_version=DecisionPolicyVersion("pool-relative-price-v1"),
            ),
            RankingPreferencePolicy(
                preference_scope=PreferenceScope.FEWER_STOPS,
                feature_key=STOP_COUNT,
                direction=RankingPreferenceDirection.LOWER_IS_BETTER,
                normalizer_version=DecisionPolicyVersion("pool-relative-stop-count-v1"),
            ),
        ),
    )


def m6_default_normalizer_registry() -> NormalizerRegistry:
    policy = m6_default_ranking_policy_set()
    return NormalizerRegistry(
        normalizers=tuple(
            PoolRelativeFeatureNormalizer(
                preference_scope=preference_policy.preference_scope,
                feature_key=preference_policy.feature_key,
                value_type=FeatureValueType.MONEY
                if preference_policy.feature_key == TOTAL_PRICE
                else FeatureValueType.INTEGER,
                normalizer_version=preference_policy.normalizer_version,
            )
            for preference_policy in policy.preference_policies
        ),
        registry_version=DecisionPolicyVersion("ranking-normalizer-registry-v1"),
    )


def m6_default_complete_ranking_engine() -> CompleteRankingEngine:
    return CompleteRankingEngine(normalizer_registry=m6_default_normalizer_registry())


def _validate_lineage(
    requirement: RequirementState,
    snapshot: CandidateSnapshot,
    feature_set: DerivedFeatureSet,
    filter_result: CompleteFilterResult,
) -> None:
    if feature_set.input_lineage.snapshot_id != snapshot.snapshot_id:
        raise DomainInvariantViolation("Ranking feature set snapshot lineage does not match CandidateSnapshot")
    if feature_set.input_lineage.snapshot_version != snapshot.version:
        raise DomainInvariantViolation("Ranking feature set snapshot version does not match CandidateSnapshot")
    if feature_set.input_lineage.requirement_id != requirement.requirement_id:
        raise DomainInvariantViolation("Ranking feature set requirement lineage does not match RequirementState")
    if feature_set.input_lineage.requirement_version != requirement.version:
        raise DomainInvariantViolation("Ranking feature set requirement version does not match RequirementState")
    if filter_result.requirement_id != requirement.requirement_id or filter_result.requirement_version != requirement.version:
        raise DomainInvariantViolation("Ranking FilterResult requirement lineage does not match RequirementState")
    if filter_result.snapshot_id != snapshot.snapshot_id or filter_result.snapshot_version != snapshot.version:
        raise DomainInvariantViolation("Ranking FilterResult snapshot lineage does not match CandidateSnapshot")
    if filter_result.derived_feature_set_id != feature_set.feature_set_id.value:
        raise DomainInvariantViolation("Ranking FilterResult feature lineage does not match DerivedFeatureSet")


def _candidates_for_view(
    filter_result: CompleteFilterResult,
    ranking_view_kind: RankingViewKind,
) -> tuple[OfferBackedItineraryCandidate, ...]:
    if ranking_view_kind is RankingViewKind.QUALIFIED:
        return tuple(sorted(filter_result.qualified_candidates, key=_canonical_candidate_identity))
    if ranking_view_kind is RankingViewKind.UNCERTAIN:
        return tuple(sorted(filter_result.uncertain_candidates, key=_canonical_candidate_identity))
    raise DomainInvariantViolation(f"Unsupported ranking view: {ranking_view_kind.value}")


def _applicable_preferences(
    preferences: Iterable[SoftPreference],
    ranking_policy_set: RankingPolicySet,
    normalizer_registry: NormalizerRegistry,
) -> tuple[SoftPreference, ...]:
    ordered = tuple(sorted(preferences, key=lambda preference: preference.preference_id.value))
    for preference in ordered:
        ranking_policy_set.policy_for(preference.scope)
        normalizer_registry.get(preference.scope)
    return ordered


def _normalization_evidence(
    candidates: tuple[OfferBackedItineraryCandidate, ...],
    preferences: tuple[SoftPreference, ...],
    feature_set: DerivedFeatureSet,
    ranking_policy_set: RankingPolicySet,
    normalizer_registry: NormalizerRegistry,
) -> tuple[PoolRelativeNormalizationEvidence, ...]:
    evidence_by_scope: dict[PreferenceScope, PoolRelativeNormalizationEvidence] = {}
    for preference in preferences:
        if preference.scope in evidence_by_scope:
            continue
        policy = ranking_policy_set.policy_for(preference.scope)
        normalizer = normalizer_registry.get(preference.scope)
        if normalizer.feature_key != policy.feature_key:
            raise DomainInvariantViolation("Ranking policy and normalizer feature keys must match")
        evidence_by_scope[preference.scope] = normalizer.build_evidence(candidates=candidates, feature_set=feature_set)
    return tuple(evidence_by_scope[scope] for scope in sorted(evidence_by_scope, key=lambda item: item.value))


def _entry_for_candidate(
    *,
    candidate: OfferBackedItineraryCandidate,
    preferences: tuple[SoftPreference, ...],
    feature_set: DerivedFeatureSet,
    ranking_policy_set: RankingPolicySet,
    normalizer_registry: NormalizerRegistry,
    normalization: tuple[PoolRelativeNormalizationEvidence, ...],
) -> RankingEntry:
    contributions = tuple(
        _contribution_for_preference(
            preference=preference,
            candidate=candidate,
            feature_set=feature_set,
            ranking_policy_set=ranking_policy_set,
            normalizer_registry=normalizer_registry,
            normalization=normalization,
        )
        for preference in preferences
    )
    total_weight = sum(
        (ranking_policy_set.resolve_weight(preference.importance) for preference in preferences),
        Decimal(0),
    )
    evaluated_contributions = tuple(
        contribution
        for contribution in contributions
        if contribution.status is PreferenceContributionStatus.EVALUATED
        and contribution.weighted_contribution.is_known
    )
    evaluated_weight = sum((contribution.resolved_weight for contribution in evaluated_contributions), Decimal(0))
    weighted_sum = sum((contribution.weighted_contribution.value for contribution in evaluated_contributions), Decimal(0))
    aggregate_score = NEUTRAL_SCORE if evaluated_weight == Decimal(0) else weighted_sum / evaluated_weight
    coverage = PreferenceCoverage(
        evaluated_preference_count=len(evaluated_contributions),
        total_applicable_preference_count=len(preferences),
        evaluated_weight=evaluated_weight,
        total_applicable_weight=total_weight,
        missing_preference_evidence=tuple(
            contribution.preference_id
            for contribution in contributions
            if contribution.status is PreferenceContributionStatus.MISSING_EVIDENCE
        ),
    )
    return RankingEntry(
        candidate=candidate,
        rank_position=0,
        aggregate_score=aggregate_score,
        preference_contributions=contributions,
        coverage=coverage,
        tie_break_evidence=TieBreakEvidence(
            aggregate_score=aggregate_score,
            evaluated_preference_coverage=coverage.evaluated_preference_coverage,
            canonical_candidate_identity=_canonical_candidate_identity(candidate),
        ),
    )


def _contribution_for_preference(
    *,
    preference: SoftPreference,
    candidate: OfferBackedItineraryCandidate,
    feature_set: DerivedFeatureSet,
    ranking_policy_set: RankingPolicySet,
    normalizer_registry: NormalizerRegistry,
    normalization: tuple[PoolRelativeNormalizationEvidence, ...],
) -> PreferenceContribution:
    policy = ranking_policy_set.policy_for(preference.scope)
    normalizer = normalizer_registry.get(preference.scope)
    contribution = normalizer.normalize(
        preference=preference,
        candidate=candidate,
        feature_set=feature_set,
        policy=policy,
        evidence=_normalization_for_scope(normalization, preference.scope),
    )
    if contribution.status is PreferenceContributionStatus.MISSING_EVIDENCE:
        return contribution
    weight = ranking_policy_set.resolve_weight(preference.importance)
    normalized_value = contribution.normalized_value.value.value
    return PreferenceContribution(
        preference_id=contribution.preference_id,
        preference_scope=contribution.preference_scope,
        feature_key=contribution.feature_key,
        candidate=contribution.candidate,
        status=contribution.status,
        raw_feature_value=contribution.raw_feature_value,
        normalized_value=contribution.normalized_value,
        resolved_weight=weight,
        weighted_contribution=DomainValue.known(normalized_value * weight),
        missing_reason=None,
    )


def _normalization_for_scope(
    normalization: tuple[PoolRelativeNormalizationEvidence, ...],
    preference_scope: PreferenceScope,
) -> PoolRelativeNormalizationEvidence:
    for evidence in normalization:
        if evidence.preference_scope is preference_scope:
            return evidence
    raise DomainInvariantViolation(f"Missing normalization evidence for {preference_scope.value}")


def _feature_value_for(
    feature_set: DerivedFeatureSet,
    candidate: OfferBackedItineraryCandidate,
    feature_key: FeatureKey,
) -> FeatureValue | None:
    matches = tuple(
        value
        for value in feature_set.values
        if value.candidate == candidate and value.feature_key == feature_key
    )
    if len(matches) > 1:
        raise DomainInvariantViolation("Ranking FeatureValue lookup found duplicate values")
    return matches[0] if len(matches) == 1 else None


def _known_decimal_feature(
    feature_set: DerivedFeatureSet,
    candidate: OfferBackedItineraryCandidate,
    feature_key: FeatureKey,
    value_type: FeatureValueType,
) -> Decimal | None:
    feature_value = _feature_value_for(feature_set, candidate, feature_key)
    if _missing_reason(feature_value, value_type) is not None:
        return None
    if feature_value is None:
        raise DomainInvariantViolation("Known ranking feature requires FeatureValue evidence")
    return _feature_scalar_to_decimal(feature_value.value.value, value_type)


def _missing_reason(feature_value: FeatureValue | None, value_type: FeatureValueType) -> str | None:
    if feature_value is None:
        return "feature value is absent"
    if feature_value.value_type is not value_type:
        raise DomainInvariantViolation("Ranking feature value has wrong feature value type")
    if feature_value.value.state is not ValueState.KNOWN:
        return f"feature value is {feature_value.value.state.value}"
    return None


def _feature_scalar_to_decimal(value: FeatureScalar, value_type: FeatureValueType) -> Decimal:
    if value_type is FeatureValueType.MONEY:
        if not isinstance(value, Money):
            raise DomainInvariantViolation("MONEY ranking feature requires Money value")
        return value.amount
    if value_type is FeatureValueType.INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            raise DomainInvariantViolation("INTEGER ranking feature requires int value")
        return Decimal(value)
    raise DomainInvariantViolation(f"Unsupported ranking feature value type: {value_type.value}")


def _normalize_decimal(
    value: Decimal,
    min_value: Decimal,
    max_value: Decimal,
    direction: RankingPreferenceDirection,
) -> Decimal:
    if min_value == max_value:
        return DEGENERATE_NORMALIZED_VALUE
    if direction is RankingPreferenceDirection.LOWER_IS_BETTER:
        return (max_value - value) / (max_value - min_value)
    if direction is RankingPreferenceDirection.HIGHER_IS_BETTER:
        return (value - min_value) / (max_value - min_value)
    raise DomainInvariantViolation(f"Unsupported ranking direction: {direction.value}")


def _with_rank(entry: RankingEntry, rank_position: int) -> RankingEntry:
    return RankingEntry(
        candidate=entry.candidate,
        rank_position=rank_position,
        aggregate_score=entry.aggregate_score,
        preference_contributions=entry.preference_contributions,
        coverage=entry.coverage,
        tie_break_evidence=entry.tie_break_evidence,
    )


def _canonical_candidate_identity(candidate: OfferBackedItineraryCandidate) -> str:
    return f"{candidate.offer_id.value}|{candidate.itinerary_id.value}"
