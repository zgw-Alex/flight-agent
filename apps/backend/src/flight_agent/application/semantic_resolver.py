"""Application orchestration for the M8-U6H-C semantic resolver."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from flight_agent.application.requirement_parser_hybrid import (
    BindingConsolidator,
    DeterministicInitialBinder,
    DeterministicInitialProposalBuilder,
    ParserBindingState,
    ParserCandidateType,
    ParserEvidenceExtractor,
    ParserInterpretationRouter,
    ParserInterpretationStatus,
    ParserSemanticBinding,
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
from flight_agent.domain.requirements import RequirementState, StopCount
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
    "NO_AUTHORITATIVE_MUTATION",
)
PARSER_OUTPUT_VOCABULARY = (
    "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION",
    "ADD_SOFT_FEWER_STOPS_PREFERENCE",
    "NO_AUTHORITATIVE_BINDING",
)
_STRICT_RESPONSE_FIELDS = frozenset(
    {"request_id", "status", "relations", "unresolved_items", "diagnostics", "model_metadata"}
)
_STRICT_RELATION_FIELDS = frozenset(
    {"relation_kind", "evidence_ids", "target", "value", "confidence"}
)
_STRICT_UNRESOLVED_FIELDS = frozenset({"code", "message", "evidence_ids"})
_STRICT_METADATA_FIELDS = frozenset({"key", "value"})


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
    return ir.interpretation_status is ParserInterpretationStatus.SEMANTIC_RESOLVER_REQUIRED


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
        deterministic_context=(("front_half", "M8-U6H-B"),),
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
    if not should_call_semantic_resolver(ir):
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
        if relation.relation_kind == "ADD_SOFT_FEWER_STOPS_PREFERENCE" and (
            relation.target is not None or relation.value is not None
        ):
            return _evidence_failure(
                "UNAUTHORIZED_SOFT_PREFERENCE_PAYLOAD",
                "Soft FEWER_STOPS parser relation must not carry model-controlled target or value",
            )
        if relation.relation_kind == "ADD_SOFT_FEWER_STOPS_PREFERENCE" and not _supports_soft_fewer_stops_relation(
            tuple(known_evidence[evidence_id] for evidence_id in relation.evidence_ids)
        ):
            return _evidence_failure(
                "INSUFFICIENT_SOFT_PREFERENCE_EVIDENCE",
                "Soft FEWER_STOPS parser relation requires direct-flight evidence with a positive preference signal",
            )
        if relation.target is not None and relation.target not in request.allowed_output_vocabulary:
            return _evidence_failure("OUT_OF_VOCABULARY_TARGET", "Resolver target is outside request vocabulary")
        if relation.value is not None and relation.value not in known_text and relation.value not in request.allowed_output_vocabulary:
            return _evidence_failure("INVENTED_VALUE", "Resolver returned a value not present in deterministic evidence")
        if relation.value is not None and _looks_like_invented_atomic_fact(relation.value, known_text):
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
    resolved = replace(ir, disposition=ResolutionDisposition.RESOLVED, mutations=tuple(mutations), ambiguities=())
    return PatchInterpretationRouter().route(MutationConsolidator().consolidate(resolved))


def _parser_ir_from_result(ir: ParserSemanticIR, result: SemanticResolverResult) -> ParserSemanticIR:
    if result.failure is not None:
        return replace(
            ir,
            interpretation_status=ParserInterpretationStatus.CLARIFICATION_REQUIRED,
            issues=(*ir.issues, _parser_issue(result.failure.code, result.failure.message)),
        )
    response = result.response
    if response is None or response.status is not SemanticResolverStatus.RESOLVED:
        return replace(
            ir,
            interpretation_status=ParserInterpretationStatus.CLARIFICATION_REQUIRED,
            issues=(
                *ir.issues,
                _parser_issue(response.status.value if response else "MODEL_FAILURE", "Semantic resolver did not resolve authoritatively"),
            ),
        )
    soft_preference_relations = tuple(
        relation for relation in response.relations if relation.relation_kind == "ADD_SOFT_FEWER_STOPS_PREFERENCE"
    )
    if soft_preference_relations and all(
        relation.relation_kind
        in {
            "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION",
            "ADD_SOFT_FEWER_STOPS_PREFERENCE",
            "NO_AUTHORITATIVE_BINDING",
        }
        for relation in response.relations
    ):
        existing_resolved = tuple(binding for binding in ir.bindings if binding.state is ParserBindingState.RESOLVED)
        existing_targets = {binding.target for binding in existing_resolved}
        semantic_bindings = existing_resolved
        if ParserSemanticTarget.FEWER_STOPS not in existing_targets:
            evidence_ids = tuple(
                dict.fromkeys(
                    evidence_id
                    for relation in soft_preference_relations
                    for evidence_id in relation.evidence_ids
                )
            )
            semantic_bindings = (
                *semantic_bindings,
                ParserSemanticBinding(
                    ParserSemanticTarget.FEWER_STOPS,
                    ParserBindingState.RESOLVED,
                    ParserCandidateType.PREFERENCE,
                    value=None,
                    value_signal="ADD_SOFT_FEWER_STOPS_PREFERENCE",
                    evidence_ids=evidence_ids,
                ),
            )
        return replace(ir, interpretation_status=ParserInterpretationStatus.RESOLVED, issues=(), bindings=semantic_bindings)
    non_binding_relations = {"ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION", "NO_AUTHORITATIVE_BINDING"}
    if all(relation.relation_kind in non_binding_relations for relation in response.relations):
        return replace(
            ir,
            interpretation_status=ParserInterpretationStatus.RESOLVED,
            issues=(),
            bindings=tuple(binding for binding in ir.bindings if binding.state is ParserBindingState.RESOLVED),
        )
    return replace(ir, interpretation_status=ParserInterpretationStatus.CLARIFICATION_REQUIRED)


def _looks_like_invented_atomic_fact(value: str, known_text: frozenset[str]) -> bool:
    if value in known_text:
        return False
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", value) or re.fullmatch(r"[A-Z]{3}", value) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


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


def _supports_soft_fewer_stops_relation(evidence: tuple[SemanticResolverEvidence, ...]) -> bool:
    compact = "".join(
        text
        for item in evidence
        for text in (item.source_text, item.normalized_text)
        if text is not None
    )
    return "直飞" in compact and any(marker in compact for marker in ("更喜欢", "优先", "最好", "偏好", "倾向"))


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
