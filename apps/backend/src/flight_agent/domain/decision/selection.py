"""M5 minimal and M6 complete recommendation selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.decision.evaluation import OfferBackedItineraryCandidate
from flight_agent.domain.decision.features import (
    STOP_COUNT,
    TOTAL_PRICE,
    DerivedFeatureSet,
    FeatureValue,
    FeatureValueType,
)
from flight_agent.domain.decision.filtering import CompleteFilterResult
from flight_agent.domain.decision.identity import RecommendationRunId
from flight_agent.domain.decision.policy import DecisionPolicyVersion
from flight_agent.domain.decision.ranking import (
    CompleteRankingResult,
    RankingEntry,
    RankingResult,
    RankingViewKind,
)
from flight_agent.domain.flights import (
    CandidateSnapshot,
    CandidateSnapshotId,
    Money,
)
from flight_agent.domain.requirements import (
    PreferenceId,
    PreferenceScope,
    RequirementId,
    RequirementState,
)
from flight_agent.domain.shared import (
    DomainInstant,
    DomainInvariantViolation,
    RequirementVersion,
    SnapshotVersion,
    ValueState,
)
from flight_agent.domain.workflow import (
    CandidateComparison,
    EvidenceRef,
    EvidenceSource,
    ExecutionId,
    RecommendationItem,
    RecommendationResult,
    RecommendationResultId,
    RecommendationResultStatus,
    RecommendationRole,
    RecommendationRoleAssignment,
)


@dataclass(frozen=True)
class RecommendationSelector:
    def select_best_overall(
        self,
        *,
        ranking_result: RankingResult,
        snapshot: CandidateSnapshot,
        recommendation_result_id: RecommendationResultId,
        execution_id: ExecutionId,
        generated_at: DomainInstant,
    ) -> RecommendationResult:
        if len(ranking_result.ranked_candidates) == 0:
            return RecommendationResult(
                recommendation_result_id=recommendation_result_id,
                status=RecommendationResultStatus.NO_MATCH,
                execution_id=execution_id,
                based_on_requirement_version=snapshot.created_from_requirement_version,
                snapshot_id=snapshot.snapshot_id,
                snapshot_version=snapshot.version,
                generated_at=generated_at,
            )
        selected = ranking_result.ranked_candidates[0]
        return RecommendationResult(
            recommendation_result_id=recommendation_result_id,
            status=RecommendationResultStatus.EXACT_MATCH,
            execution_id=execution_id,
            based_on_requirement_version=snapshot.created_from_requirement_version,
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.version,
            generated_at=generated_at,
            items=(
                RecommendationItem(
                    itinerary_id=selected.itinerary_id,
                    primary_offer_id=selected.offer_id,
                    roles=(RecommendationRole.BEST_OVERALL,),
                    evidence=(
                        *selected.evidence,
                        EvidenceRef(
                            EvidenceSource.OFFER,
                            selected.offer_id,
                            note="Selected from rank 1 lower-price result",
                        ),
                    ),
                ),
            ),
        )


class SelectionCandidateSource(str, Enum):
    BEST_OVERALL_ANCHOR = "BEST_OVERALL_ANCHOR"
    ROLE_COVERAGE = "ROLE_COVERAGE"


@dataclass(frozen=True, init=False)
class RecommendationPolicy:
    policy_version: DecisionPolicyVersion
    target_count: int
    max_count: int
    enabled_roles: tuple[RecommendationRole, ...]
    redundancy_predicate_version: DecisionPolicyVersion
    selection_ordering_policy: tuple[str, ...]

    def __init__(
        self,
        policy_version: DecisionPolicyVersion,
        target_count: int,
        max_count: int,
        enabled_roles: tuple[RecommendationRole, ...],
        redundancy_predicate_version: DecisionPolicyVersion,
        selection_ordering_policy: tuple[str, ...] = (
            "best_overall_anchor",
            "role_coverage",
            "canonical_candidate_identity",
        ),
    ) -> None:
        roles_tuple = tuple(enabled_roles)
        if target_count < 1 or max_count < 1:
            raise DomainInvariantViolation("RecommendationPolicy counts must be positive")
        if target_count > max_count:
            raise DomainInvariantViolation("RecommendationPolicy target_count must not exceed max_count")
        if len(frozenset(roles_tuple)) != len(roles_tuple):
            raise DomainInvariantViolation("RecommendationPolicy enabled roles must be unique")
        object.__setattr__(self, "policy_version", policy_version)
        object.__setattr__(self, "target_count", target_count)
        object.__setattr__(self, "max_count", max_count)
        object.__setattr__(self, "enabled_roles", roles_tuple)
        object.__setattr__(self, "redundancy_predicate_version", redundancy_predicate_version)
        object.__setattr__(self, "selection_ordering_policy", tuple(selection_ordering_policy))


@dataclass(frozen=True)
class RecommendationRun:
    run_id: RecommendationRunId
    requirement_id: RequirementId
    requirement_version: RequirementVersion
    snapshot_id: CandidateSnapshotId
    snapshot_version: SnapshotVersion
    filter_result_id: str
    ranking_result_id: str
    derived_feature_set_id: str
    recommendation_policy_version: DecisionPolicyVersion
    target_count: int
    max_count: int
    enabled_roles: tuple[RecommendationRole, ...]
    selected_count: int


@dataclass(frozen=True)
class RoleCandidate:
    candidate: OfferBackedItineraryCandidate
    role_assignment: RecommendationRoleAssignment
    source_rank: int
    source: SelectionCandidateSource
    trade_off_evidence: tuple[str, ...] = ()


class CompleteRecommendationSelector:
    def select(
        self,
        *,
        recommendation_result_id: RecommendationResultId,
        recommendation_run_id: RecommendationRunId,
        execution_id: ExecutionId,
        generated_at: DomainInstant,
        requirement: RequirementState,
        snapshot: CandidateSnapshot,
        feature_set: DerivedFeatureSet,
        filter_result: CompleteFilterResult,
        ranking_result: CompleteRankingResult,
        recommendation_policy: RecommendationPolicy,
    ) -> tuple[RecommendationRun, RecommendationResult]:
        _validate_complete_inputs(requirement, snapshot, feature_set, filter_result, ranking_result)
        role_candidates = _qualified_role_candidates(
            requirement=requirement,
            feature_set=feature_set,
            filter_result=filter_result,
            ranking_result=ranking_result,
            recommendation_policy=recommendation_policy,
        )
        selected_role_candidates = _select_role_candidates(role_candidates, recommendation_policy)
        items = _merge_role_candidates(selected_role_candidates)
        comparisons = _candidate_comparisons(items, feature_set)
        result = RecommendationResult(
            recommendation_result_id=recommendation_result_id,
            status=RecommendationResultStatus.NO_MATCH
            if len(items) == 0
            else RecommendationResultStatus.EXACT_MATCH,
            execution_id=execution_id,
            based_on_requirement_version=requirement.version,
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.version,
            generated_at=generated_at,
            items=items,
            requirement_id=requirement.requirement_id,
            recommendation_run_id=recommendation_run_id.value,
            filter_result_id=filter_result.filter_result_id.value,
            ranking_result_id=ranking_result.ranking_result_id.value,
            derived_feature_set_id=feature_set.feature_set_id.value,
            recommendation_policy_version=recommendation_policy.policy_version.value,
            candidate_comparisons=comparisons,
            target_count=recommendation_policy.target_count,
            max_count=recommendation_policy.max_count,
        )
        run = RecommendationRun(
            run_id=recommendation_run_id,
            requirement_id=requirement.requirement_id,
            requirement_version=requirement.version,
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.version,
            filter_result_id=filter_result.filter_result_id.value,
            ranking_result_id=ranking_result.ranking_result_id.value,
            derived_feature_set_id=feature_set.feature_set_id.value,
            recommendation_policy_version=recommendation_policy.policy_version,
            target_count=recommendation_policy.target_count,
            max_count=recommendation_policy.max_count,
            enabled_roles=recommendation_policy.enabled_roles,
            selected_count=len(items),
        )
        return run, result


def m6_default_recommendation_policy() -> RecommendationPolicy:
    return RecommendationPolicy(
        policy_version=DecisionPolicyVersion("recommendation-policy-v1"),
        target_count=2,
        max_count=3,
        enabled_roles=(RecommendationRole.BEST_OVERALL, RecommendationRole.CHEAPEST),
        redundancy_predicate_version=DecisionPolicyVersion("canonical-candidate-dedup-v1"),
    )


def m6_default_complete_recommendation_selector() -> CompleteRecommendationSelector:
    return CompleteRecommendationSelector()


def _validate_complete_inputs(
    requirement: RequirementState,
    snapshot: CandidateSnapshot,
    feature_set: DerivedFeatureSet,
    filter_result: CompleteFilterResult,
    ranking_result: CompleteRankingResult,
) -> None:
    if ranking_result.ranking_view_kind is not RankingViewKind.QUALIFIED:
        raise DomainInvariantViolation("Main RecommendationSelector requires QUALIFIED RankingResult")
    if feature_set.input_lineage.requirement_id != requirement.requirement_id:
        raise DomainInvariantViolation("Recommendation feature set requirement lineage mismatch")
    if feature_set.input_lineage.requirement_version != requirement.version:
        raise DomainInvariantViolation("Recommendation feature set requirement version mismatch")
    if feature_set.input_lineage.snapshot_id != snapshot.snapshot_id:
        raise DomainInvariantViolation("Recommendation feature set snapshot lineage mismatch")
    if feature_set.input_lineage.snapshot_version != snapshot.version:
        raise DomainInvariantViolation("Recommendation feature set snapshot version mismatch")
    if filter_result.requirement_id != requirement.requirement_id or filter_result.requirement_version != requirement.version:
        raise DomainInvariantViolation("Recommendation FilterResult requirement lineage mismatch")
    if filter_result.snapshot_id != snapshot.snapshot_id or filter_result.snapshot_version != snapshot.version:
        raise DomainInvariantViolation("Recommendation FilterResult snapshot lineage mismatch")
    if filter_result.derived_feature_set_id != feature_set.feature_set_id.value:
        raise DomainInvariantViolation("Recommendation FilterResult feature lineage mismatch")
    if ranking_result.requirement_id != requirement.requirement_id or ranking_result.requirement_version != requirement.version:
        raise DomainInvariantViolation("Recommendation RankingResult requirement lineage mismatch")
    if ranking_result.snapshot_id != snapshot.snapshot_id or ranking_result.snapshot_version != snapshot.version:
        raise DomainInvariantViolation("Recommendation RankingResult snapshot lineage mismatch")
    if ranking_result.filter_result_id != filter_result.filter_result_id.value:
        raise DomainInvariantViolation("Recommendation RankingResult filter lineage mismatch")
    if ranking_result.derived_feature_set_id != feature_set.feature_set_id.value:
        raise DomainInvariantViolation("Recommendation RankingResult feature lineage mismatch")
    qualified = frozenset(filter_result.qualified_candidates)
    if any(entry.candidate not in qualified for entry in ranking_result.entries):
        raise DomainInvariantViolation("Recommendation RankingResult contains non-qualified candidate")


def _qualified_role_candidates(
    *,
    requirement: RequirementState,
    feature_set: DerivedFeatureSet,
    filter_result: CompleteFilterResult,
    ranking_result: CompleteRankingResult,
    recommendation_policy: RecommendationPolicy,
) -> tuple[RoleCandidate, ...]:
    if len(ranking_result.entries) == 0:
        return ()
    qualified = frozenset(filter_result.qualified_candidates)
    role_candidates = [_best_overall_anchor(ranking_result.entries[0])]
    if RecommendationRole.CHEAPEST in recommendation_policy.enabled_roles and _has_price_preference(requirement):
        cheapest = _cheapest_role_candidate(ranking_result.entries, feature_set, qualified)
        if cheapest is not None:
            role_candidates.append(cheapest)
    return tuple(role_candidates)


def _best_overall_anchor(entry: RankingEntry) -> RoleCandidate:
    return RoleCandidate(
        candidate=entry.candidate,
        role_assignment=RecommendationRoleAssignment(
            RecommendationRole.BEST_OVERALL,
            evidence=(
                EvidenceRef(EvidenceSource.OFFER, entry.candidate.offer_id, note="Selected from qualified rank 1"),
            ),
        ),
        source_rank=entry.rank_position,
        source=SelectionCandidateSource.BEST_OVERALL_ANCHOR,
    )


def _cheapest_role_candidate(
    entries: tuple[RankingEntry, ...],
    feature_set: DerivedFeatureSet,
    qualified_candidates: frozenset[OfferBackedItineraryCandidate],
) -> RoleCandidate | None:
    comparable = tuple(
        (entry, value.value.value)
        for entry in entries
        for value in (_feature_value(feature_set, entry.candidate, TOTAL_PRICE),)
        if entry.candidate in qualified_candidates
        and value is not None
        and value.value_type is FeatureValueType.MONEY
        and value.value.state is ValueState.KNOWN
        and isinstance(value.value.value, Money)
    )
    if len(comparable) == 0:
        return None
    currencies = frozenset(money.currency for _, money in comparable)
    if len(currencies) != 1:
        raise DomainInvariantViolation("CHEAPEST role requires comparable Money currency")
    entry, money = min(
        comparable,
        key=lambda item: (item[1].amount, _canonical_candidate_identity(item[0].candidate)),
    )
    return RoleCandidate(
        candidate=entry.candidate,
        role_assignment=RecommendationRoleAssignment(
            RecommendationRole.CHEAPEST,
            preference_id=_price_preference_id(entries, feature_set),
            evidence=(
                EvidenceRef(EvidenceSource.OFFER, entry.candidate.offer_id, note=f"Lowest known {money.currency} total price"),
            ),
        ),
        source_rank=entry.rank_position,
        source=SelectionCandidateSource.ROLE_COVERAGE,
        trade_off_evidence=("lowest-known-total-price",),
    )


def _price_preference_id(entries: tuple[RankingEntry, ...], feature_set: DerivedFeatureSet) -> PreferenceId | None:
    del feature_set
    for entry in entries:
        for contribution in entry.preference_contributions:
            if contribution.preference_scope is PreferenceScope.PRICE:
                return contribution.preference_id
    return None


def _has_price_preference(requirement: RequirementState) -> bool:
    return any(preference.scope is PreferenceScope.PRICE for preference in requirement.preferences)


def _select_role_candidates(
    role_candidates: tuple[RoleCandidate, ...],
    policy: RecommendationPolicy,
) -> tuple[RoleCandidate, ...]:
    if len(role_candidates) == 0 or policy.max_count == 0:
        return ()
    by_candidate: dict[OfferBackedItineraryCandidate, list[RoleCandidate]] = {}
    for role_candidate in role_candidates:
        by_candidate.setdefault(role_candidate.candidate, []).append(role_candidate)
    anchor = role_candidates[0]
    selected_candidates = [anchor.candidate]
    candidates_by_priority = sorted(
        (
            candidate_roles[0]
            for candidate, candidate_roles in by_candidate.items()
            if candidate != anchor.candidate
        ),
        key=lambda role_candidate: (
            0 if role_candidate.source is SelectionCandidateSource.ROLE_COVERAGE else 1,
            _canonical_candidate_identity(role_candidate.candidate),
        ),
    )
    for role_candidate in candidates_by_priority:
        if len(selected_candidates) >= min(policy.target_count, policy.max_count):
            break
        selected_candidates.append(role_candidate.candidate)
    return tuple(
        role_candidate
        for candidate in selected_candidates
        for role_candidate in by_candidate[candidate]
    )


def _merge_role_candidates(role_candidates: tuple[RoleCandidate, ...]) -> tuple[RecommendationItem, ...]:
    if len(role_candidates) == 0:
        return ()
    by_candidate: dict[OfferBackedItineraryCandidate, list[RoleCandidate]] = {}
    for role_candidate in role_candidates:
        by_candidate.setdefault(role_candidate.candidate, []).append(role_candidate)
    items = []
    for order, candidate in enumerate(
        sorted(by_candidate, key=lambda item: (0 if item == role_candidates[0].candidate else 1, _canonical_candidate_identity(item))),
        start=1,
    ):
        roles = tuple(
            sorted(
                {role_candidate.role_assignment.role for role_candidate in by_candidate[candidate]},
                key=_role_order,
            )
        )
        assignments = tuple(
            sorted(
                (role_candidate.role_assignment for role_candidate in by_candidate[candidate]),
                key=lambda assignment: _role_order(assignment.role),
            )
        )
        source_rank = min(role_candidate.source_rank for role_candidate in by_candidate[candidate])
        evidence = tuple(ref for assignment in assignments for ref in assignment.evidence)
        trade_off_evidence = tuple(
            evidence
            for role_candidate in by_candidate[candidate]
            for evidence in role_candidate.trade_off_evidence
        )
        items.append(
            RecommendationItem(
                itinerary_id=candidate.itinerary_id,
                primary_offer_id=candidate.offer_id,
                roles=roles,
                evidence=evidence,
                source_rank=source_rank,
                selection_order=order,
                role_assignments=assignments,
                trade_off_evidence=trade_off_evidence,
            )
        )
    return tuple(items)


def _candidate_comparisons(
    items: tuple[RecommendationItem, ...],
    feature_set: DerivedFeatureSet,
) -> tuple[CandidateComparison, ...]:
    if len(items) < 2:
        return ()
    anchor = items[0]
    comparisons = []
    for item in items[1:]:
        left = OfferBackedItineraryCandidate(anchor.primary_offer_id, anchor.itinerary_id)
        right = OfferBackedItineraryCandidate(item.primary_offer_id, item.itinerary_id)
        comparisons.append(
            CandidateComparison(
                left_offer_id=anchor.primary_offer_id,
                right_offer_id=item.primary_offer_id,
                price_difference=_price_difference(feature_set, left, right),
                stop_count_difference=_stop_count_difference(feature_set, left, right),
                source_rank_relation=f"{anchor.source_rank}->{item.source_rank}",
                evidence=(
                    EvidenceRef(EvidenceSource.OFFER, anchor.primary_offer_id),
                    EvidenceRef(EvidenceSource.OFFER, item.primary_offer_id),
                ),
            )
        )
    return tuple(comparisons)


def _price_difference(
    feature_set: DerivedFeatureSet,
    left: OfferBackedItineraryCandidate,
    right: OfferBackedItineraryCandidate,
) -> str | None:
    left_price = _known_money(feature_set, left)
    right_price = _known_money(feature_set, right)
    if left_price is None or right_price is None:
        return None
    if left_price.currency != right_price.currency:
        return None
    return f"{right_price.amount - left_price.amount} {left_price.currency}"


def _stop_count_difference(
    feature_set: DerivedFeatureSet,
    left: OfferBackedItineraryCandidate,
    right: OfferBackedItineraryCandidate,
) -> int | None:
    left_stops = _known_stop_count(feature_set, left)
    right_stops = _known_stop_count(feature_set, right)
    if left_stops is None or right_stops is None:
        return None
    return right_stops - left_stops


def _known_money(feature_set: DerivedFeatureSet, candidate: OfferBackedItineraryCandidate) -> Money | None:
    value = _feature_value(feature_set, candidate, TOTAL_PRICE)
    if value is None or value.value_type is not FeatureValueType.MONEY or value.value.state is not ValueState.KNOWN:
        return None
    if not isinstance(value.value.value, Money):
        raise DomainInvariantViolation("TOTAL_PRICE recommendation evidence requires Money")
    return value.value.value


def _known_stop_count(feature_set: DerivedFeatureSet, candidate: OfferBackedItineraryCandidate) -> int | None:
    value = _feature_value(feature_set, candidate, STOP_COUNT)
    if value is None or value.value_type is not FeatureValueType.INTEGER or value.value.state is not ValueState.KNOWN:
        return None
    if not isinstance(value.value.value, int) or isinstance(value.value.value, bool):
        raise DomainInvariantViolation("STOP_COUNT recommendation evidence requires int")
    return value.value.value


def _feature_value(
    feature_set: DerivedFeatureSet,
    candidate: OfferBackedItineraryCandidate,
    feature_key: object,
) -> FeatureValue | None:
    matches = tuple(
        value
        for value in feature_set.values
        if value.candidate == candidate and value.feature_key == feature_key
    )
    if len(matches) > 1:
        raise DomainInvariantViolation("Recommendation feature lookup found duplicate values")
    return matches[0] if matches else None


def _role_order(role: RecommendationRole) -> int:
    order = {
        RecommendationRole.BEST_OVERALL: 0,
        RecommendationRole.CHEAPEST: 1,
        RecommendationRole.EARLIEST_ARRIVAL: 2,
        RecommendationRole.BEST_DEPARTURE_TIME: 3,
        RecommendationRole.BEST_AIRPORT_MATCH: 4,
        RecommendationRole.FALLBACK: 5,
    }
    return order[role]


def _canonical_candidate_identity(candidate: OfferBackedItineraryCandidate) -> str:
    return f"{candidate.offer_id.value}|{candidate.itinerary_id.value}"
