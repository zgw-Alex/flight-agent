"""M7-U2 impact compatibility and data-action decision foundation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.decision import (
    FeatureDefinitionRegistry,
    FeatureKey,
    RankingPolicySet,
)
from flight_agent.domain.flights import CandidateSnapshot
from flight_agent.domain.impact.semantic_diff import (
    RequirementDependencyKey,
    RequirementSemanticChangeKind,
    RequirementSemanticDiff,
    SemanticSubjectType,
)
from flight_agent.domain.shared import (
    DomainId,
    DomainInvariantViolation,
    FreshnessState,
    RequirementVersion,
)


class ImpactCompatibility(str, Enum):
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DataAction(str, Enum):
    REUSE = "REUSE"
    RECOMPUTE = "RECOMPUTE"
    REFRESH = "REFRESH"
    SEARCH = "SEARCH"
    ENRICH = "ENRICH"
    REBUILD_FROM_RAW = "REBUILD_FROM_RAW"
    NONE = "NONE"


class ImpactAssetKind(str, Enum):
    SNAPSHOT = "SNAPSHOT"
    DERIVED_FEATURE_SET = "DERIVED_FEATURE_SET"
    FILTER_RESULT = "FILTER_RESULT"
    RANKING_RESULT = "RANKING_RESULT"
    RECOMMENDATION_RESULT = "RECOMMENDATION_RESULT"
    RELAXATION_RESULT = "RELAXATION_RESULT"
    EXPLANATION = "EXPLANATION"


class ImpactReasonCode(str, Enum):
    NO_SEMANTIC_CHANGE = "NO_SEMANTIC_CHANGE"
    SNAPSHOT_COMPATIBLE = "SNAPSHOT_COMPATIBLE"
    SNAPSHOT_COVERAGE_INSUFFICIENT = "SNAPSHOT_COVERAGE_INSUFFICIENT"
    SNAPSHOT_COVERAGE_UNKNOWN = "SNAPSHOT_COVERAGE_UNKNOWN"
    SNAPSHOT_STRUCTURAL_STALE = "SNAPSHOT_STRUCTURAL_STALE"
    SNAPSHOT_OFFER_STALE = "SNAPSHOT_OFFER_STALE"
    SNAPSHOT_PIPELINE_INCOMPATIBLE_RAW_USABLE = "SNAPSHOT_PIPELINE_INCOMPATIBLE_RAW_USABLE"
    SNAPSHOT_PIPELINE_COMPATIBILITY_UNKNOWN = "SNAPSHOT_PIPELINE_COMPATIBILITY_UNKNOWN"
    SNAPSHOT_MISSING_EXTERNAL_FACTS = "SNAPSHOT_MISSING_EXTERNAL_FACTS"
    FEATURE_DEPENDENCY_CHANGED = "FEATURE_DEPENDENCY_CHANGED"
    FEATURE_POLICY_OR_REFERENCE_CHANGED = "FEATURE_POLICY_OR_REFERENCE_CHANGED"
    FILTER_HARD_CONSTRAINT_CHANGED = "FILTER_HARD_CONSTRAINT_CHANGED"
    FILTER_COMPATIBLE_WITH_SOFT_CHANGE = "FILTER_COMPATIBLE_WITH_SOFT_CHANGE"
    RANKING_DEPENDENCY_CHANGED = "RANKING_DEPENDENCY_CHANGED"
    RANKING_POLICY_CHANGED = "RANKING_POLICY_CHANGED"
    RECOMMENDATION_UPSTREAM_CHANGED = "RECOMMENDATION_UPSTREAM_CHANGED"
    RECOMMENDATION_POLICY_CHANGED = "RECOMMENDATION_POLICY_CHANGED"
    RELAXATION_UPSTREAM_CHANGED = "RELAXATION_UPSTREAM_CHANGED"
    EXPLANATION_DOWNSTREAM_OF_RECOMMENDATION = "EXPLANATION_DOWNSTREAM_OF_RECOMMENDATION"
    ARTIFACT_COMPATIBILITY_UNKNOWN = "ARTIFACT_COMPATIBILITY_UNKNOWN"


@dataclass(frozen=True, init=False)
class SnapshotCompatibilityFacts:
    snapshot: CandidateSnapshot
    required_scope_covered: bool | None
    pipeline_compatible: bool | None
    raw_evidence_usable: bool
    missing_external_fact_keys: tuple[str, ...]

    def __init__(
        self,
        *,
        snapshot: CandidateSnapshot,
        required_scope_covered: bool | None,
        pipeline_compatible: bool | None = True,
        raw_evidence_usable: bool = False,
        missing_external_fact_keys: tuple[str, ...] = (),
    ) -> None:
        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "required_scope_covered", required_scope_covered)
        object.__setattr__(self, "pipeline_compatible", pipeline_compatible)
        object.__setattr__(self, "raw_evidence_usable", raw_evidence_usable)
        object.__setattr__(
            self,
            "missing_external_fact_keys",
            tuple(sorted(missing_external_fact_keys)),
        )


@dataclass(frozen=True, init=False)
class M6ArtifactFacts:
    feature_registry: FeatureDefinitionRegistry
    ranking_policy_set: RankingPolicySet
    active_feature_keys: tuple[FeatureKey, ...]
    feature_policy_compatible: bool | None
    feature_reference_compatible: bool | None
    filter_policy_compatible: bool | None
    ranking_policy_compatible: bool | None
    recommendation_policy_compatible: bool | None
    relaxation_policy_compatible: bool | None

    def __init__(
        self,
        *,
        feature_registry: FeatureDefinitionRegistry,
        ranking_policy_set: RankingPolicySet,
        active_feature_keys: tuple[FeatureKey, ...] = (),
        feature_policy_compatible: bool | None = True,
        feature_reference_compatible: bool | None = True,
        filter_policy_compatible: bool | None = True,
        ranking_policy_compatible: bool | None = True,
        recommendation_policy_compatible: bool | None = True,
        relaxation_policy_compatible: bool | None = True,
    ) -> None:
        object.__setattr__(self, "feature_registry", feature_registry)
        object.__setattr__(self, "ranking_policy_set", ranking_policy_set)
        object.__setattr__(
            self,
            "active_feature_keys",
            tuple(sorted(active_feature_keys, key=lambda key: key.value)),
        )
        object.__setattr__(self, "feature_policy_compatible", feature_policy_compatible)
        object.__setattr__(self, "feature_reference_compatible", feature_reference_compatible)
        object.__setattr__(self, "filter_policy_compatible", filter_policy_compatible)
        object.__setattr__(self, "ranking_policy_compatible", ranking_policy_compatible)
        object.__setattr__(self, "recommendation_policy_compatible", recommendation_policy_compatible)
        object.__setattr__(self, "relaxation_policy_compatible", relaxation_policy_compatible)


@dataclass(frozen=True)
class ImpactResolverInput:
    semantic_diff: RequirementSemanticDiff
    snapshot: SnapshotCompatibilityFacts
    artifacts: M6ArtifactFacts


@dataclass(frozen=True, init=False)
class AssetImpact:
    asset_kind: ImpactAssetKind
    compatibility: ImpactCompatibility
    required_action: DataAction
    reason_codes: tuple[ImpactReasonCode, ...]
    affected_dependency_keys: tuple[RequirementDependencyKey, ...]

    def __init__(
        self,
        *,
        asset_kind: ImpactAssetKind,
        compatibility: ImpactCompatibility,
        required_action: DataAction,
        reason_codes: tuple[ImpactReasonCode, ...],
        affected_dependency_keys: tuple[RequirementDependencyKey, ...] = (),
    ) -> None:
        if len(reason_codes) == 0:
            raise DomainInvariantViolation("AssetImpact requires reason evidence")
        object.__setattr__(self, "asset_kind", asset_kind)
        object.__setattr__(self, "compatibility", compatibility)
        object.__setattr__(self, "required_action", required_action)
        object.__setattr__(
            self,
            "reason_codes",
            tuple(sorted(frozenset(reason_codes), key=lambda item: item.value)),
        )
        object.__setattr__(
            self,
            "affected_dependency_keys",
            tuple(
                RequirementDependencyKey(key)
                for key in sorted({item.value for item in affected_dependency_keys})
            ),
        )


@dataclass(frozen=True, init=False)
class ImpactDecision:
    impact_decision_id: DomainId
    semantic_diff_id: DomainId
    requirement_id: DomainId
    from_version: RequirementVersion
    to_version: RequirementVersion
    primary_data_action: DataAction
    asset_impacts: tuple[AssetImpact, ...]

    def __init__(
        self,
        *,
        impact_decision_id: DomainId,
        semantic_diff: RequirementSemanticDiff,
        primary_data_action: DataAction,
        asset_impacts: tuple[AssetImpact, ...],
    ) -> None:
        impacts_tuple = tuple(sorted(asset_impacts, key=lambda impact: impact.asset_kind.value))
        if len({impact.asset_kind for impact in impacts_tuple}) != len(impacts_tuple):
            raise DomainInvariantViolation("ImpactDecision requires one impact per asset kind")
        object.__setattr__(self, "impact_decision_id", impact_decision_id)
        object.__setattr__(self, "semantic_diff_id", semantic_diff.diff_id)
        object.__setattr__(self, "requirement_id", semantic_diff.requirement_id)
        object.__setattr__(self, "from_version", semantic_diff.from_version)
        object.__setattr__(self, "to_version", semantic_diff.to_version)
        object.__setattr__(self, "primary_data_action", primary_data_action)
        object.__setattr__(self, "asset_impacts", impacts_tuple)

    def impact_for(self, asset_kind: ImpactAssetKind) -> AssetImpact:
        for impact in self.asset_impacts:
            if impact.asset_kind is asset_kind:
                return impact
        raise DomainInvariantViolation(f"Missing asset impact: {asset_kind.value}")


class ImpactResolver:
    """Resolves M7-U2 asset compatibility without executing a plan."""

    def resolve(self, resolver_input: ImpactResolverInput) -> ImpactDecision:
        diff = resolver_input.semantic_diff
        snapshot_impact = _snapshot_impact(resolver_input)
        feature_impact = _derived_feature_impact(resolver_input)
        filter_impact = _filter_impact(resolver_input)
        ranking_impact = _ranking_impact(resolver_input, filter_impact, feature_impact)
        recommendation_impact = _recommendation_impact(
            resolver_input,
            filter_impact,
            ranking_impact,
            feature_impact,
        )
        relaxation_impact = _relaxation_impact(resolver_input, filter_impact, feature_impact)
        explanation_impact = _explanation_impact(recommendation_impact)
        impacts = (
            snapshot_impact,
            feature_impact,
            filter_impact,
            ranking_impact,
            recommendation_impact,
            relaxation_impact,
            explanation_impact,
        )
        return ImpactDecision(
            impact_decision_id=DomainId(f"impact-decision:{diff.diff_id.value}"),
            semantic_diff=diff,
            primary_data_action=snapshot_impact.required_action,
            asset_impacts=impacts,
        )


def _snapshot_impact(resolver_input: ImpactResolverInput) -> AssetImpact:
    diff = resolver_input.semantic_diff
    facts = resolver_input.snapshot
    if facts.pipeline_compatible is False and facts.raw_evidence_usable:
        return _impact(
            ImpactAssetKind.SNAPSHOT,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.REBUILD_FROM_RAW,
            (ImpactReasonCode.SNAPSHOT_PIPELINE_INCOMPATIBLE_RAW_USABLE,),
            diff.affected_dependency_keys,
        )
    if facts.pipeline_compatible is None:
        return _impact(
            ImpactAssetKind.SNAPSHOT,
            ImpactCompatibility.UNKNOWN,
            DataAction.SEARCH,
            (ImpactReasonCode.SNAPSHOT_PIPELINE_COMPATIBILITY_UNKNOWN,),
            diff.affected_dependency_keys,
        )
    if facts.snapshot.structural_freshness.state is FreshnessState.STALE:
        return _impact(
            ImpactAssetKind.SNAPSHOT,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.SEARCH,
            (ImpactReasonCode.SNAPSHOT_STRUCTURAL_STALE,),
            diff.affected_dependency_keys,
        )
    if facts.required_scope_covered is False and _search_scope_changed(diff):
        return _impact(
            ImpactAssetKind.SNAPSHOT,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.SEARCH,
            (ImpactReasonCode.SNAPSHOT_COVERAGE_INSUFFICIENT,),
            diff.affected_dependency_keys,
        )
    if facts.required_scope_covered is None:
        return _impact(
            ImpactAssetKind.SNAPSHOT,
            ImpactCompatibility.UNKNOWN,
            DataAction.SEARCH,
            (ImpactReasonCode.SNAPSHOT_COVERAGE_UNKNOWN,),
            diff.affected_dependency_keys,
        )
    if len(facts.missing_external_fact_keys) > 0:
        return _impact(
            ImpactAssetKind.SNAPSHOT,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.ENRICH,
            (ImpactReasonCode.SNAPSHOT_MISSING_EXTERNAL_FACTS,),
            diff.affected_dependency_keys,
        )
    if any(offer.offer_freshness.state is FreshnessState.STALE for offer in facts.snapshot.offers):
        return _impact(
            ImpactAssetKind.SNAPSHOT,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.REFRESH,
            (ImpactReasonCode.SNAPSHOT_OFFER_STALE,),
            diff.affected_dependency_keys,
        )
    reason = (
        ImpactReasonCode.NO_SEMANTIC_CHANGE
        if diff.change_kind is RequirementSemanticChangeKind.NO_SEMANTIC_CHANGE
        else ImpactReasonCode.SNAPSHOT_COMPATIBLE
    )
    return _impact(
        ImpactAssetKind.SNAPSHOT,
        ImpactCompatibility.COMPATIBLE,
        DataAction.REUSE,
        (reason,),
        (),
    )


def _derived_feature_impact(resolver_input: ImpactResolverInput) -> AssetImpact:
    diff = resolver_input.semantic_diff
    facts = resolver_input.artifacts
    if facts.feature_policy_compatible is None or facts.feature_reference_compatible is None:
        return _impact(
            ImpactAssetKind.DERIVED_FEATURE_SET,
            ImpactCompatibility.UNKNOWN,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.ARTIFACT_COMPATIBILITY_UNKNOWN,),
            diff.affected_dependency_keys,
        )
    if facts.feature_policy_compatible is False or facts.feature_reference_compatible is False:
        return _impact(
            ImpactAssetKind.DERIVED_FEATURE_SET,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.FEATURE_POLICY_OR_REFERENCE_CHANGED,),
            diff.affected_dependency_keys,
        )
    changed = _changed_active_feature_dependency_keys(facts, diff)
    if len(changed) > 0:
        return _impact(
            ImpactAssetKind.DERIVED_FEATURE_SET,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.FEATURE_DEPENDENCY_CHANGED,),
            changed,
        )
    return _impact(
        ImpactAssetKind.DERIVED_FEATURE_SET,
        ImpactCompatibility.COMPATIBLE,
        DataAction.REUSE,
        (ImpactReasonCode.NO_SEMANTIC_CHANGE,),
        (),
    )


def _filter_impact(resolver_input: ImpactResolverInput) -> AssetImpact:
    diff = resolver_input.semantic_diff
    facts = resolver_input.artifacts
    if facts.filter_policy_compatible is None:
        return _impact(
            ImpactAssetKind.FILTER_RESULT,
            ImpactCompatibility.UNKNOWN,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.ARTIFACT_COMPATIBILITY_UNKNOWN,),
            diff.affected_dependency_keys,
        )
    if facts.filter_policy_compatible is False or _hard_constraint_changed(diff):
        reason = (
            ImpactReasonCode.ARTIFACT_COMPATIBILITY_UNKNOWN
            if facts.filter_policy_compatible is False
            else ImpactReasonCode.FILTER_HARD_CONSTRAINT_CHANGED
        )
        return _impact(
            ImpactAssetKind.FILTER_RESULT,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.RECOMPUTE,
            (reason,),
            diff.affected_dependency_keys,
        )
    return _impact(
        ImpactAssetKind.FILTER_RESULT,
        ImpactCompatibility.COMPATIBLE,
        DataAction.REUSE,
        (ImpactReasonCode.FILTER_COMPATIBLE_WITH_SOFT_CHANGE,),
        (),
    )


def _ranking_impact(
    resolver_input: ImpactResolverInput,
    filter_impact: AssetImpact,
    feature_impact: AssetImpact,
) -> AssetImpact:
    diff = resolver_input.semantic_diff
    facts = resolver_input.artifacts
    if facts.ranking_policy_compatible is None:
        return _impact(
            ImpactAssetKind.RANKING_RESULT,
            ImpactCompatibility.UNKNOWN,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.ARTIFACT_COMPATIBILITY_UNKNOWN,),
            diff.affected_dependency_keys,
        )
    if facts.ranking_policy_compatible is False:
        return _impact(
            ImpactAssetKind.RANKING_RESULT,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.RANKING_POLICY_CHANGED,),
            diff.affected_dependency_keys,
        )
    ranking_keys = _ranking_dependency_keys(facts)
    changed_keys = _intersect_keys(diff.affected_dependency_keys, ranking_keys)
    if (
        filter_impact.required_action is DataAction.RECOMPUTE
        or feature_impact.required_action is DataAction.RECOMPUTE
        or len(changed_keys) > 0
    ):
        return _impact(
            ImpactAssetKind.RANKING_RESULT,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.RANKING_DEPENDENCY_CHANGED,),
            changed_keys or diff.affected_dependency_keys,
        )
    return _impact(
        ImpactAssetKind.RANKING_RESULT,
        ImpactCompatibility.COMPATIBLE,
        DataAction.REUSE,
        (ImpactReasonCode.NO_SEMANTIC_CHANGE,),
        (),
    )


def _recommendation_impact(
    resolver_input: ImpactResolverInput,
    filter_impact: AssetImpact,
    ranking_impact: AssetImpact,
    feature_impact: AssetImpact,
) -> AssetImpact:
    diff = resolver_input.semantic_diff
    facts = resolver_input.artifacts
    if facts.recommendation_policy_compatible is None:
        return _impact(
            ImpactAssetKind.RECOMMENDATION_RESULT,
            ImpactCompatibility.UNKNOWN,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.ARTIFACT_COMPATIBILITY_UNKNOWN,),
            diff.affected_dependency_keys,
        )
    if facts.recommendation_policy_compatible is False:
        return _impact(
            ImpactAssetKind.RECOMMENDATION_RESULT,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.RECOMMENDATION_POLICY_CHANGED,),
            diff.affected_dependency_keys,
        )
    if any(
        impact.required_action is DataAction.RECOMPUTE
        for impact in (filter_impact, ranking_impact, feature_impact)
    ):
        return _impact(
            ImpactAssetKind.RECOMMENDATION_RESULT,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.RECOMMENDATION_UPSTREAM_CHANGED,),
            diff.affected_dependency_keys,
        )
    return _impact(
        ImpactAssetKind.RECOMMENDATION_RESULT,
        ImpactCompatibility.COMPATIBLE,
        DataAction.REUSE,
        (ImpactReasonCode.NO_SEMANTIC_CHANGE,),
        (),
    )


def _relaxation_impact(
    resolver_input: ImpactResolverInput,
    filter_impact: AssetImpact,
    feature_impact: AssetImpact,
) -> AssetImpact:
    facts = resolver_input.artifacts
    diff = resolver_input.semantic_diff
    if facts.relaxation_policy_compatible is None:
        return _impact(
            ImpactAssetKind.RELAXATION_RESULT,
            ImpactCompatibility.UNKNOWN,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.ARTIFACT_COMPATIBILITY_UNKNOWN,),
            diff.affected_dependency_keys,
        )
    if facts.relaxation_policy_compatible is False or any(
        impact.required_action is DataAction.RECOMPUTE
        for impact in (filter_impact, feature_impact)
    ):
        return _impact(
            ImpactAssetKind.RELAXATION_RESULT,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.RELAXATION_UPSTREAM_CHANGED,),
            diff.affected_dependency_keys,
        )
    return _impact(
        ImpactAssetKind.RELAXATION_RESULT,
        ImpactCompatibility.COMPATIBLE,
        DataAction.REUSE,
        (ImpactReasonCode.NO_SEMANTIC_CHANGE,),
        (),
    )


def _explanation_impact(recommendation_impact: AssetImpact) -> AssetImpact:
    if recommendation_impact.required_action is DataAction.RECOMPUTE:
        return _impact(
            ImpactAssetKind.EXPLANATION,
            ImpactCompatibility.INCOMPATIBLE,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.EXPLANATION_DOWNSTREAM_OF_RECOMMENDATION,),
            recommendation_impact.affected_dependency_keys,
        )
    if recommendation_impact.compatibility is ImpactCompatibility.UNKNOWN:
        return _impact(
            ImpactAssetKind.EXPLANATION,
            ImpactCompatibility.UNKNOWN,
            DataAction.RECOMPUTE,
            (ImpactReasonCode.ARTIFACT_COMPATIBILITY_UNKNOWN,),
            recommendation_impact.affected_dependency_keys,
        )
    return _impact(
        ImpactAssetKind.EXPLANATION,
        ImpactCompatibility.COMPATIBLE,
        DataAction.REUSE,
        (ImpactReasonCode.NO_SEMANTIC_CHANGE,),
        (),
    )


def _impact(
    asset_kind: ImpactAssetKind,
    compatibility: ImpactCompatibility,
    required_action: DataAction,
    reason_codes: tuple[ImpactReasonCode, ...],
    affected_dependency_keys: tuple[RequirementDependencyKey, ...],
) -> AssetImpact:
    return AssetImpact(
        asset_kind=asset_kind,
        compatibility=compatibility,
        required_action=required_action,
        reason_codes=reason_codes,
        affected_dependency_keys=affected_dependency_keys,
    )


def _search_scope_changed(diff: RequirementSemanticDiff) -> bool:
    return any(
        change.subject_type in {SemanticSubjectType.ROUTE, SemanticSubjectType.DATE}
        for change in diff.changes
    )


def _hard_constraint_changed(diff: RequirementSemanticDiff) -> bool:
    return any(
        change.subject_type
        in {
            SemanticSubjectType.TRIP,
            SemanticSubjectType.ROUTE,
            SemanticSubjectType.DATE,
            SemanticSubjectType.HARD_CONSTRAINT,
        }
        for change in diff.changes
    )


def _changed_active_feature_dependency_keys(
    facts: M6ArtifactFacts,
    diff: RequirementSemanticDiff,
) -> tuple[RequirementDependencyKey, ...]:
    dependencies = tuple(
        dependency_key
        for feature_key in facts.active_feature_keys
        for dependency_key in _feature_requirement_dependency_keys(facts, feature_key)
    )
    return _intersect_keys(diff.affected_dependency_keys, dependencies)


def _feature_requirement_dependency_keys(
    facts: M6ArtifactFacts,
    feature_key: FeatureKey,
) -> tuple[RequirementDependencyKey, ...]:
    definition = facts.feature_registry.get(feature_key)
    return tuple(
        _m6_requirement_dependency_key(dependency.source, dependency.key)
        for dependency in definition.requirement_dependencies
    )


def _ranking_dependency_keys(facts: M6ArtifactFacts) -> tuple[RequirementDependencyKey, ...]:
    return tuple(
        key
        for policy in facts.ranking_policy_set.preference_policies
        for key in (
            _preference_dependency_key(policy.preference_scope.value),
            RequirementDependencyKey(f"{_preference_dependency_key(policy.preference_scope.value).value}.importance"),
        )
    )


def _m6_requirement_dependency_key(source: str, key: str) -> RequirementDependencyKey:
    if source == "constraint" and key == "DEPARTURE_DATE":
        return RequirementDependencyKey("requirement.trip.departure_date")
    if source == "constraint" and key == "MAX_PRICE":
        return RequirementDependencyKey("constraint.max_price")
    if source == "constraint" and key == "MAX_STOPS":
        return RequirementDependencyKey("constraint.max_stops")
    return RequirementDependencyKey(f"{source}.{key.lower()}")


def _preference_dependency_key(scope: str) -> RequirementDependencyKey:
    keys = {
        "PRICE": "preference.price",
        "DEPARTURE_TIME": "preference.departure_time",
        "ARRIVAL_TIME": "preference.arrival_time",
        "AIRPORT_MATCH": "preference.airport_match",
        "FEWER_STOPS": "preference.fewer_stops",
    }
    return RequirementDependencyKey(keys[scope])


def _intersect_keys(
    left: tuple[RequirementDependencyKey, ...],
    right: tuple[RequirementDependencyKey, ...],
) -> tuple[RequirementDependencyKey, ...]:
    right_values = {key.value for key in right}
    return tuple(RequirementDependencyKey(key.value) for key in left if key.value in right_values)
