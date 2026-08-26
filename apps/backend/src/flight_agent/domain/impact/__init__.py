"""M7 impact-domain primitives."""

from flight_agent.domain.impact.decision import (
    AssetImpact,
    DataAction,
    ImpactAssetKind,
    ImpactCompatibility,
    ImpactDecision,
    ImpactReasonCode,
    ImpactResolver,
    ImpactResolverInput,
    M6ArtifactFacts,
    SnapshotCompatibilityFacts,
)
from flight_agent.domain.impact.semantic_diff import (
    HardConstraintSemanticEffect,
    RequirementDependencyKey,
    RequirementSemanticChange,
    RequirementSemanticChangeKind,
    RequirementSemanticDiff,
    RequirementSemanticDiffer,
    SemanticSubjectType,
    SoftPreferenceSemanticEffect,
    StructuralChangeKind,
)

__all__ = [
    "AssetImpact",
    "DataAction",
    "HardConstraintSemanticEffect",
    "ImpactAssetKind",
    "ImpactCompatibility",
    "ImpactDecision",
    "ImpactReasonCode",
    "ImpactResolver",
    "ImpactResolverInput",
    "M6ArtifactFacts",
    "RequirementDependencyKey",
    "RequirementSemanticChange",
    "RequirementSemanticChangeKind",
    "RequirementSemanticDiff",
    "RequirementSemanticDiffer",
    "SemanticSubjectType",
    "SnapshotCompatibilityFacts",
    "SoftPreferenceSemanticEffect",
    "StructuralChangeKind",
]
