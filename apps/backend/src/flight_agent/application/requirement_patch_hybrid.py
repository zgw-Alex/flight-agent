"""Deterministic-first Patch Hybrid front-half for M8-U6H-A."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum

from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import (
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementState,
    SoftPreference,
    StopCount,
)
from flight_agent.ports import (
    InterpreterFailure,
    InterpreterInput,
    InterpreterMode,
    InterpreterResult,
    PatchProposalAction,
    PatchProposalOperation,
    PatchRequirementProposal,
    ProposalEvidence,
    RequirementInterpretationContext,
    SourceSpanHint,
)


class SemanticEvidenceKind(str, Enum):
    TARGET_TEXT = "TARGET_TEXT"
    VALUE_TEXT = "VALUE_TEXT"
    OPERATOR_TEXT = "OPERATOR_TEXT"
    MODALITY_TEXT = "MODALITY_TEXT"
    REFERENCE_TEXT = "REFERENCE_TEXT"
    CORRECTION_TEXT = "CORRECTION_TEXT"
    PRESERVATION_TEXT = "PRESERVATION_TEXT"


class SemanticTarget(str, Enum):
    MAX_PRICE = "MAX_PRICE"
    MAX_STOPS = "MAX_STOPS"
    FEWER_STOPS = "FEWER_STOPS"
    DEPARTURE_DATE = "DEPARTURE_DATE"
    AMBIGUOUS_REFERENCE = "AMBIGUOUS_REFERENCE"


class SemanticOperation(str, Enum):
    SET = "SET"
    REMOVE = "REMOVE"
    CLEAR = "CLEAR"
    NO_OP = "NO_OP"


class SemanticImportanceSignal(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"
    UNSPECIFIED = "UNSPECIFIED"


class ResolutionDisposition(str, Enum):
    RESOLVED = "RESOLVED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    SEMANTIC_RESOLVER_REQUIRED = "SEMANTIC_RESOLVER_REQUIRED"
    INVALID = "INVALID"


@dataclass(frozen=True)
class SourceLocation:
    start: int
    end: int


@dataclass(frozen=True)
class SemanticEvidence:
    evidence_id: str
    kind: SemanticEvidenceKind
    source_text: str
    normalized_text: str | None = None
    source_location: SourceLocation | None = None
    extractor_id: str = "patch-evidence-extractor"
    extractor_version: str = "m8-u6h-a-v1"


@dataclass(frozen=True)
class SemanticMutation:
    target: SemanticTarget
    operation: SemanticOperation
    value_evidence_id: str | None = None
    value: object | None = None
    importance_signal: SemanticImportanceSignal = SemanticImportanceSignal.UNSPECIFIED
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticAssertion:
    assertion_type: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class SemanticAmbiguity:
    code: str
    message: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PatchSemanticIR:
    disposition: ResolutionDisposition
    mutations: tuple[SemanticMutation, ...] = ()
    assertions: tuple[SemanticAssertion, ...] = ()
    ambiguities: tuple[SemanticAmbiguity, ...] = ()
    evidence: tuple[SemanticEvidence, ...] = ()
    interpreter_metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PendingPatchInterpretation:
    requirement_id: str
    requirement_version: int
    resolved_evidence_ids: tuple[str, ...]
    unresolved_issues: tuple[SemanticAmbiguity, ...]


class PatchEvidenceExtractor:
    def extract(self, message: str) -> tuple[SemanticEvidence, ...]:
        evidence: list[SemanticEvidence] = []
        for index, match in enumerate(re.finditer(r"\d+(?:\.\d+)?", message), start=1):
            evidence.append(
                SemanticEvidence(
                    evidence_id=f"ev-value-{index}",
                    kind=SemanticEvidenceKind.VALUE_TEXT,
                    source_text=match.group(0),
                    normalized_text=match.group(0),
                    source_location=SourceLocation(match.start(), match.end()),
                )
            )
        for index, token in enumerate(("预算", "价格", "直飞", "转一次", "限制", "日期"), start=1):
            start = message.find(token)
            if start >= 0:
                evidence.append(
                    SemanticEvidence(
                        evidence_id=f"ev-target-{index}",
                        kind=SemanticEvidenceKind.TARGET_TEXT,
                        source_text=token,
                        normalized_text=token,
                        source_location=SourceLocation(start, start + len(token)),
                    )
                )
        for index, token in enumerate(("不对", "不是", "改成"), start=1):
            start = message.find(token)
            if start >= 0:
                evidence.append(
                    SemanticEvidence(
                        evidence_id=f"ev-correction-{index}",
                        kind=SemanticEvidenceKind.CORRECTION_TEXT,
                        source_text=token,
                        source_location=SourceLocation(start, start + len(token)),
                    )
                )
        for index, token in enumerate(("其他不变", "其余照旧"), start=1):
            start = message.find(token)
            if start >= 0:
                evidence.append(
                    SemanticEvidence(
                        evidence_id=f"ev-preserve-{index}",
                        kind=SemanticEvidenceKind.PRESERVATION_TEXT,
                        source_text=token,
                        source_location=SourceLocation(start, start + len(token)),
                    )
                )
        return tuple(evidence)


class DeterministicPatchInterpreter:
    def __init__(self, extractor: PatchEvidenceExtractor | None = None) -> None:
        self._extractor = extractor or PatchEvidenceExtractor()

    def interpret(self, message: str) -> PatchSemanticIR:
        evidence = self._extractor.extract(message)
        compact = re.sub(r"\s+", "", message)
        semantic_resolver = _semantic_resolver_reason(compact)
        if semantic_resolver is not None:
            return PatchSemanticIR(
                disposition=ResolutionDisposition.SEMANTIC_RESOLVER_REQUIRED,
                ambiguities=(SemanticAmbiguity("SEMANTIC_RESOLVER_REQUIRED", semantic_resolver),),
                evidence=evidence,
            )
        clarification = _clarification_reason(compact)
        raw_mutations = _raw_mutations(compact, evidence)
        if clarification is not None:
            return PatchSemanticIR(
                disposition=ResolutionDisposition.CLARIFICATION_REQUIRED,
                mutations=raw_mutations,
                ambiguities=(SemanticAmbiguity("CLARIFICATION_REQUIRED", clarification),),
                evidence=evidence,
            )
        assertions = (
            (
                SemanticAssertion(
                    "GLOBAL_UNMENTIONED_PRESERVE",
                    tuple(item.evidence_id for item in evidence if item.kind is SemanticEvidenceKind.PRESERVATION_TEXT),
                ),
            )
            if any(item.kind is SemanticEvidenceKind.PRESERVATION_TEXT for item in evidence)
            else ()
        )
        return PatchSemanticIR(
            disposition=ResolutionDisposition.RESOLVED if raw_mutations else ResolutionDisposition.CLARIFICATION_REQUIRED,
            mutations=raw_mutations,
            assertions=assertions,
            ambiguities=()
            if raw_mutations
            else (SemanticAmbiguity("NO_DETERMINISTIC_MUTATION", "No deterministic patch semantics found"),),
            evidence=evidence,
        )


class MutationConsolidator:
    def consolidate(self, ir: PatchSemanticIR) -> PatchSemanticIR:
        if ir.disposition is not ResolutionDisposition.RESOLVED:
            return ir
        corrections = tuple(
            evidence for evidence in ir.evidence if evidence.kind is SemanticEvidenceKind.CORRECTION_TEXT
        )
        grouped: dict[tuple[SemanticTarget, SemanticOperation, SemanticImportanceSignal], list[SemanticMutation]] = {}
        for mutation in ir.mutations:
            grouped.setdefault(
                (mutation.target, mutation.operation, mutation.importance_signal),
                [],
            ).append(mutation)
        consolidated: list[SemanticMutation] = []
        for mutations in grouped.values():
            distinct_values = {repr(mutation.value) for mutation in mutations}
            if len(distinct_values) == 1 or corrections:
                consolidated.append(mutations[-1])
            else:
                return replace(
                    ir,
                    disposition=ResolutionDisposition.CLARIFICATION_REQUIRED,
                    ambiguities=(
                        *ir.ambiguities,
                        SemanticAmbiguity(
                            "CONTRADICTORY_VALUES",
                            "Contradictory values require clarification",
                            tuple(evidence.evidence_id for evidence in ir.evidence),
                        ),
                    ),
                )
        return replace(ir, mutations=tuple(consolidated))


class PatchInterpretationValidator:
    def validate(self, ir: PatchSemanticIR) -> PatchSemanticIR:
        if ir.disposition is not ResolutionDisposition.RESOLVED:
            return ir
        if any(mutation.target is SemanticTarget.DEPARTURE_DATE for mutation in ir.mutations):
            return replace(
                ir,
                disposition=ResolutionDisposition.CLARIFICATION_REQUIRED,
                ambiguities=(
                    *ir.ambiguities,
                    SemanticAmbiguity("PARTIALLY_RESOLVED", "Date reference requires clarification"),
                ),
            )
        return ir


class PatchInterpretationRouter:
    def __init__(self, validator: PatchInterpretationValidator | None = None) -> None:
        self._validator = validator or PatchInterpretationValidator()

    def route(self, ir: PatchSemanticIR) -> PatchSemanticIR:
        return self._validator.validate(ir)


class SemanticIRToPatchProposalBuilder:
    def build(
        self,
        ir: PatchSemanticIR,
        current: RequirementState,
        source_input: str,
    ) -> PatchRequirementProposal:
        lineage = {
            "source_input": source_input,
            "based_on_requirement_id": current.requirement_id,
            "based_on_requirement_version": current.version,
            "evidence": _proposal_evidence(ir.evidence),
        }
        if ir.disposition is ResolutionDisposition.CLARIFICATION_REQUIRED:
            return PatchRequirementProposal(
                unresolved_semantics=tuple(issue.message for issue in ir.ambiguities),
                ambiguity_reasons=tuple(issue.code for issue in ir.ambiguities),
                **lineage,
            )
        if ir.disposition is ResolutionDisposition.SEMANTIC_RESOLVER_REQUIRED:
            return PatchRequirementProposal(
                unresolved_semantics=("Semantic resolver required",),
                ambiguity_reasons=("SEMANTIC_RESOLVER_REQUIRED",),
                **lineage,
            )
        if ir.disposition is ResolutionDisposition.INVALID:
            return PatchRequirementProposal(
                unresolved_semantics=("Invalid deterministic patch interpretation",),
                ambiguity_reasons=("INVALID",),
                **lineage,
            )

        operations: list[PatchProposalOperation] = []
        issues: list[str] = []
        for mutation in ir.mutations:
            operation = _operation_for_mutation(mutation, current)
            if isinstance(operation, str):
                issues.append(operation)
            elif operation is not None:
                operations.extend(operation)
        return PatchRequirementProposal(
            operations=tuple(operations),
            unresolved_semantics=tuple(issues),
            ambiguity_reasons=tuple(issues),
            **lineage,
        )


class DeterministicPatchHybridInterpreter:
    def __init__(self) -> None:
        self._interpreter = DeterministicPatchInterpreter()
        self._consolidator = MutationConsolidator()
        self._router = PatchInterpretationRouter()
        self._builder = SemanticIRToPatchProposalBuilder()
        self.last_ir: PatchSemanticIR | None = None

    def interpret(
        self,
        interpreter_input: InterpreterInput,
        context: RequirementInterpretationContext | None = None,
    ) -> InterpreterResult:
        if interpreter_input.mode is not InterpreterMode.PATCH:
            return InterpreterResult.failure_result(
                InterpreterFailure(
                    "PATCH_ONLY",
                    "Deterministic Patch Hybrid only supports PATCH input",
                    interpreter_input.source_input,
                )
            )
        current = getattr(context, "current_requirement", None)
        if not isinstance(current, RequirementState):
            return InterpreterResult.failure_result(
                InterpreterFailure(
                    "PATCH_CURRENT_REQUIRED",
                    "Deterministic Patch Hybrid requires current RequirementState context",
                    interpreter_input.source_input,
                )
            )
        ir = self._router.route(self._consolidator.consolidate(self._interpreter.interpret(interpreter_input.source_input)))
        self.last_ir = ir
        proposal = self._builder.build(ir, current, interpreter_input.source_input)
        if proposal.unresolved_semantics:
            return InterpreterResult.unresolved(proposal)
        return InterpreterResult.success(proposal)


def build_deterministic_patch_proposal(
    message: str,
    current: RequirementState,
) -> tuple[PatchSemanticIR, PatchRequirementProposal]:
    interpreter = DeterministicPatchInterpreter()
    ir = PatchInterpretationRouter().route(MutationConsolidator().consolidate(interpreter.interpret(message)))
    return ir, SemanticIRToPatchProposalBuilder().build(ir, current, message)


def _semantic_resolver_reason(message: str) -> str | None:
    if "如果" in message or "便宜很多" in message:
        return "Conditional or comparative direct-flight semantics require semantic resolver"
    if "别卡那么死" in message or "更重要" in message:
        return "Relative trade-off semantics require semantic resolver"
    return None


def _clarification_reason(message: str) -> str | None:
    if "稍微" in message or "放宽一点" in message:
        return "Missing exact value"
    if "或者" in message or "都行" in message:
        return "Alternative values are unsupported"
    if "那个限制" in message:
        return "Ambiguous target reference"
    if "下周那个时间" in message or "日期改" in message:
        return "Date reference requires clarification"
    return None


def _raw_mutations(message: str, evidence: tuple[SemanticEvidence, ...]) -> tuple[SemanticMutation, ...]:
    mutations: list[SemanticMutation] = []
    numbers = tuple(item for item in evidence if item.kind is SemanticEvidenceKind.VALUE_TEXT)
    last_number = numbers[-1] if numbers else None
    if _mentions_price(message) and last_number is not None:
        mutations.append(
            SemanticMutation(
                target=SemanticTarget.MAX_PRICE,
                operation=SemanticOperation.SET,
                value_evidence_id=last_number.evidence_id,
                value=Money(Decimal(last_number.source_text), "CNY"),
                importance_signal=SemanticImportanceSignal.HARD,
                evidence_ids=(last_number.evidence_id,),
            )
        )
    elif ("不对" in message or "不是" in message) and last_number is not None:
        mutations.append(
            SemanticMutation(
                target=SemanticTarget.MAX_PRICE,
                operation=SemanticOperation.SET,
                value_evidence_id=last_number.evidence_id,
                value=Money(Decimal(last_number.source_text), "CNY"),
                importance_signal=SemanticImportanceSignal.HARD,
                evidence_ids=tuple(item.evidence_id for item in evidence),
            )
        )
    if "取消预算限制" in message or "取消价格限制" in message:
        mutations.append(
            SemanticMutation(
                target=SemanticTarget.MAX_PRICE,
                operation=SemanticOperation.REMOVE,
                importance_signal=SemanticImportanceSignal.HARD,
            )
        )
    if "必须直飞" in message or "继续要求直飞" in message:
        mutations.append(
            SemanticMutation(
                target=SemanticTarget.MAX_STOPS,
                operation=SemanticOperation.SET,
                value=StopCount(0),
                importance_signal=SemanticImportanceSignal.HARD,
            )
        )
    if "最多转一次" in message:
        mutations.append(
            SemanticMutation(
                target=SemanticTarget.MAX_STOPS,
                operation=SemanticOperation.SET,
                value=StopCount(1),
                importance_signal=SemanticImportanceSignal.HARD,
            )
        )
    if "最好直飞" in message or "最好就行" in message:
        mutations.append(
            SemanticMutation(
                target=SemanticTarget.FEWER_STOPS,
                operation=SemanticOperation.SET,
                importance_signal=SemanticImportanceSignal.SOFT,
            )
        )
    if "不用必须" in message and not ("最好" in message or "就行" in message):
        mutations.append(
            SemanticMutation(
                target=SemanticTarget.MAX_STOPS,
                operation=SemanticOperation.REMOVE,
                importance_signal=SemanticImportanceSignal.HARD,
            )
        )
    if "不用必须" in message and ("最好" in message or "就行" in message):
        mutations = [
            SemanticMutation(
                target=SemanticTarget.MAX_STOPS,
                operation=SemanticOperation.REMOVE,
                importance_signal=SemanticImportanceSignal.HARD,
            ),
            SemanticMutation(
                target=SemanticTarget.FEWER_STOPS,
                operation=SemanticOperation.SET,
                importance_signal=SemanticImportanceSignal.SOFT,
            ),
        ]
    if "日期改" in message:
        mutations.append(SemanticMutation(SemanticTarget.DEPARTURE_DATE, SemanticOperation.SET))
    return tuple(mutations)


def _mentions_price(message: str) -> bool:
    return any(token in message for token in ("预算", "价格", "不要超过", "不超过"))


def _operation_for_mutation(
    mutation: SemanticMutation,
    current: RequirementState,
) -> tuple[PatchProposalOperation, ...] | str | None:
    if mutation.operation is SemanticOperation.NO_OP:
        return None
    if mutation.target is SemanticTarget.MAX_PRICE:
        return _max_price_operation(mutation, current)
    if mutation.target is SemanticTarget.MAX_STOPS:
        return _max_stops_operation(mutation, current)
    if mutation.target is SemanticTarget.FEWER_STOPS:
        return _soft_direct_operation(mutation, current)
    return f"Unsupported semantic target: {mutation.target.value}"


def _max_price_operation(
    mutation: SemanticMutation,
    current: RequirementState,
) -> tuple[PatchProposalOperation, ...] | str:
    matches = tuple(item for item in current.constraints if item.scope is ConstraintScope.MAX_PRICE)
    if mutation.operation is SemanticOperation.REMOVE:
        if len(matches) != 1:
            return "MAX_PRICE target is not unique"
        return (PatchProposalOperation(PatchProposalAction.REMOVE_CONSTRAINT, target_id=matches[0].constraint_id),)
    if not isinstance(mutation.value, Money):
        return "MAX_PRICE value is missing"
    item = HardConstraint(
        constraint_id=ConstraintId("hybrid-max-price"),
        scope=ConstraintScope.MAX_PRICE,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=mutation.value,
    )
    if len(matches) == 0:
        return (PatchProposalOperation(PatchProposalAction.ADD_CONSTRAINT, item=item),)
    if len(matches) == 1:
        if _hard_constraint_equivalent(matches[0], item):
            return ()
        return (
            PatchProposalOperation(
                PatchProposalAction.REPLACE_CONSTRAINT,
                item=item,
                target_id=matches[0].constraint_id,
            ),
        )
    return "MAX_PRICE target is ambiguous"


def _max_stops_operation(
    mutation: SemanticMutation,
    current: RequirementState,
) -> tuple[PatchProposalOperation, ...] | str:
    matches = tuple(item for item in current.constraints if item.scope is ConstraintScope.MAX_STOPS)
    if mutation.operation is SemanticOperation.REMOVE:
        if len(matches) != 1:
            return "MAX_STOPS target is not unique"
        return (PatchProposalOperation(PatchProposalAction.REMOVE_CONSTRAINT, target_id=matches[0].constraint_id),)
    if not isinstance(mutation.value, StopCount):
        return "MAX_STOPS value is missing"
    item = HardConstraint(
        constraint_id=ConstraintId("hybrid-max-stops"),
        scope=ConstraintScope.MAX_STOPS,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=mutation.value,
    )
    if len(matches) == 0:
        return (PatchProposalOperation(PatchProposalAction.ADD_CONSTRAINT, item=item),)
    if len(matches) == 1:
        if _hard_constraint_equivalent(matches[0], item):
            return ()
        return (
            PatchProposalOperation(
                PatchProposalAction.REPLACE_CONSTRAINT,
                item=item,
                target_id=matches[0].constraint_id,
            ),
        )
    return "MAX_STOPS target is ambiguous"


def _soft_direct_operation(
    mutation: SemanticMutation,
    current: RequirementState,
) -> tuple[PatchProposalOperation, ...] | str:
    matches = tuple(item for item in current.preferences if item.scope is PreferenceScope.FEWER_STOPS)
    item = SoftPreference(
        preference_id=PreferenceId("hybrid-fewer-stops"),
        scope=PreferenceScope.FEWER_STOPS,
        importance=PreferenceImportance.HIGH,
    )
    operations: list[PatchProposalOperation] = []
    if len(matches) == 0:
        operations.append(PatchProposalOperation(PatchProposalAction.ADD_PREFERENCE, item=item))
    elif len(matches) == 1:
        if not _soft_preference_equivalent(matches[0], item):
            operations.append(
                PatchProposalOperation(
                    PatchProposalAction.REPLACE_PREFERENCE,
                    item=item,
                    target_id=matches[0].preference_id,
                )
            )
    else:
        return "FEWER_STOPS preference target is ambiguous"
    return tuple(operations)


def _hard_constraint_equivalent(current: HardConstraint, desired: HardConstraint) -> bool:
    return (
        current.scope is desired.scope
        and current.operator is desired.operator
        and current.value == desired.value
    )


def _soft_preference_equivalent(current: SoftPreference, desired: SoftPreference) -> bool:
    return (
        current.scope is desired.scope
        and current.importance is desired.importance
        and current.value == desired.value
    )


def _proposal_evidence(evidence: tuple[SemanticEvidence, ...]) -> tuple[ProposalEvidence, ...]:
    return tuple(
        ProposalEvidence(
            source_input=item.source_text,
            span=SourceSpanHint(item.source_location.start, item.source_location.end, item.source_text)
            if item.source_location is not None
            else None,
        )
        for item in evidence
    )
