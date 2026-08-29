"""Deterministic-first Initial Requirement Parser Hybrid front-half for M8-U6H-B."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import Enum

from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementValue,
    SoftPreference,
    StopCount,
    ValueRange,
    ValueSet,
)
from flight_agent.ports import (
    InitialRequirementProposal,
    InterpreterFailure,
    InterpreterInput,
    InterpreterMode,
    InterpreterResult,
    ProposalEvidence,
    RequirementInterpretationContext,
    SourceSpanHint,
)

ParserConstraintExpression = RequirementValue | ValueRange | ValueSet


class ParserEvidenceKind(str, Enum):
    LOCATION_TEXT = "LOCATION_TEXT"
    DATE_TEXT = "DATE_TEXT"
    VALUE_TEXT = "VALUE_TEXT"
    RELATION_TEXT = "RELATION_TEXT"
    CONSTRAINT_TEXT = "CONSTRAINT_TEXT"
    PREFERENCE_TEXT = "PREFERENCE_TEXT"
    CORRECTION_TEXT = "CORRECTION_TEXT"
    ALTERNATIVE_TEXT = "ALTERNATIVE_TEXT"
    UNSUPPORTED_TEXT = "UNSUPPORTED_TEXT"


class ParserSemanticTarget(str, Enum):
    ORIGIN = "ORIGIN"
    DESTINATION = "DESTINATION"
    DEPARTURE_DATE = "DEPARTURE_DATE"
    MAX_PRICE = "MAX_PRICE"
    MAX_STOPS = "MAX_STOPS"
    PRICE = "PRICE"
    FEWER_STOPS = "FEWER_STOPS"
    TRIP_STRUCTURE = "TRIP_STRUCTURE"


class ParserBindingState(str, Enum):
    RESOLVED = "RESOLVED"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"
    UNSUPPORTED = "UNSUPPORTED"


class ParserInterpretationStatus(str, Enum):
    RESOLVED = "RESOLVED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    SEMANTIC_RESOLVER_REQUIRED = "SEMANTIC_RESOLVER_REQUIRED"
    INVALID = "INVALID"


class ParserCandidateType(str, Enum):
    CITY = "CITY"
    AIRPORT = "AIRPORT"
    DATE = "DATE"
    MONEY = "MONEY"
    STOP_COUNT = "STOP_COUNT"
    PREFERENCE = "PREFERENCE"


@dataclass(frozen=True)
class ParserSourceLocation:
    start: int
    end: int


@dataclass(frozen=True)
class ParserSemanticEvidence:
    evidence_id: str
    kind: ParserEvidenceKind
    source_text: str
    normalized_text: str | None = None
    source_location: ParserSourceLocation | None = None
    extractor_id: str = "parser-evidence-extractor"
    extractor_version: str = "m8-u6h-b-v1"


@dataclass(frozen=True)
class ParserSemanticBinding:
    target: ParserSemanticTarget
    state: ParserBindingState
    candidate_type: ParserCandidateType | None = None
    value: object | None = None
    value_signal: str = ""
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequiredSlotState:
    target: ParserSemanticTarget
    state: ParserBindingState
    evidence_ids: tuple[str, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class ParserSemanticIssue:
    code: str
    message: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParserSemanticIR:
    interpretation_status: ParserInterpretationStatus
    required_slots: tuple[RequiredSlotState, ...] = ()
    bindings: tuple[ParserSemanticBinding, ...] = ()
    issues: tuple[ParserSemanticIssue, ...] = ()
    evidence: tuple[ParserSemanticEvidence, ...] = ()
    interpreter_metadata: tuple[tuple[str, str], ...] = ()


class ParserEvidenceExtractor:
    def extract(self, message: str) -> tuple[ParserSemanticEvidence, ...]:
        evidence: list[ParserSemanticEvidence] = []
        counters: dict[ParserEvidenceKind, int] = {}

        def add(
            kind: ParserEvidenceKind,
            source_text: str,
            start: int,
            normalized_text: str | None = None,
        ) -> None:
            counters[kind] = counters.get(kind, 0) + 1
            evidence.append(
                ParserSemanticEvidence(
                    evidence_id=f"ev-{kind.value.lower().replace('_text', '').replace('_', '-')}-{counters[kind]}",
                    kind=kind,
                    source_text=source_text,
                    normalized_text=normalized_text,
                    source_location=ParserSourceLocation(start, start + len(source_text)),
                )
            )

        for token, code in _LOCATION_ALIASES.items():
            for start in _token_starts(message, token):
                add(ParserEvidenceKind.LOCATION_TEXT, token, start, code)
        for match in re.finditer(r"(?:下周[一二三四五六日天])|(?:\d{1,2}月\d{1,2}日)", message):
            add(ParserEvidenceKind.DATE_TEXT, match.group(0), match.start(), match.group(0))
        for match in re.finditer(r"\d+(?:\.\d+)?", message):
            add(ParserEvidenceKind.VALUE_TEXT, match.group(0), match.start(), match.group(0))
        for token in ("从", "去", "到", "出发", "再从"):
            for start in _token_starts(message, token):
                add(ParserEvidenceKind.RELATION_TEXT, token, start, token)
        for token in ("预算", "以内", "必须直飞", "最多转一次", "不要转机"):
            for start in _token_starts(message, token):
                add(ParserEvidenceKind.CONSTRAINT_TEXT, token, start, token)
        for token in (
            "最好直飞",
            "直飞最好",
            "转机少一点比较好",
            "价格越便宜越好",
            "越便宜越好",
            "便宜也很重要",
            "其他没要求",
        ):
            for start in _token_starts(message, token):
                add(ParserEvidenceKind.PREFERENCE_TEXT, token, start, token)
        for token in ("不对", "不，是", "不是"):
            for start in _token_starts(message, token):
                add(ParserEvidenceKind.CORRECTION_TEXT, token, start, token)
        for token in ("或者", "或", "都可以"):
            for start in _token_starts(message, token):
                add(ParserEvidenceKind.ALTERNATIVE_TEXT, token, start, token)
        for token in ("越便宜越好但别太早", "再从"):
            for start in _token_starts(message, token):
                add(ParserEvidenceKind.UNSUPPORTED_TEXT, token, start, token)
        for start, source_text in _material_semantic_residue_spans(message, tuple(evidence)):
            add(ParserEvidenceKind.UNSUPPORTED_TEXT, source_text, start, source_text)
        return tuple(evidence)


class DeterministicInitialBinder:
    def bind(self, message: str, evidence: tuple[ParserSemanticEvidence, ...]) -> tuple[ParserSemanticBinding, ...]:
        compact = re.sub(r"\s+", "", message)
        bindings: list[ParserSemanticBinding] = []
        bindings.extend(_route_bindings(compact, evidence))
        bindings.extend(_date_bindings(compact, evidence))
        price = _price_binding(message, evidence)
        if price is not None:
            bindings.append(price)
        if "必须直飞" in compact or ("不要转机" in compact and "最好不要转机" not in compact):
            bindings.append(_simple_binding(ParserSemanticTarget.MAX_STOPS, StopCount(0), ParserCandidateType.STOP_COUNT, evidence))
        if "最多转一次" in compact:
            bindings.append(_simple_binding(ParserSemanticTarget.MAX_STOPS, StopCount(1), ParserCandidateType.STOP_COUNT, evidence))
        if any(token in compact for token in ("最好直飞", "直飞最好", "转机少一点比较好")):
            bindings.append(_simple_binding(ParserSemanticTarget.FEWER_STOPS, None, ParserCandidateType.PREFERENCE, evidence))
        if any(token in compact for token in ("价格越便宜越好", "越便宜越好", "便宜也很重要")):
            bindings.append(_simple_binding(ParserSemanticTarget.PRICE, None, ParserCandidateType.PREFERENCE, evidence))
        if "再从" in compact:
            bindings.append(
                ParserSemanticBinding(
                    ParserSemanticTarget.TRIP_STRUCTURE,
                    ParserBindingState.UNSUPPORTED,
                    evidence_ids=_ids_for(evidence, ParserEvidenceKind.UNSUPPORTED_TEXT),
                    value_signal="multi-leg request",
                )
            )
        if "一千多" in compact:
            bindings.append(
                ParserSemanticBinding(
                    ParserSemanticTarget.MAX_PRICE,
                    ParserBindingState.UNSUPPORTED,
                    ParserCandidateType.MONEY,
                    value_signal="inexact numeric budget",
                    evidence_ids=_ids_for(evidence, ParserEvidenceKind.VALUE_TEXT, ParserEvidenceKind.CONSTRAINT_TEXT),
                )
            )
        return tuple(bindings)


class BindingConsolidator:
    def consolidate(
        self,
        bindings: tuple[ParserSemanticBinding, ...],
        evidence: tuple[ParserSemanticEvidence, ...],
    ) -> tuple[ParserSemanticBinding, ...]:
        corrections = _ids_for(evidence, ParserEvidenceKind.CORRECTION_TEXT)
        consolidated: list[ParserSemanticBinding] = []
        for target in ParserSemanticTarget:
            target_bindings = tuple(binding for binding in bindings if binding.target is target)
            if not target_bindings:
                continue
            if any(binding.state is ParserBindingState.UNSUPPORTED for binding in target_bindings):
                consolidated.append(next(binding for binding in target_bindings if binding.state is ParserBindingState.UNSUPPORTED))
                continue
            unique = {repr(binding.value) for binding in target_bindings}
            if len(unique) == 1:
                merged_ids = tuple(dict.fromkeys(eid for binding in target_bindings for eid in binding.evidence_ids))
                consolidated.append(replace(target_bindings[-1], evidence_ids=merged_ids))
            elif corrections:
                consolidated.append(target_bindings[-1])
            else:
                consolidated.append(
                    ParserSemanticBinding(
                        target=target,
                        state=ParserBindingState.CONFLICTING,
                        candidate_type=target_bindings[-1].candidate_type,
                        value_signal="conflicting values",
                        evidence_ids=tuple(eid for binding in target_bindings for eid in binding.evidence_ids),
                    )
                )
        return tuple(consolidated)


class RequiredSlotCompletenessDeriver:
    def derive(self, bindings: tuple[ParserSemanticBinding, ...]) -> tuple[RequiredSlotState, ...]:
        states: list[RequiredSlotState] = []
        for target in (ParserSemanticTarget.ORIGIN, ParserSemanticTarget.DESTINATION, ParserSemanticTarget.DEPARTURE_DATE):
            target_bindings = tuple(binding for binding in bindings if binding.target is target)
            if not target_bindings:
                states.append(RequiredSlotState(target, ParserBindingState.MISSING, message=f"{target.value} is missing"))
                continue
            binding = target_bindings[-1]
            states.append(
                RequiredSlotState(
                    target=target,
                    state=binding.state,
                    evidence_ids=binding.evidence_ids,
                    message="" if binding.state is ParserBindingState.RESOLVED else f"{target.value} is {binding.state.value}",
                )
            )
        return tuple(states)


class ParserInterpretationRouter:
    def route(
        self,
        bindings: tuple[ParserSemanticBinding, ...],
        required_slots: tuple[RequiredSlotState, ...],
        evidence: tuple[ParserSemanticEvidence, ...],
    ) -> ParserSemanticIR:
        issues: list[ParserSemanticIssue] = []
        for slot in required_slots:
            if slot.state is not ParserBindingState.RESOLVED:
                issues.append(ParserSemanticIssue(slot.state.value, slot.message, slot.evidence_ids))
        for binding in bindings:
            if binding.state in {ParserBindingState.AMBIGUOUS, ParserBindingState.CONFLICTING, ParserBindingState.UNSUPPORTED}:
                issues.append(ParserSemanticIssue(binding.state.value, f"{binding.target.value} is {binding.state.value}", binding.evidence_ids))
        if issues:
            return ParserSemanticIR(ParserInterpretationStatus.CLARIFICATION_REQUIRED, required_slots, bindings, tuple(issues), evidence)
        semantic_resolver_evidence_ids = _semantic_resolver_required_evidence_ids(evidence)
        if semantic_resolver_evidence_ids:
            return ParserSemanticIR(
                ParserInterpretationStatus.SEMANTIC_RESOLVER_REQUIRED,
                required_slots,
                bindings,
                (ParserSemanticIssue("SEMANTIC_RESOLVER_REQUIRED", "Complex preference relation requires semantic resolver", semantic_resolver_evidence_ids),),
                evidence,
            )
        return ParserSemanticIR(ParserInterpretationStatus.RESOLVED, required_slots, bindings, (), evidence)


class DeterministicInitialProposalBuilder:
    def build(self, ir: ParserSemanticIR, source_input: str) -> InitialRequirementProposal:
        evidence = _proposal_evidence(ir.evidence)
        if ir.interpretation_status is ParserInterpretationStatus.SEMANTIC_RESOLVER_REQUIRED:
            return InitialRequirementProposal(
                unresolved_semantics=("Semantic resolver required",),
                ambiguity_reasons=("SEMANTIC_RESOLVER_REQUIRED",),
                source_input=source_input,
                evidence=evidence,
            )
        if ir.interpretation_status is not ParserInterpretationStatus.RESOLVED:
            return InitialRequirementProposal(
                unresolved_semantics=tuple(issue.message for issue in ir.issues),
                ambiguity_reasons=tuple(issue.code for issue in ir.issues),
                source_input=source_input,
                evidence=evidence,
            )
        constraints: list[HardConstraint] = []
        preferences: list[SoftPreference] = []
        for binding in ir.bindings:
            item = _proposal_item(binding)
            if isinstance(item, HardConstraint):
                constraints.append(item)
            elif isinstance(item, SoftPreference):
                preferences.append(item)
        return InitialRequirementProposal(
            constraints=tuple(constraints),
            preferences=tuple(preferences),
            source_input=source_input,
            evidence=evidence,
        )


class DeterministicParserHybridInterpreter:
    def __init__(self) -> None:
        self._extractor = ParserEvidenceExtractor()
        self._binder = DeterministicInitialBinder()
        self._consolidator = BindingConsolidator()
        self._deriver = RequiredSlotCompletenessDeriver()
        self._router = ParserInterpretationRouter()
        self._builder = DeterministicInitialProposalBuilder()
        self.last_ir: ParserSemanticIR | None = None

    def interpret(
        self,
        interpreter_input: InterpreterInput,
        context: RequirementInterpretationContext | None = None,
    ) -> InterpreterResult:
        _ = context
        if interpreter_input.mode is not InterpreterMode.INITIAL:
            return InterpreterResult.failure_result(
                InterpreterFailure("INITIAL_ONLY", "Deterministic Parser Hybrid only supports INITIAL input", interpreter_input.source_input)
            )
        ir, proposal = build_deterministic_initial_proposal(interpreter_input.source_input)
        self.last_ir = ir
        if proposal.unresolved_semantics:
            return InterpreterResult.unresolved(proposal)
        return InterpreterResult.success(proposal)


def build_deterministic_initial_proposal(message: str) -> tuple[ParserSemanticIR, InitialRequirementProposal]:
    extractor = ParserEvidenceExtractor()
    evidence = extractor.extract(message)
    bindings = BindingConsolidator().consolidate(DeterministicInitialBinder().bind(message, evidence), evidence)
    required_slots = RequiredSlotCompletenessDeriver().derive(bindings)
    ir = ParserInterpretationRouter().route(bindings, required_slots, evidence)
    return ir, DeterministicInitialProposalBuilder().build(ir, message)


def _route_bindings(message: str, evidence: tuple[ParserSemanticEvidence, ...]) -> tuple[ParserSemanticBinding, ...]:
    after_correction = _after_last_correction(message)
    route_text = after_correction or message
    if "或" in message and "从" in message:
        destination = _single_location_after(message, ("去", "到"))
        bindings = [
            ParserSemanticBinding(
                ParserSemanticTarget.ORIGIN,
                ParserBindingState.AMBIGUOUS,
                ParserCandidateType.CITY,
                value_signal="alternative origin",
                evidence_ids=_ids_for(evidence, ParserEvidenceKind.LOCATION_TEXT, ParserEvidenceKind.ALTERNATIVE_TEXT),
            )
        ]
        if destination is not None:
            bindings.append(_location_binding(ParserSemanticTarget.DESTINATION, destination, evidence))
        return tuple(bindings)
    match = re.search(r"从(?P<origin>[^，,。…\s或]+)(?:出发)?(?:去|到)(?P<destination>[^，,。…\s]+)", route_text)
    if match is not None:
        return (
            _location_binding(ParserSemanticTarget.ORIGIN, match.group("origin"), evidence),
            _location_binding(ParserSemanticTarget.DESTINATION, match.group("destination"), evidence),
        )
    bindings: list[ParserSemanticBinding] = []
    origin = _single_location_after(message, ("从",))
    destination = _single_location_after(message, ("去", "到"))
    if origin is not None:
        bindings.append(_location_binding(ParserSemanticTarget.ORIGIN, origin, evidence))
    if destination is not None:
        bindings.append(_location_binding(ParserSemanticTarget.DESTINATION, destination, evidence))
    return tuple(bindings)


def _date_bindings(message: str, evidence: tuple[ParserSemanticEvidence, ...]) -> tuple[ParserSemanticBinding, ...]:
    if "下周" in message:
        return (
            ParserSemanticBinding(
                ParserSemanticTarget.DEPARTURE_DATE,
                ParserBindingState.UNSUPPORTED,
                ParserCandidateType.DATE,
                value_signal="relative date requires deterministic context",
                evidence_ids=_ids_for(evidence, ParserEvidenceKind.DATE_TEXT),
            ),
        )
    date_items = tuple(item for item in evidence if item.kind is ParserEvidenceKind.DATE_TEXT and "月" in item.source_text)
    if not date_items:
        return ()
    if "或者" in message or "都可以" in message:
        return (
            ParserSemanticBinding(
                ParserSemanticTarget.DEPARTURE_DATE,
                ParserBindingState.UNSUPPORTED,
                ParserCandidateType.DATE,
                value_signal="alternative dates",
                evidence_ids=tuple(item.evidence_id for item in date_items),
            ),
        )
    corrected = _after_last_correction(message)
    if corrected:
        corrected_items = tuple(item for item in date_items if item.source_text in corrected)
        if corrected_items:
            date_items = corrected_items[-1:]
    return tuple(_date_binding(item) for item in date_items)


def _price_binding(message: str, evidence: tuple[ParserSemanticEvidence, ...]) -> ParserSemanticBinding | None:
    compact = re.sub(r"\s+", "", message)
    if "预算" not in compact and "以内" not in compact:
        return None
    if "一千多" in compact:
        return None
    numbers = tuple(item for item in evidence if item.kind is ParserEvidenceKind.VALUE_TEXT and item.normalized_text is not None)
    if not numbers:
        return None
    value_source = _price_value_evidence(message, numbers)
    if value_source is None:
        return None
    value_text = value_source.normalized_text
    if value_text is None:
        return None
    value = Money(Decimal(value_text), "CNY")
    return ParserSemanticBinding(
        ParserSemanticTarget.MAX_PRICE,
        ParserBindingState.RESOLVED,
        ParserCandidateType.MONEY,
        value=value,
        value_signal=value_source.source_text,
        evidence_ids=(value_source.evidence_id,),
    )


def _price_value_evidence(
    message: str,
    numbers: tuple[ParserSemanticEvidence, ...],
) -> ParserSemanticEvidence | None:
    within_index = message.find("以内")
    if within_index >= 0:
        before_within = tuple(
            item
            for item in numbers
            if item.source_location is not None and item.source_location.end <= within_index
        )
        return before_within[-1] if before_within else None
    return numbers[-1]


def _location_binding(
    target: ParserSemanticTarget,
    value_signal: str,
    evidence: tuple[ParserSemanticEvidence, ...],
) -> ParserSemanticBinding:
    token = _clean_location(value_signal)
    code = _LOCATION_ALIASES.get(token)
    state = ParserBindingState.RESOLVED if code is not None else ParserBindingState.UNSUPPORTED
    return ParserSemanticBinding(
        target,
        state,
        ParserCandidateType.AIRPORT if token in _AIRPORT_ALIASES else ParserCandidateType.CITY,
        AirportCode(code) if code is not None else None,
        token,
        tuple(item.evidence_id for item in evidence if item.source_text == token),
    )


def _date_binding(item: ParserSemanticEvidence) -> ParserSemanticBinding:
    match = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", item.source_text)
    if match is None:
        return ParserSemanticBinding(ParserSemanticTarget.DEPARTURE_DATE, ParserBindingState.UNSUPPORTED, ParserCandidateType.DATE, evidence_ids=(item.evidence_id,))
    return ParserSemanticBinding(
        ParserSemanticTarget.DEPARTURE_DATE,
        ParserBindingState.RESOLVED,
        ParserCandidateType.DATE,
        LocalDate(date(2026, int(match.group(1)), int(match.group(2)))),
        item.source_text,
        (item.evidence_id,),
    )


def _simple_binding(
    target: ParserSemanticTarget,
    value: object | None,
    candidate_type: ParserCandidateType,
    evidence: tuple[ParserSemanticEvidence, ...],
) -> ParserSemanticBinding:
    return ParserSemanticBinding(
        target,
        ParserBindingState.RESOLVED,
        candidate_type,
        value,
        str(value),
        _ids_for(evidence, ParserEvidenceKind.CONSTRAINT_TEXT, ParserEvidenceKind.PREFERENCE_TEXT),
    )


def _proposal_item(binding: ParserSemanticBinding) -> HardConstraint | SoftPreference | None:
    if binding.state is not ParserBindingState.RESOLVED:
        return None
    if binding.target is ParserSemanticTarget.ORIGIN and isinstance(binding.value, AirportCode):
        return _constraint("parser-origin", ConstraintScope.ORIGIN_AIRPORT, ConstraintOperator.EQUALS, binding.value)
    if binding.target is ParserSemanticTarget.DESTINATION and isinstance(binding.value, AirportCode):
        return _constraint("parser-destination", ConstraintScope.DESTINATION_AIRPORT, ConstraintOperator.EQUALS, binding.value)
    if binding.target is ParserSemanticTarget.DEPARTURE_DATE and isinstance(binding.value, LocalDate):
        return _constraint("parser-departure-date", ConstraintScope.DEPARTURE_DATE, ConstraintOperator.EQUALS, binding.value)
    if binding.target is ParserSemanticTarget.MAX_PRICE and isinstance(binding.value, Money):
        return _constraint("parser-max-price", ConstraintScope.MAX_PRICE, ConstraintOperator.AT_OR_BEFORE, binding.value)
    if binding.target is ParserSemanticTarget.MAX_STOPS and isinstance(binding.value, StopCount):
        return _constraint("parser-max-stops", ConstraintScope.MAX_STOPS, ConstraintOperator.AT_OR_BEFORE, binding.value)
    if binding.target is ParserSemanticTarget.PRICE:
        return SoftPreference(PreferenceId("parser-price"), PreferenceScope.PRICE, PreferenceImportance.HIGH)
    if binding.target is ParserSemanticTarget.FEWER_STOPS:
        return SoftPreference(PreferenceId("parser-fewer-stops"), PreferenceScope.FEWER_STOPS, PreferenceImportance.HIGH)
    return None


def _constraint(
    raw_id: str,
    scope: ConstraintScope,
    operator: ConstraintOperator,
    value: ParserConstraintExpression,
) -> HardConstraint:
    return HardConstraint(ConstraintId(raw_id), scope, operator, value)


def _single_location_after(message: str, markers: tuple[str, ...]) -> str | None:
    for marker in markers:
        index = message.find(marker)
        if index < 0:
            continue
        fragment = message[index + len(marker) :]
        for token in sorted(_LOCATION_ALIASES, key=len, reverse=True):
            if fragment.startswith(token):
                return token
    return None


def _after_last_correction(message: str) -> str:
    positions = [message.rfind(token) for token in ("不对", "不，是", "不是") if message.rfind(token) >= 0]
    if not positions:
        return ""
    return message[max(positions) :]


def _clean_location(value: str) -> str:
    token = value.replace("出发", "").strip("，,。… ")
    for particle in _BENIGN_LOCATION_SUFFIXES:
        token = token.removesuffix(particle)
    return token


def _ids_for(evidence: tuple[ParserSemanticEvidence, ...], *kinds: ParserEvidenceKind) -> tuple[str, ...]:
    return tuple(item.evidence_id for item in evidence if item.kind in kinds)


def _token_starts(message: str, token: str) -> tuple[int, ...]:
    return tuple(match.start() for match in re.finditer(re.escape(token), message))


def _semantic_resolver_required_evidence_ids(evidence: tuple[ParserSemanticEvidence, ...]) -> tuple[str, ...]:
    return tuple(
        item.evidence_id
        for item in evidence
        if item.kind is ParserEvidenceKind.UNSUPPORTED_TEXT and _requires_semantic_resolver(item.source_text)
    )


def _requires_semantic_resolver(source_text: str) -> bool:
    return not _is_benign_residue(source_text)


def _material_semantic_residue_spans(
    message: str,
    evidence: tuple[ParserSemanticEvidence, ...],
) -> tuple[tuple[int, str], ...]:
    if not message.strip():
        return ()
    spans: list[tuple[int, str]] = []
    for start, end in _residual_spans(message, evidence):
        trimmed = _trim_benign_edges(message[start:end], start)
        if trimmed is None:
            continue
        trimmed_start, source_text = trimmed
        if not _is_benign_residue(source_text):
            spans.append((trimmed_start, source_text))
    return tuple(dict.fromkeys(spans))


def _residual_spans(message: str, evidence: tuple[ParserSemanticEvidence, ...]) -> tuple[tuple[int, int], ...]:
    covered = _merged_source_spans(evidence, len(message))
    residual: list[tuple[int, int]] = []
    cursor = 0
    for start, end in covered:
        if cursor < start:
            residual.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < len(message):
        residual.append((cursor, len(message)))
    return tuple(residual)


def _merged_source_spans(
    evidence: tuple[ParserSemanticEvidence, ...],
    source_length: int,
) -> tuple[tuple[int, int], ...]:
    spans = sorted(
        (max(0, item.source_location.start), min(source_length, item.source_location.end))
        for item in evidence
        if item.source_location is not None and item.source_location.start < item.source_location.end
    )
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return tuple(merged)


def _trim_benign_edges(source_text: str, source_start: int) -> tuple[int, str] | None:
    match = re.search(r"[^\s，,。；;、.!?！？…]+(?:[\s，,。；;、.!?！？…]+[^\s，,。；;、.!?！？…]+)*", source_text)
    if match is None:
        return None
    trimmed = match.group(0).strip()
    if not trimmed:
        return None
    return source_start + match.start(), trimmed


def _is_benign_residue(source_text: str) -> bool:
    compact = re.sub(r"[\s，,。；;、.!?！？]+", "", source_text)
    if not compact:
        return True
    previous = None
    while previous != compact:
        previous = compact
        for token in sorted(_BENIGN_RESIDUE_TOKENS, key=len, reverse=True):
            compact = compact.replace(token, "")
    return compact == ""


def _proposal_evidence(evidence: tuple[ParserSemanticEvidence, ...]) -> tuple[ProposalEvidence, ...]:
    return tuple(
        ProposalEvidence(
            source_input=item.source_text,
            span=SourceSpanHint(item.source_location.start, item.source_location.end, item.source_text)
            if item.source_location is not None
            else None,
        )
        for item in evidence
    )


_AIRPORT_ALIASES = frozenset({"首都机场", "虹桥"})
_LOCATION_ALIASES = {
    "首都机场": "PEK",
    "虹桥": "SHA",
    "北京": "PEK",
    "天津": "TSN",
    "上海": "SHA",
    "广州": "CAN",
}
_BENIGN_LOCATION_SUFFIXES = ("吧",)
_BENIGN_RESIDUE_TOKENS = (
    "麻烦",
    "帮我看看",
    "帮我看",
    "帮忙看看",
    "帮忙看",
    "帮我",
    "帮忙",
    "看一下",
    "查一下",
    "查询",
    "看看",
    "而且",
    "我想订",
    "我想买",
    "我想查",
    "我想",
    "我要订",
    "我要买",
    "我要查",
    "我要",
    "请",
    "订",
    "买",
    "查",
    "机票",
    "航班",
    "不一定要直飞",
    "元",
    "人民币",
    "谢谢",
    "多谢",
    "您好",
    "你好",
    "一下",
    "吧",
    "的",
    "了",
    "呢",
    "啊",
    "呀",
    "哈",
)
