"""Application orchestration for the M8-U6H-C semantic resolver."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Any

from flight_agent.application.requirement_parser_hybrid import (
    BindingConsolidator,
    DeterministicInitialBinder,
    DeterministicInitialProposalBuilder,
    ParserBindingState,
    ParserCandidateType,
    ParserEvidenceExtractor,
    ParserEvidenceKind,
    ParserInterpretationRouter,
    ParserInterpretationStatus,
    ParserSemanticBinding,
    ParserSemanticEvidence,
    ParserSemanticIR,
    ParserSemanticTarget,
    RequiredSlotCompletenessDeriver,
)
from flight_agent.application.requirement_patch_hybrid import (
    DeterministicPatchInterpreter,
    MutationConsolidator,
    PatchInterpretationRouter,
    PatchSemanticIR,
    ResolutionDisposition,
    SemanticImportanceSignal,
    SemanticMutation,
    SemanticOperation,
    SemanticTarget,
)
from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import PreferenceImportance, RequirementState, StopCount
from flight_agent.ports import (
    InitialRequirementProposal,
    InterpreterFailure,
    InterpreterInput,
    InterpreterMode,
    InterpreterResult,
    PatchRequirementProposal,
    RequirementInterpretationContext,
)
from flight_agent.ports.semantic_resolver import (
    SEMANTIC_RESOLVER_CONTRACT_VERSION,
    SemanticResolver,
    SemanticResolverEvidence,
    SemanticResolverFailure,
    SemanticResolverFailureKind,
    SemanticResolverPreferenceImportance,
    SemanticResolverRelation,
    SemanticResolverRequest,
    SemanticResolverResponse,
    SemanticResolverResult,
    SemanticResolverStatus,
    SemanticResolverTaskKind,
    SemanticResolverUnresolvedItem,
)

PATCH_OUTPUT_VOCABULARY = (
    "RELAX_MAX_STOPS_TO_ONE",
    "CONVERT_HARD_DIRECT_TO_SOFT_FEWER_STOPS",
    "PRICE_RELAXATION_LOWER_PRIORITY_THAN_DIRECT",
    "ADD_SOFT_FEWER_STOPS_PREFERENCE",
    "ADD_SOFT_PRICE_PREFERENCE",
    "REMOVE_SOFT_PREFERENCE",
    "NO_AUTHORITATIVE_MUTATION",
)
PARSER_OUTPUT_VOCABULARY = (
    "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION",
    "ADD_SOFT_FEWER_STOPS_PREFERENCE",
    "ADD_SOFT_PRICE_PREFERENCE",
    "ADD_HARD_MAX_PRICE_CONSTRAINT",
    "ADD_HARD_MAX_STOPS_CONSTRAINT",
    "NO_AUTHORITATIVE_BINDING",
)


class ParserResolverRoutingOutcome(str, Enum):
    DETERMINISTIC_COMPLETE = "DETERMINISTIC_COMPLETE"
    RESOLVER_ELIGIBLE = "RESOLVER_ELIGIBLE"
    NON_BLOCKING_UNSUPPORTED = "NON_BLOCKING_UNSUPPORTED"
    MISSING_EVIDENCE_BLOCKING = "MISSING_EVIDENCE_BLOCKING"
    HARD_UNRESOLVED_BLOCKING = "HARD_UNRESOLVED_BLOCKING"
    IRRELEVANT = "IRRELEVANT"


@dataclass(frozen=True)
class ParserResolverRoutingDecision:
    outcome: ParserResolverRoutingOutcome
    reason: str
    candidate_semantic_space: tuple[str, ...] = ()

    @property
    def should_call(self) -> bool:
        return self.outcome is ParserResolverRoutingOutcome.RESOLVER_ELIGIBLE

    @property
    def should_progress_without_resolver(self) -> bool:
        return self.outcome in {
            ParserResolverRoutingOutcome.NON_BLOCKING_UNSUPPORTED,
            ParserResolverRoutingOutcome.IRRELEVANT,
        }
_STRICT_RESPONSE_FIELDS = frozenset(
    {"request_id", "status", "relations", "unresolved_items", "diagnostics", "model_metadata"}
)
_STRICT_RELATION_FIELDS = frozenset(
    {"relation_kind", "evidence_ids", "target", "value", "importance", "confidence"}
)
_STRICT_UNRESOLVED_FIELDS = frozenset({"code", "message", "evidence_ids"})
_STRICT_METADATA_FIELDS = frozenset({"key", "value"})
_CA04_IMPORTANCE_RELATION_KINDS = frozenset(
    {"ADD_SOFT_PRICE_PREFERENCE", "ADD_SOFT_FEWER_STOPS_PREFERENCE"}
)


class SemanticResolverPatchHybridInterpreter:
    def __init__(self, resolver: SemanticResolver) -> None:
        self._resolver = resolver
        self._interpreter = DeterministicPatchInterpreter()
        self._consolidator = MutationConsolidator()
        self._router = PatchInterpretationRouter()
        self.last_ir: PatchSemanticIR | None = None
        self.last_resolver_result: SemanticResolverResult | None = None

    def interpret(
        self,
        interpreter_input: InterpreterInput,
        context: RequirementInterpretationContext | None = None,
    ) -> InterpreterResult:
        if interpreter_input.mode is not InterpreterMode.PATCH:
            return InterpreterResult.failure_result(
                InterpreterFailure(
                    "PATCH_ONLY",
                    "Semantic Resolver Patch Hybrid only supports PATCH input",
                    interpreter_input.source_input,
                )
            )
        current = getattr(context, "current_requirement", None)
        if not isinstance(current, RequirementState):
            return InterpreterResult.failure_result(
                InterpreterFailure(
                    "PATCH_CURRENT_REQUIRED",
                    "Semantic Resolver Patch Hybrid requires current RequirementState context",
                    interpreter_input.source_input,
                )
            )
        front_ir = self._router.route(
            self._consolidator.consolidate(self._interpreter.interpret(interpreter_input.source_input))
        )
        ir, proposal, resolver_result = resolve_patch_semantics(
            front_ir,
            current,
            interpreter_input.source_input,
            self._resolver,
        )
        self.last_ir = ir
        self.last_resolver_result = resolver_result
        if proposal.unresolved_semantics:
            return InterpreterResult.unresolved(proposal)
        return InterpreterResult.success(proposal)


class SemanticResolverParserHybridInterpreter:
    def __init__(self, resolver: SemanticResolver) -> None:
        self._resolver = resolver
        self._extractor = ParserEvidenceExtractor()
        self._binder = DeterministicInitialBinder()
        self._consolidator = BindingConsolidator()
        self._deriver = RequiredSlotCompletenessDeriver()
        self._router = ParserInterpretationRouter()
        self.last_ir: ParserSemanticIR | None = None
        self.last_resolver_result: SemanticResolverResult | None = None

    def interpret(
        self,
        interpreter_input: InterpreterInput,
        context: RequirementInterpretationContext | None = None,
    ) -> InterpreterResult:
        _ = context
        if interpreter_input.mode is not InterpreterMode.INITIAL:
            return InterpreterResult.failure_result(
                InterpreterFailure(
                    "INITIAL_ONLY",
                    "Semantic Resolver Parser Hybrid only supports INITIAL input",
                    interpreter_input.source_input,
                )
            )
        evidence = self._extractor.extract(interpreter_input.source_input)
        bindings = self._consolidator.consolidate(
            self._binder.bind(interpreter_input.source_input, evidence),
            evidence,
        )
        required_slots = self._deriver.derive(bindings)
        front_ir = self._router.route(bindings, required_slots, evidence)
        ir, proposal, resolver_result = resolve_parser_semantics(
            front_ir,
            interpreter_input.source_input,
            self._resolver,
        )
        self.last_ir = ir
        self.last_resolver_result = resolver_result
        if proposal.unresolved_semantics:
            return InterpreterResult.unresolved(proposal)
        return InterpreterResult.success(proposal)


def should_call_semantic_resolver(ir: PatchSemanticIR | ParserSemanticIR) -> bool:
    if isinstance(ir, PatchSemanticIR):
        return ir.disposition is ResolutionDisposition.SEMANTIC_RESOLVER_REQUIRED
    return evaluate_parser_resolver_routing(ir).should_call


def evaluate_parser_resolver_routing(ir: ParserSemanticIR) -> ParserResolverRoutingDecision:
    if ir.interpretation_status is not ParserInterpretationStatus.SEMANTIC_RESOLVER_REQUIRED:
        if ir.interpretation_status is ParserInterpretationStatus.RESOLVED:
            return ParserResolverRoutingDecision(
                ParserResolverRoutingOutcome.DETERMINISTIC_COMPLETE,
                "already_resolved_deterministically",
            )
        if any(slot.state is not ParserBindingState.RESOLVED for slot in ir.required_slots):
            return ParserResolverRoutingDecision(
                ParserResolverRoutingOutcome.MISSING_EVIDENCE_BLOCKING,
                "missing_required_evidence",
            )
        if any(
            binding.state is not ParserBindingState.RESOLVED
            and binding.target in {ParserSemanticTarget.MAX_PRICE, ParserSemanticTarget.MAX_STOPS, ParserSemanticTarget.TRIP_STRUCTURE}
            for binding in ir.bindings
        ):
            return ParserResolverRoutingDecision(
                ParserResolverRoutingOutcome.HARD_UNRESOLVED_BLOCKING,
                "explicit_hard_unresolved",
            )
        return ParserResolverRoutingDecision(
            ParserResolverRoutingOutcome.MISSING_EVIDENCE_BLOCKING,
            "blocking_deterministic_issue",
        )

    if any(slot.state is not ParserBindingState.RESOLVED for slot in ir.required_slots):
        return ParserResolverRoutingDecision(
            ParserResolverRoutingOutcome.MISSING_EVIDENCE_BLOCKING,
            "missing_required_evidence",
        )
    if any(binding.state is not ParserBindingState.RESOLVED for binding in ir.bindings):
        return ParserResolverRoutingDecision(
            ParserResolverRoutingOutcome.HARD_UNRESOLVED_BLOCKING,
            "explicit_hard_unresolved",
        )
    unsupported_evidence = tuple(item for item in ir.evidence if item.kind is ParserEvidenceKind.UNSUPPORTED_TEXT)
    if not unsupported_evidence:
        return ParserResolverRoutingDecision(
            ParserResolverRoutingOutcome.IRRELEVANT,
            "no_downstream_proposal_value",
        )
    candidate_semantic_space = _parser_candidate_semantic_space(unsupported_evidence)
    full_evidence_text = _compact_parser_evidence_text(ir.evidence)
    if (
        _has_negated_price_preference_context(full_evidence_text)
        and candidate_semantic_space == ("ADD_SOFT_PRICE_PREFERENCE",)
    ):
        return ParserResolverRoutingDecision(
            ParserResolverRoutingOutcome.NON_BLOCKING_UNSUPPORTED,
            "unsupported_family",
        )
    if candidate_semantic_space:
        return ParserResolverRoutingDecision(
            ParserResolverRoutingOutcome.RESOLVER_ELIGIBLE,
            "bounded_supported_relation",
            candidate_semantic_space,
        )
    if _has_unsupported_preference_ordering(candidate_semantic_space, unsupported_evidence):
        return ParserResolverRoutingDecision(
            ParserResolverRoutingOutcome.HARD_UNRESOLVED_BLOCKING,
            "unsupported_scope",
        )
    if any(not _parser_residue_is_non_blocking(item.source_text) for item in unsupported_evidence):
        if _has_unsupported_preference_ordering(candidate_semantic_space, unsupported_evidence):
            return ParserResolverRoutingDecision(
                ParserResolverRoutingOutcome.HARD_UNRESOLVED_BLOCKING,
                "unsupported_scope",
            )
        return ParserResolverRoutingDecision(
            ParserResolverRoutingOutcome.HARD_UNRESOLVED_BLOCKING,
            "explicit_hard_unresolved",
        )
    reason = "known_negated_no_positive_preference" if _has_negated_price_preference_context(
        _compact_parser_evidence_text(unsupported_evidence)
    ) else "unsupported_family"
    return ParserResolverRoutingDecision(
        ParserResolverRoutingOutcome.NON_BLOCKING_UNSUPPORTED,
        reason,
    )


def build_patch_resolver_request(ir: PatchSemanticIR, source_input: str) -> SemanticResolverRequest:
    return SemanticResolverRequest(
        request_id=_request_id("patch", source_input),
        contract_version=SEMANTIC_RESOLVER_CONTRACT_VERSION,
        task_kind=SemanticResolverTaskKind.PATCH,
        evidence=tuple(
            SemanticResolverEvidence(
                item.evidence_id,
                item.kind.value,
                item.source_text,
                item.normalized_text,
            )
            for item in ir.evidence
        ),
        unresolved_question=_issue_text(tuple(item.message for item in ir.ambiguities)),
        allowed_output_vocabulary=PATCH_OUTPUT_VOCABULARY,
        deterministic_context=(("front_half", "M8-U6H-A"),),
        trace_metadata=ir.interpreter_metadata,
    )


def build_parser_resolver_request(ir: ParserSemanticIR, source_input: str) -> SemanticResolverRequest:
    routing = evaluate_parser_resolver_routing(ir)
    return SemanticResolverRequest(
        request_id=_request_id("parser", source_input),
        contract_version=SEMANTIC_RESOLVER_CONTRACT_VERSION,
        task_kind=SemanticResolverTaskKind.PARSER,
        evidence=tuple(
            SemanticResolverEvidence(
                item.evidence_id,
                item.kind.value,
                item.source_text,
                item.normalized_text,
            )
            for item in ir.evidence
        ),
        unresolved_question=_issue_text(tuple(item.message for item in ir.issues)),
        allowed_output_vocabulary=PARSER_OUTPUT_VOCABULARY,
        deterministic_context=(
            ("front_half", "M8-U6H-B"),
            ("resolver_routing_outcome", routing.outcome.value),
            ("resolver_routing_reason", routing.reason),
            ("candidate_semantic_space", ",".join(routing.candidate_semantic_space) or "NONE"),
            *_parser_resolved_binding_context(ir),
        ),
        trace_metadata=ir.interpreter_metadata,
    )


def parse_semantic_resolver_response(
    payload: dict[str, Any], request: SemanticResolverRequest
) -> SemanticResolverResult:
    unknown = frozenset(payload) - _STRICT_RESPONSE_FIELDS
    if unknown:
        return _contract_failure("UNKNOWN_RESPONSE_FIELD", f"Unknown response fields: {sorted(unknown)}")
    missing = _STRICT_RESPONSE_FIELDS - frozenset(payload)
    if missing:
        return _contract_failure("MISSING_RESPONSE_FIELD", f"Missing response fields: {sorted(missing)}")
    if payload["request_id"] != request.request_id:
        return _contract_failure("REQUEST_ID_MISMATCH", "Resolver response request_id does not match request")
    try:
        status = SemanticResolverStatus(payload["status"])
    except ValueError:
        return _contract_failure("INVALID_STATUS", "Resolver response status is not allowed")
    relations_result = _relations_from_payload(payload["relations"], request)
    if relations_result.failure is not None:
        return relations_result
    unresolved_result = _unresolved_from_payload(payload["unresolved_items"], request)
    if unresolved_result.failure is not None:
        return unresolved_result
    diagnostics = payload["diagnostics"]
    if not isinstance(diagnostics, list) or not all(isinstance(item, str) for item in diagnostics):
        return _contract_failure("INVALID_DIAGNOSTICS", "diagnostics must be a list of strings")
    metadata_result = _metadata_from_payload(payload["model_metadata"])
    if isinstance(metadata_result, SemanticResolverFailure):
        return SemanticResolverResult.failed(metadata_result)
    try:
        response = SemanticResolverResponse(
            request_id=request.request_id,
            status=status,
            relations=relations_result.response.relations if relations_result.response else (),
            unresolved_items=unresolved_result.response.unresolved_items
            if unresolved_result.response
            else (),
            diagnostics=tuple(diagnostics),
            model_metadata=metadata_result,
        )
    except ValueError as exc:
        return _contract_failure("INVALID_RESPONSE_SHAPE", str(exc))
    return _validate_evidence_closed(response, request)


def _parser_resolved_binding_context(ir: ParserSemanticIR) -> tuple[tuple[str, str], ...]:
    return tuple(
        (f"resolved_parser_target_{index}", f"{binding.target.value}:{binding.candidate_type.value if binding.candidate_type else 'UNKNOWN'}")
        for index, binding in enumerate(
            (item for item in ir.bindings if item.state is ParserBindingState.RESOLVED),
            start=1,
        )
    )


def resolve_patch_semantics(
    ir: PatchSemanticIR,
    current: RequirementState,
    source_input: str,
    resolver: SemanticResolver,
) -> tuple[PatchSemanticIR, PatchRequirementProposal, SemanticResolverResult | None]:
    from flight_agent.application.requirement_patch_hybrid import SemanticIRToPatchProposalBuilder

    builder = SemanticIRToPatchProposalBuilder()
    if not should_call_semantic_resolver(ir):
        return ir, builder.build(ir, current, source_input), None
    request = build_patch_resolver_request(ir, source_input)
    result = resolver.resolve(request)
    resolved_ir = _patch_ir_from_result(ir, result)
    proposal = builder.build(resolved_ir, current, source_input)
    return resolved_ir, proposal, result


def resolve_parser_semantics(
    ir: ParserSemanticIR,
    source_input: str,
    resolver: SemanticResolver,
) -> tuple[ParserSemanticIR, InitialRequirementProposal, SemanticResolverResult | None]:
    builder = DeterministicInitialProposalBuilder()
    routing = evaluate_parser_resolver_routing(ir)
    if not routing.should_call:
        if routing.should_progress_without_resolver:
            resolved_ir = _resolved_parser_ir_from_existing_bindings(ir)
            return resolved_ir, builder.build(resolved_ir, source_input), None
        if routing.reason == "unsupported_scope":
            unresolved_ir = replace(
                ir,
                interpretation_status=ParserInterpretationStatus.CLARIFICATION_REQUIRED,
                issues=(
                    *ir.issues,
                    _parser_issue(
                        "UNSUPPORTED_SCOPE",
                        "Unsupported preference ordering remains outside CA04 authority",
                    ),
                ),
            )
            return unresolved_ir, builder.build(unresolved_ir, source_input), None
        return ir, builder.build(ir, source_input), None
    request = build_parser_resolver_request(ir, source_input)
    result = resolver.resolve(request)
    resolved_ir = _parser_ir_from_result(ir, result)
    return resolved_ir, builder.build(resolved_ir, source_input), result


def _relations_from_payload(
    raw_relations: object, request: SemanticResolverRequest
) -> SemanticResolverResult:
    if not isinstance(raw_relations, list):
        return _contract_failure("INVALID_RELATIONS", "relations must be a list")
    relations: list[SemanticResolverRelation] = []
    seen: set[tuple[str, tuple[str, ...], str | None, str | None]] = set()
    for raw in raw_relations:
        if not isinstance(raw, dict):
            return _contract_failure("INVALID_RELATION", "relation must be an object")
        unknown = frozenset(raw) - _STRICT_RELATION_FIELDS
        if unknown:
            return _contract_failure("UNKNOWN_RELATION_FIELD", f"Unknown relation fields: {sorted(unknown)}")
        missing = {"relation_kind", "evidence_ids"} - frozenset(raw)
        if missing:
            return _contract_failure("MISSING_RELATION_FIELD", f"Missing relation fields: {sorted(missing)}")
        relation_kind = raw["relation_kind"]
        if relation_kind not in request.allowed_output_vocabulary:
            return _contract_failure("OUT_OF_VOCABULARY", "relation_kind is outside request vocabulary")
        evidence_ids = raw["evidence_ids"]
        if not isinstance(evidence_ids, list) or not all(isinstance(item, str) for item in evidence_ids):
            return _contract_failure("INVALID_RELATION_EVIDENCE", "relation evidence_ids must be strings")
        target = raw.get("target")
        value = raw.get("value")
        importance_result = _importance_from_payload(raw.get("importance"))
        if isinstance(importance_result, SemanticResolverFailure):
            return SemanticResolverResult.failed(importance_result)
        importance = importance_result
        confidence_result = _confidence_from_payload(raw.get("confidence"))
        if isinstance(confidence_result, SemanticResolverFailure):
            return SemanticResolverResult.failed(confidence_result)
        confidence = confidence_result
        if target is not None and not isinstance(target, str):
            return _contract_failure("INVALID_TARGET", "relation target must be a string")
        if value is not None and not isinstance(value, str):
            return _contract_failure("INVALID_VALUE", "relation value must be a string")
        key = (relation_kind, tuple(evidence_ids), target, value)
        if key in seen:
            return _contract_failure("DUPLICATE_RELATION", "duplicate semantic relation returned")
        seen.add(key)
        try:
            relations.append(
                SemanticResolverRelation(
                    relation_kind,
                    tuple(evidence_ids),
                    target=target,
                    value=value,
                    importance=importance,
                    confidence=confidence,
                )
            )
        except ValueError as exc:
            return _contract_failure("INVALID_RELATION", str(exc))
    return SemanticResolverResult.success(
        SemanticResolverResponse(
            request.request_id,
            SemanticResolverStatus.RESOLVED if relations else SemanticResolverStatus.MODEL_FAILURE,
            relations=tuple(relations) if relations else (),
        )
    )


def _unresolved_from_payload(
    raw_items: object, request: SemanticResolverRequest
) -> SemanticResolverResult:
    if not isinstance(raw_items, list):
        return _contract_failure("INVALID_UNRESOLVED_ITEMS", "unresolved_items must be a list")
    items: list[SemanticResolverUnresolvedItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            return _contract_failure("INVALID_UNRESOLVED_ITEM", "unresolved item must be an object")
        unknown = frozenset(raw) - _STRICT_UNRESOLVED_FIELDS
        if unknown:
            return _contract_failure("UNKNOWN_UNRESOLVED_FIELD", f"Unknown unresolved fields: {sorted(unknown)}")
        if not {"code", "message", "evidence_ids"}.issubset(raw):
            return _contract_failure("MISSING_UNRESOLVED_FIELD", "unresolved item is missing required fields")
        evidence_ids = raw["evidence_ids"]
        if not isinstance(evidence_ids, list) or not all(isinstance(item, str) for item in evidence_ids):
            return _contract_failure("INVALID_UNRESOLVED_EVIDENCE", "unresolved evidence_ids must be strings")
        try:
            items.append(
                SemanticResolverUnresolvedItem(
                    str(raw["code"]),
                    str(raw["message"]),
                    tuple(evidence_ids),
                )
            )
        except ValueError as exc:
            return _contract_failure("INVALID_UNRESOLVED_ITEM", str(exc))
    return SemanticResolverResult.success(
        SemanticResolverResponse(
            request.request_id,
            SemanticResolverStatus.AMBIGUOUS,
            unresolved_items=tuple(items),
        )
    )


def _metadata_from_payload(raw_metadata: object) -> tuple[tuple[str, str], ...] | SemanticResolverFailure:
    if not isinstance(raw_metadata, list):
        return _failure(SemanticResolverFailureKind.MODEL_CONTRACT, "INVALID_METADATA", "model_metadata must be a list")
    metadata: list[tuple[str, str]] = []
    for raw in raw_metadata:
        if not isinstance(raw, dict):
            return _failure(SemanticResolverFailureKind.MODEL_CONTRACT, "INVALID_METADATA", "metadata item must be an object")
        unknown = frozenset(raw) - _STRICT_METADATA_FIELDS
        if unknown or not _STRICT_METADATA_FIELDS.issubset(raw):
            return _failure(SemanticResolverFailureKind.MODEL_CONTRACT, "INVALID_METADATA", "metadata item fields are invalid")
        if not isinstance(raw["key"], str) or not isinstance(raw["value"], str):
            return _failure(SemanticResolverFailureKind.MODEL_CONTRACT, "INVALID_METADATA", "metadata item values must be strings")
        metadata.append((raw["key"], raw["value"]))
    return tuple(metadata)


def _validate_evidence_closed(
    response: SemanticResolverResponse, request: SemanticResolverRequest
) -> SemanticResolverResult:
    known_ids = frozenset(item.evidence_id for item in request.evidence)
    known_evidence = {item.evidence_id: item for item in request.evidence}
    known_text = frozenset(
        text
        for evidence in request.evidence
        for text in (evidence.source_text, evidence.normalized_text)
        if text is not None
    )
    for relation in response.relations:
        if not frozenset(relation.evidence_ids).issubset(known_ids):
            return _evidence_failure("UNKNOWN_EVIDENCE_ID", "Resolver referenced evidence outside request closure")
        relation_evidence = tuple(known_evidence[evidence_id] for evidence_id in relation.evidence_ids)
        relation_failure = _validate_relation_payload(relation, relation_evidence, request.evidence, known_text, request)
        if relation_failure is not None:
            return relation_failure
        authorized_targets = (
            _AUTHORIZED_PATCH_RELATION_TARGETS
            if request.task_kind is SemanticResolverTaskKind.PATCH
            else _AUTHORIZED_PARSER_RELATION_TARGETS
        )
        if relation.target is not None and relation.target not in authorized_targets:
            return _evidence_failure("OUT_OF_VOCABULARY_TARGET", "Resolver target is outside authorized semantic targets")
        if (
            relation.value is not None
            and relation.value not in known_text
            and relation.value not in request.allowed_output_vocabulary
            and not _value_is_authorized_by_relation(relation)
        ):
            return _evidence_failure("INVENTED_VALUE", "Resolver returned a value not present in deterministic evidence")
        if relation.value is not None and not _value_is_authorized_by_relation(relation) and _looks_like_invented_atomic_fact(relation.value, known_text):
            return _evidence_failure("INVENTED_ATOMIC_FACT", "Resolver invented an atomic semantic fact")
    for item in response.unresolved_items:
        if not frozenset(item.evidence_ids).issubset(known_ids):
            return _evidence_failure("UNKNOWN_UNRESOLVED_EVIDENCE_ID", "Unresolved item referenced unknown evidence")
    return SemanticResolverResult.success(response)


def _patch_ir_from_result(ir: PatchSemanticIR, result: SemanticResolverResult) -> PatchSemanticIR:
    if result.failure is not None:
        return replace(
            ir,
            disposition=ResolutionDisposition.CLARIFICATION_REQUIRED,
            ambiguities=(*ir.ambiguities, _patch_issue(result.failure.code, result.failure.message)),
        )
    response = result.response
    if response is None or response.status is not SemanticResolverStatus.RESOLVED:
        return replace(
            ir,
            disposition=ResolutionDisposition.CLARIFICATION_REQUIRED,
            ambiguities=(
                *ir.ambiguities,
                _patch_issue(response.status.value if response else "MODEL_FAILURE", "Semantic resolver did not resolve authoritatively"),
            ),
        )
    mutations: list[SemanticMutation] = []
    for relation in response.relations:
        if relation.relation_kind == "RELAX_MAX_STOPS_TO_ONE":
            mutations.append(
                SemanticMutation(
                    SemanticTarget.MAX_STOPS,
                    SemanticOperation.SET,
                    value=StopCount(1),
                    importance_signal=SemanticImportanceSignal.HARD,
                    evidence_ids=relation.evidence_ids,
                )
            )
        elif relation.relation_kind == "CONVERT_HARD_DIRECT_TO_SOFT_FEWER_STOPS":
            mutations.extend(
                (
                    SemanticMutation(
                        SemanticTarget.MAX_STOPS,
                        SemanticOperation.REMOVE,
                        importance_signal=SemanticImportanceSignal.HARD,
                        evidence_ids=relation.evidence_ids,
                    ),
                    SemanticMutation(
                        SemanticTarget.FEWER_STOPS,
                        SemanticOperation.SET,
                        importance_signal=SemanticImportanceSignal.SOFT,
                        evidence_ids=relation.evidence_ids,
                    ),
                )
            )
        elif relation.relation_kind in {
            "PRICE_RELAXATION_LOWER_PRIORITY_THAN_DIRECT",
            "NO_AUTHORITATIVE_MUTATION",
        }:
            continue
        elif relation.relation_kind == "ADD_SOFT_FEWER_STOPS_PREFERENCE":
            mutations.append(
                SemanticMutation(
                    SemanticTarget.FEWER_STOPS,
                    SemanticOperation.SET,
                    importance_signal=SemanticImportanceSignal.SOFT,
                    preference_importance=_parser_preference_importance(relation.importance),
                    allow_add=False,
                    evidence_ids=relation.evidence_ids,
                )
            )
        elif relation.relation_kind == "ADD_SOFT_PRICE_PREFERENCE":
            mutations.append(
                SemanticMutation(
                    SemanticTarget.PRICE,
                    SemanticOperation.SET,
                    importance_signal=SemanticImportanceSignal.SOFT,
                    preference_importance=_parser_preference_importance(relation.importance),
                    allow_add=False,
                    evidence_ids=relation.evidence_ids,
                )
            )
        elif relation.relation_kind == "REMOVE_SOFT_PREFERENCE":
            target = SemanticTarget.PRICE if relation.target == ParserSemanticTarget.PRICE.value else SemanticTarget.FEWER_STOPS
            mutations.append(
                SemanticMutation(
                    target,
                    SemanticOperation.REMOVE,
                    importance_signal=SemanticImportanceSignal.SOFT,
                    allow_add=False,
                    evidence_ids=relation.evidence_ids,
                )
            )
    resolved = replace(ir, disposition=ResolutionDisposition.RESOLVED, mutations=tuple(mutations), ambiguities=())
    return PatchInterpretationRouter().route(MutationConsolidator().consolidate(resolved))


def _parser_ir_from_result(ir: ParserSemanticIR, result: SemanticResolverResult) -> ParserSemanticIR:
    if result.failure is not None:
        if _parser_ir_can_progress_with_non_blocking_residue(ir):
            return _resolved_parser_ir_from_existing_bindings(ir)
        return replace(
            ir,
            interpretation_status=ParserInterpretationStatus.CLARIFICATION_REQUIRED,
            issues=(*ir.issues, _parser_issue(result.failure.code, result.failure.message)),
        )
    response = result.response
    if response is None or response.status is not SemanticResolverStatus.RESOLVED:
        if _parser_ir_can_progress_with_non_blocking_residue(ir):
            return _resolved_parser_ir_from_existing_bindings(ir)
        return replace(
            ir,
            interpretation_status=ParserInterpretationStatus.CLARIFICATION_REQUIRED,
            issues=(
                *ir.issues,
                _parser_issue(response.status.value if response else "MODEL_FAILURE", "Semantic resolver did not resolve authoritatively"),
            ),
        )
    if _parser_ir_has_conditional_tradeoff(ir):
        return replace(
            ir,
            interpretation_status=ParserInterpretationStatus.CLARIFICATION_REQUIRED,
            issues=(
                *ir.issues,
                _parser_issue("CONDITIONAL_TRADEOFF_OUT_OF_SCOPE", "Conditional tradeoff remains outside parser resolver authority"),
            ),
        )
    binding_relations = {
        "ADD_SOFT_FEWER_STOPS_PREFERENCE",
        "ADD_SOFT_PRICE_PREFERENCE",
        "ADD_HARD_MAX_PRICE_CONSTRAINT",
        "ADD_HARD_MAX_STOPS_CONSTRAINT",
    }
    allowed_parser_relations = {
        "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION",
        "NO_AUTHORITATIVE_BINDING",
        *binding_relations,
    }
    if all(relation.relation_kind in allowed_parser_relations for relation in response.relations):
        semantic_bindings = [binding for binding in ir.bindings if binding.state is ParserBindingState.RESOLVED]
        existing_targets = {binding.target for binding in semantic_bindings}
        for relation in response.relations:
            binding = _parser_binding_from_relation(relation, existing_targets)
            if binding is None:
                continue
            semantic_bindings.append(binding)
            existing_targets.add(binding.target)
        if semantic_bindings or any(relation.relation_kind in {"ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION", "NO_AUTHORITATIVE_BINDING"} for relation in response.relations):
            return replace(ir, interpretation_status=ParserInterpretationStatus.RESOLVED, issues=(), bindings=tuple(semantic_bindings))
    if _parser_ir_can_progress_with_non_blocking_residue(ir):
        return _resolved_parser_ir_from_existing_bindings(ir)
    non_binding_relations = {"ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION", "NO_AUTHORITATIVE_BINDING"}
    if all(relation.relation_kind in non_binding_relations for relation in response.relations):
        return replace(
            ir,
            interpretation_status=ParserInterpretationStatus.RESOLVED,
            issues=(),
            bindings=tuple(binding for binding in ir.bindings if binding.state is ParserBindingState.RESOLVED),
        )
    return replace(ir, interpretation_status=ParserInterpretationStatus.CLARIFICATION_REQUIRED)


def _resolved_parser_ir_from_existing_bindings(ir: ParserSemanticIR) -> ParserSemanticIR:
    return replace(
        ir,
        interpretation_status=ParserInterpretationStatus.RESOLVED,
        issues=(),
        bindings=tuple(binding for binding in ir.bindings if binding.state is ParserBindingState.RESOLVED),
    )


def _parser_ir_can_progress_with_non_blocking_residue(ir: ParserSemanticIR) -> bool:
    if _parser_ir_has_conditional_tradeoff(ir):
        return False
    if any(slot.state is not ParserBindingState.RESOLVED for slot in ir.required_slots):
        return False
    if any(binding.state is not ParserBindingState.RESOLVED for binding in ir.bindings):
        return False
    unsupported_evidence = tuple(item for item in ir.evidence if item.kind is ParserEvidenceKind.UNSUPPORTED_TEXT)
    if not unsupported_evidence:
        return False
    return all(_parser_residue_is_non_blocking(item.source_text) for item in unsupported_evidence)


def _parser_residue_is_non_blocking(source_text: str) -> bool:
    compact = re.sub(r"[\s，,。；;、.!?！？]+", "", source_text)
    if not compact:
        return True
    if _looks_like_unsupported_preference_ordering(compact):
        return False
    if any(token in compact for token in ("必须", "只能", "只接受", "不能", "别高于", "不超过", "最多", "上限", "预算")):
        return False
    return not any(token in compact for token in ("如果", "但如果", "再从", "或者", "都可以"))


def _parser_candidate_semantic_space(
    evidence: tuple[ParserSemanticEvidence, ...],
) -> tuple[str, ...]:
    compact = _compact_parser_evidence_text(evidence)
    candidates: list[str] = []
    if _supports_parser_soft_fewer_stops_candidate(compact):
        candidates.append("ADD_SOFT_FEWER_STOPS_PREFERENCE")
    if _supports_parser_soft_price_candidate(compact):
        candidates.append("ADD_SOFT_PRICE_PREFERENCE")
    if _supports_parser_hard_price_candidate(compact):
        candidates.append("ADD_HARD_MAX_PRICE_CONSTRAINT")
    if _supports_parser_hard_stops_candidate(compact):
        candidates.append("ADD_HARD_MAX_STOPS_CONSTRAINT")
    if _supports_parser_complex_ack_candidate(compact):
        candidates.append("ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION")
    return tuple(dict.fromkeys(candidates))


def _compact_parser_evidence_text(evidence: tuple[ParserSemanticEvidence, ...]) -> str:
    return "".join(
        text
        for item in evidence
        for text in (item.source_text, item.normalized_text)
        if text is not None
    )


def _supports_parser_soft_fewer_stops_candidate(compact: str) -> bool:
    if any(token in compact for token in ("不要求直飞", "不一定非要直飞")) and not any(
        marker in compact for marker in ("更喜欢直飞", "优先直飞", "直飞优先", "最好直飞", "最好不要转机")
    ):
        return False
    return _has_ca04_fewer_stops_importance_candidate(compact) or any(
        token in compact
        for token in (
            "更喜欢直飞",
            "优先直飞",
            "直飞优先",
            "最好不要转机",
            "少转几次比较好",
            "转机越少越好",
        )
    )


def _supports_parser_soft_price_candidate(compact: str) -> bool:
    return not _has_negated_price_preference_context(compact) and (
        _has_explicit_soft_price_phrase(compact) or _has_ca04_price_importance_candidate(compact)
    )


def _supports_parser_hard_price_candidate(compact: str) -> bool:
    return bool(re.search(r"\d", compact)) and any(token in compact for token in ("预算", "封顶", "别超过", "不超过", "别高于", "上限"))


def _supports_parser_hard_stops_candidate(compact: str) -> bool:
    return any(token in compact for token in ("不能转机", "不转机", "最多转一次", "最多允许一次中转"))


def _supports_parser_complex_ack_candidate(compact: str) -> bool:
    if "别太早" in compact and any(token in compact for token in ("越便宜越好", "价格越低越好", "便宜")):
        return True
    return "如果" in compact and any(token in compact for token in ("直飞", "便宜", "转一次"))


def _has_ca04_price_importance_candidate(compact: str) -> bool:
    if any(token in compact for token in ("预算", "封顶", "别超过", "不超过", "别高于", "上限", "以内")):
        return False
    return any(token in compact for token in ("价格", "票价", "便宜")) and _has_ca04_importance_marker(compact)


def _has_ca04_fewer_stops_importance_candidate(compact: str) -> bool:
    return any(token in compact for token in ("直飞", "转机", "中转", "少转", "转不转")) and _has_ca04_importance_marker(
        compact
    )


def _has_ca04_importance_marker(compact: str) -> bool:
    return any(
        token in compact
        for token in (
            "最重要",
            "第一优先",
            "最看重",
            "核心考虑",
            "主要考虑",
            "重点考虑",
            "非常看重",
            "非常重要",
            "很重要",
            "更重要",
            "其次",
            "第二考虑",
            "次要考虑",
            "次要",
            "一般重要",
            "比较重要",
            "稍微考虑",
            "有更好",
            "不太重要",
            "不那么重要",
            "只稍微",
        )
    )


def _has_unsupported_preference_ordering(
    candidate_semantic_space: tuple[str, ...],
    evidence: tuple[ParserSemanticEvidence, ...],
) -> bool:
    if candidate_semantic_space:
        return False
    return _looks_like_unsupported_preference_ordering(_compact_parser_evidence_text(evidence))


def _looks_like_unsupported_preference_ordering(compact: str) -> bool:
    return (
        any(token in compact for token in ("按这个顺序", "按此顺序", "排序", "优先级"))
        and any(token in compact for token in ("价格", "票价", "便宜"))
        and any(token in compact for token in ("直飞", "转机", "中转", "少转"))
        and any(token in compact for token in ("出发时间", "起飞时间", "时间"))
    )


def _looks_like_invented_atomic_fact(value: str, known_text: frozenset[str]) -> bool:
    if value in known_text:
        return False
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", value) or re.fullmatch(r"[A-Z]{3}", value) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def _parser_ir_has_conditional_tradeoff(ir: ParserSemanticIR) -> bool:
    compact = "".join(item.source_text for item in ir.evidence)
    return "但如果" in compact or "如果" in compact


_AUTHORIZED_PARSER_RELATION_TARGETS = frozenset(
    target.value
    for target in (
        ParserSemanticTarget.FEWER_STOPS,
        ParserSemanticTarget.PRICE,
        ParserSemanticTarget.MAX_PRICE,
        ParserSemanticTarget.MAX_STOPS,
    )
)
_AUTHORIZED_PATCH_RELATION_TARGETS = frozenset(
    target.value for target in (ParserSemanticTarget.FEWER_STOPS, ParserSemanticTarget.PRICE)
)


def _validate_relation_payload(
    relation: SemanticResolverRelation,
    evidence: tuple[SemanticResolverEvidence, ...],
    request_evidence: tuple[SemanticResolverEvidence, ...],
    known_text: frozenset[str],
    request: SemanticResolverRequest,
) -> SemanticResolverResult | None:
    if request.task_kind is SemanticResolverTaskKind.PATCH:
        return _validate_patch_relation_payload(relation, evidence)
    return _validate_parser_relation_payload(relation, evidence, request_evidence, known_text)


def _validate_patch_relation_payload(
    relation: SemanticResolverRelation,
    evidence: tuple[SemanticResolverEvidence, ...],
) -> SemanticResolverResult | None:
    if relation.relation_kind == "REMOVE_SOFT_PREFERENCE":
        if relation.target not in _AUTHORIZED_PATCH_RELATION_TARGETS:
            return _evidence_failure(
                "UNAUTHORIZED_REMOVE_SOFT_PREFERENCE_TARGET",
                "REMOVE_SOFT_PREFERENCE target must be PRICE or FEWER_STOPS",
            )
        if relation.value is not None:
            return _evidence_failure(
                "UNAUTHORIZED_REMOVE_SOFT_PREFERENCE_VALUE",
                "REMOVE_SOFT_PREFERENCE must not carry model-controlled value",
            )
        if relation.importance is not None:
            return _contract_failure(
                "UNAUTHORIZED_IMPORTANCE_FIELD",
                "REMOVE_SOFT_PREFERENCE must not carry preference importance",
            )
        if not _supports_soft_preference_removal_relation(relation, evidence):
            return _evidence_failure(
                "INSUFFICIENT_SOFT_PREFERENCE_REMOVAL_EVIDENCE",
                "REMOVE_SOFT_PREFERENCE requires explicit no-preference/removal evidence for its target",
            )
        return None
    if relation.importance is not None and relation.relation_kind not in _CA04_IMPORTANCE_RELATION_KINDS:
        return _contract_failure(
            "UNAUTHORIZED_IMPORTANCE_FIELD",
            "Preference importance is authorized only for CA04 soft preference relations",
        )
    if relation.relation_kind == "ADD_SOFT_FEWER_STOPS_PREFERENCE":
        importance_failure = _validate_soft_preference_importance(relation, evidence)
        if importance_failure is not None:
            return importance_failure
        if relation.target not in {None, ParserSemanticTarget.FEWER_STOPS.value} or relation.value is not None:
            return _evidence_failure(
                "UNAUTHORIZED_SOFT_PREFERENCE_PAYLOAD",
                "Soft FEWER_STOPS patch relation must not carry model-controlled value",
            )
        if not _supports_soft_fewer_stops_relation(evidence) and not _supports_fewer_stops_importance_target(evidence):
            return _evidence_failure(
                "INSUFFICIENT_SOFT_PREFERENCE_EVIDENCE",
                "Soft FEWER_STOPS patch relation requires explicit fewer-stops preference evidence",
            )
    if relation.relation_kind == "ADD_SOFT_PRICE_PREFERENCE":
        importance_failure = _validate_soft_preference_importance(relation, evidence)
        if importance_failure is not None:
            return importance_failure
        if relation.target not in {None, ParserSemanticTarget.PRICE.value} or relation.value is not None:
            return _evidence_failure(
                "UNAUTHORIZED_SOFT_PRICE_PAYLOAD",
                "Soft PRICE patch relation must not carry model-controlled value",
            )
        if not _supports_soft_price_relation(evidence) and not _supports_price_importance_target(evidence):
            return _evidence_failure(
                "INSUFFICIENT_SOFT_PRICE_EVIDENCE",
                "Soft PRICE patch relation requires explicit price preference evidence",
            )
    return None


def _validate_parser_relation_payload(
    relation: SemanticResolverRelation,
    evidence: tuple[SemanticResolverEvidence, ...],
    request_evidence: tuple[SemanticResolverEvidence, ...],
    known_text: frozenset[str],
) -> SemanticResolverResult | None:
    if relation.relation_kind == "REMOVE_SOFT_PREFERENCE":
        return _contract_failure(
            "PATCH_ONLY_RELATION",
            "REMOVE_SOFT_PREFERENCE is authorized only for PATCH resolver tasks",
        )
    if relation.importance is not None and relation.relation_kind not in _CA04_IMPORTANCE_RELATION_KINDS:
        return _contract_failure(
            "UNAUTHORIZED_IMPORTANCE_FIELD",
            "Preference importance is authorized only for CA04 soft preference relations",
        )
    if relation.relation_kind == "ADD_SOFT_FEWER_STOPS_PREFERENCE":
        importance_failure = _validate_soft_preference_importance(relation, evidence)
        if importance_failure is not None:
            return importance_failure
        if relation.target not in {None, ParserSemanticTarget.FEWER_STOPS.value} or relation.value is not None:
            return _evidence_failure(
                "UNAUTHORIZED_SOFT_PREFERENCE_PAYLOAD",
                "Soft FEWER_STOPS parser relation must not carry model-controlled value",
            )
        if not _supports_soft_fewer_stops_relation(evidence) and not _supports_fewer_stops_importance_target(evidence):
            return _evidence_failure(
                "INSUFFICIENT_SOFT_PREFERENCE_EVIDENCE",
                "Soft FEWER_STOPS parser relation requires explicit fewer-stops preference evidence",
            )
    if relation.relation_kind == "ADD_SOFT_PRICE_PREFERENCE":
        importance_failure = _validate_soft_preference_importance(relation, evidence)
        if importance_failure is not None:
            return importance_failure
        if relation.target not in {None, ParserSemanticTarget.PRICE.value} or relation.value is not None:
            return _evidence_failure(
                "UNAUTHORIZED_SOFT_PRICE_PAYLOAD",
                "Soft PRICE parser relation must not carry model-controlled value",
            )
        if not _supports_soft_price_relation(evidence, request_evidence) and not _supports_price_importance_target(evidence):
            return _evidence_failure(
                "INSUFFICIENT_SOFT_PRICE_EVIDENCE",
                "Soft PRICE parser relation requires explicit price preference evidence",
            )
    if relation.relation_kind == "ADD_HARD_MAX_PRICE_CONSTRAINT":
        if relation.importance is not None:
            return _contract_failure("UNAUTHORIZED_IMPORTANCE_FIELD", "Hard MAX_PRICE relation must not carry preference importance")
        if relation.target not in {None, ParserSemanticTarget.MAX_PRICE.value}:
            return _evidence_failure("UNAUTHORIZED_MAX_PRICE_TARGET", "Hard MAX_PRICE parser relation target is not authorized")
        if relation.value is None or not re.fullmatch(r"\d+(?:\.\d+)?", relation.value):
            return _evidence_failure("INVALID_MAX_PRICE_VALUE", "Hard MAX_PRICE parser relation requires an exact numeric value")
        if relation.value not in known_text:
            return _evidence_failure("INVENTED_MAX_PRICE_VALUE", "Hard MAX_PRICE value must be present in deterministic evidence")
        if not _supports_hard_max_price_relation(evidence):
            return _evidence_failure("INSUFFICIENT_MAX_PRICE_EVIDENCE", "Hard MAX_PRICE parser relation requires explicit ceiling evidence")
    if relation.relation_kind == "ADD_HARD_MAX_STOPS_CONSTRAINT":
        if relation.importance is not None:
            return _contract_failure("UNAUTHORIZED_IMPORTANCE_FIELD", "Hard MAX_STOPS relation must not carry preference importance")
        if relation.target not in {None, ParserSemanticTarget.MAX_STOPS.value}:
            return _evidence_failure("UNAUTHORIZED_MAX_STOPS_TARGET", "Hard MAX_STOPS parser relation target is not authorized")
        if relation.value is None or not re.fullmatch(r"\d+", relation.value):
            return _evidence_failure("INVALID_MAX_STOPS_VALUE", "Hard MAX_STOPS parser relation requires an exact stop-count value")
        if not _supports_hard_max_stops_relation(evidence, request_evidence, relation.value):
            return _evidence_failure("INSUFFICIENT_MAX_STOPS_EVIDENCE", "Hard MAX_STOPS parser relation requires explicit hard stop evidence")
    return None


def _validate_soft_preference_importance(
    relation: SemanticResolverRelation,
    evidence: tuple[SemanticResolverEvidence, ...],
) -> SemanticResolverResult | None:
    if relation.importance is None:
        return None
    if not _supports_preference_importance(relation.importance, evidence):
        return _evidence_failure(
            "INSUFFICIENT_IMPORTANCE_EVIDENCE",
            "Explicit preference importance requires deterministic evidence support",
        )
    return None


def _supports_preference_importance(
    importance: SemanticResolverPreferenceImportance,
    evidence: tuple[SemanticResolverEvidence, ...],
) -> bool:
    compact = _compact_evidence_text(evidence)
    if importance is SemanticResolverPreferenceImportance.HIGH:
        return any(
            token in compact
            for token in (
                "最重要",
                "第一优先",
                "最看重",
                "核心考虑",
                "特别重要",
                "非常重要",
                "很重要",
                "更重要",
                "主要考虑",
                "重点考虑",
                "非常看重",
                "主要",
            )
        )
    if importance is SemanticResolverPreferenceImportance.MEDIUM:
        return any(token in compact for token in ("其次", "第二考虑", "次要", "一般重要", "也重要", "比较重要")) or (
            "比" in compact and "更重要" in compact
        )
    if importance is SemanticResolverPreferenceImportance.LOW:
        return any(token in compact for token in ("稍微考虑", "有更好", "不太重要", "不那么重要", "只稍微"))
    return False


def _supports_soft_preference_removal_relation(
    relation: SemanticResolverRelation,
    evidence: tuple[SemanticResolverEvidence, ...],
) -> bool:
    compact = _compact_evidence_text(evidence)
    no_preference = any(token in compact for token in ("无所谓", "不在意", "不看", "不用考虑", "不用管", "都可以"))
    if not no_preference:
        return False
    if relation.target == ParserSemanticTarget.PRICE.value:
        return any(token in compact for token in ("价格", "票价", "便宜"))
    if relation.target == ParserSemanticTarget.FEWER_STOPS.value:
        return any(token in compact for token in ("直飞", "转机", "中转", "转不转"))
    return False


def _supports_price_importance_target(evidence: tuple[SemanticResolverEvidence, ...]) -> bool:
    compact = _compact_evidence_text(evidence)
    if not any(token in compact for token in ("价格", "票价", "便宜")):
        return False
    return any(
        token in compact
        for token in (
            "最重要",
            "最看重",
            "核心考虑",
            "特别重要",
            "非常重要",
            "很重要",
            "更重要",
            "第一优先",
            "主要考虑",
            "重点考虑",
            "非常看重",
            "主要",
            "其次",
            "第二考虑",
            "次要",
            "一般重要",
            "也重要",
            "比较重要",
            "稍微考虑",
            "有更好",
            "不太重要",
            "不那么重要",
            "只稍微",
        )
    )


def _supports_fewer_stops_importance_target(evidence: tuple[SemanticResolverEvidence, ...]) -> bool:
    compact = _compact_evidence_text(evidence)
    if not any(token in compact for token in ("直飞", "转机", "中转", "少转", "转不转")):
        return False
    return any(
        token in compact
        for token in (
            "最重要",
            "第一优先",
            "最看重",
            "核心考虑",
            "特别重要",
            "非常重要",
            "很重要",
            "更重要",
            "主要考虑",
            "重点考虑",
            "非常看重",
            "主要",
            "其次",
            "第二考虑",
            "次要",
            "一般重要",
            "也重要",
            "比较重要",
            "稍微考虑",
            "有更好",
            "不太重要",
            "不那么重要",
            "只稍微",
        )
    )


def _value_is_authorized_by_relation(relation: SemanticResolverRelation) -> bool:
    return relation.relation_kind in {"ADD_HARD_MAX_PRICE_CONSTRAINT", "ADD_HARD_MAX_STOPS_CONSTRAINT"}


def _parser_binding_from_relation(
    relation: SemanticResolverRelation, existing_targets: set[ParserSemanticTarget]
) -> ParserSemanticBinding | None:
    if relation.relation_kind == "ADD_SOFT_FEWER_STOPS_PREFERENCE":
        return _new_parser_binding(
            ParserSemanticTarget.FEWER_STOPS,
            existing_targets,
            ParserCandidateType.PREFERENCE,
            None,
            relation,
            _parser_preference_importance(relation.importance),
        )
    if relation.relation_kind == "ADD_SOFT_PRICE_PREFERENCE":
        return _new_parser_binding(
            ParserSemanticTarget.PRICE,
            existing_targets,
            ParserCandidateType.PREFERENCE,
            None,
            relation,
            _parser_preference_importance(relation.importance),
        )
    if relation.relation_kind == "ADD_HARD_MAX_PRICE_CONSTRAINT" and relation.value is not None:
        return _new_parser_binding(
            ParserSemanticTarget.MAX_PRICE,
            existing_targets,
            ParserCandidateType.MONEY,
            Money(Decimal(relation.value), "CNY"),
            relation,
        )
    if relation.relation_kind == "ADD_HARD_MAX_STOPS_CONSTRAINT" and relation.value is not None:
        return _new_parser_binding(
            ParserSemanticTarget.MAX_STOPS,
            existing_targets,
            ParserCandidateType.STOP_COUNT,
            StopCount(int(relation.value)),
            relation,
        )
    return None


def _new_parser_binding(
    target: ParserSemanticTarget,
    existing_targets: set[ParserSemanticTarget],
    candidate_type: ParserCandidateType,
    value: object | None,
    relation: SemanticResolverRelation,
    preference_importance: PreferenceImportance = PreferenceImportance.HIGH,
) -> ParserSemanticBinding | None:
    if target in existing_targets:
        return None
    return ParserSemanticBinding(
        target,
        ParserBindingState.RESOLVED,
        candidate_type,
        value=value,
        value_signal=relation.relation_kind,
        evidence_ids=relation.evidence_ids,
        preference_importance=preference_importance,
    )


def _parser_preference_importance(
    importance: SemanticResolverPreferenceImportance | None,
) -> PreferenceImportance:
    if importance is SemanticResolverPreferenceImportance.LOW:
        return PreferenceImportance.LOW
    if importance is SemanticResolverPreferenceImportance.MEDIUM:
        return PreferenceImportance.MEDIUM
    return PreferenceImportance.HIGH


def _confidence_from_payload(value: object) -> float | None | SemanticResolverFailure:
    if value is None:
        return None
    if isinstance(value, bool):
        return _failure(SemanticResolverFailureKind.MODEL_CONTRACT, "INVALID_CONFIDENCE", "relation confidence must be numeric")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return _failure(SemanticResolverFailureKind.MODEL_CONTRACT, "INVALID_CONFIDENCE", "relation confidence must be numeric")
    return _failure(SemanticResolverFailureKind.MODEL_CONTRACT, "INVALID_CONFIDENCE", "relation confidence must be numeric")


def _importance_from_payload(value: object) -> SemanticResolverPreferenceImportance | None | SemanticResolverFailure:
    if value is None:
        return None
    if not isinstance(value, str):
        return _failure(
            SemanticResolverFailureKind.MODEL_CONTRACT,
            "INVALID_IMPORTANCE",
            "relation importance must be LOW, MEDIUM, HIGH, or null",
        )
    try:
        return SemanticResolverPreferenceImportance(value)
    except ValueError:
        return _failure(
            SemanticResolverFailureKind.MODEL_CONTRACT,
            "INVALID_IMPORTANCE",
            "relation importance must be LOW, MEDIUM, HIGH, or null",
        )


def _supports_soft_fewer_stops_relation(evidence: tuple[SemanticResolverEvidence, ...]) -> bool:
    compact = _compact_evidence_text(evidence)
    ambiguous_no_transfer = any(token in compact for token in ("尽量别转机", "我不想转机"))
    if ambiguous_no_transfer:
        return False
    return any(
        token in compact
        for token in (
            "最好直飞",
            "直飞最好",
            "直飞优先",
            "优先直飞",
            "最好不要转机",
            "转机越少越好",
            "中转越少越好",
            "中转次数能少就少",
            "我比较看重少中转",
            "中转不是不行不过越少越好",
            "转机少一点比较好",
            "中转也少一点更好",
            "少转几次比较好",
            "少转",
        )
    ) or ("不要转机" in compact and "最好" in compact) or (
        "直飞" in compact and any(marker in compact for marker in ("更喜欢", "优先", "最好", "偏好", "倾向"))
    )


def _supports_soft_price_relation(
    evidence: tuple[SemanticResolverEvidence, ...],
    request_evidence: tuple[SemanticResolverEvidence, ...] = (),
) -> bool:
    compact = _compact_evidence_text(evidence)
    request_compact = _compact_evidence_text(request_evidence)
    if _has_negated_price_preference_context(compact) or (
        _has_negated_price_preference_context(request_compact) and not _has_explicit_soft_price_phrase(compact)
    ):
        return False
    if any(token in compact for token in ("封顶", "别超过", "不超过", "预算", "以内")):
        return False
    return _has_explicit_soft_price_phrase(compact) or ("便宜" in compact and any(marker in compact for marker in ("优先", "重要", "尽量", "越好")))


def _has_negated_price_preference_context(compact: str) -> bool:
    if any(token in compact for token in ("便宜不是最重要", "价格不是最重要", "不是越便宜越好")):
        return True
    return "不是" in compact and "重要" in compact and any(token in compact for token in ("便宜", "价格", "票价"))


def _has_explicit_soft_price_phrase(compact: str) -> bool:
    return any(
        token in compact
        for token in (
            "价格越便宜越好",
            "越便宜越好",
            "尽量便宜",
            "便宜的优先",
            "便宜优先",
            "票价低一点优先",
            "票价能省一点是一点",
            "我更在意价格低",
            "同等条件下选便宜的",
            "价格越低越好",
            "价格也尽量低",
            "价格低一点更好",
            "价格也重要",
            "便宜也很重要",
        )
    )


def _supports_hard_max_price_relation(evidence: tuple[SemanticResolverEvidence, ...]) -> bool:
    compact = _compact_evidence_text(evidence)
    if "最好控制在" in compact:
        return False
    return bool(re.search(r"\d", compact)) and any(
        marker in compact for marker in ("预算", "以内", "封顶", "别超过", "不超过", "最多花", "别高于", "上限", "不能超过")
    )


def _supports_hard_max_stops_relation(
    evidence: tuple[SemanticResolverEvidence, ...],
    request_evidence: tuple[SemanticResolverEvidence, ...],
    value: str,
) -> bool:
    compact = _compact_evidence_text(evidence)
    request_compact = _compact_evidence_text(request_evidence)
    if "不要转机" in request_compact and "最好" in request_compact:
        return False
    if any(token in compact for token in ("最好不要转机", "尽量别转机", "我不想转机")) or (
        "不要转机" in compact and "最好" in compact
    ):
        return False
    if value == "0":
        return any(token in compact for token in ("必须直飞", "不要转机", "不能转机", "不转机", "只接受直达航班", "只要直飞", "有中转的不要"))
    if value == "1":
        return any(token in compact for token in ("最多转一次", "最多允许一次中转"))
    return False


def _compact_evidence_text(evidence: tuple[SemanticResolverEvidence, ...]) -> str:
    return "".join(
        text
        for item in evidence
        for text in (item.source_text, item.normalized_text)
        if text is not None
    )


def _contract_failure(code: str, message: str) -> SemanticResolverResult:
    return SemanticResolverResult.failed(
        _failure(SemanticResolverFailureKind.MODEL_CONTRACT, code, message)
    )


def _evidence_failure(code: str, message: str) -> SemanticResolverResult:
    return SemanticResolverResult.failed(
        _failure(SemanticResolverFailureKind.EVIDENCE_CLOSURE, code, message)
    )


def _failure(
    kind: SemanticResolverFailureKind, code: str, message: str, retryable: bool = False
) -> SemanticResolverFailure:
    return SemanticResolverFailure(kind, code, message, retryable)


def _patch_issue(code: str, message: str):
    from flight_agent.application.requirement_patch_hybrid import SemanticAmbiguity

    return SemanticAmbiguity(code, message)


def _parser_issue(code: str, message: str):
    from flight_agent.application.requirement_parser_hybrid import ParserSemanticIssue

    return ParserSemanticIssue(code, message)


def _issue_text(issues: tuple[str, ...]) -> str:
    return "; ".join(item for item in issues if item.strip()) or "Resolve complex semantic relation"


def _request_id(prefix: str, source_input: str) -> str:
    stable = abs(hash((prefix, source_input))) % 1_000_000_000
    return f"u6h-c-{prefix}-{stable}"
