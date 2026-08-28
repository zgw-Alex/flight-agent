"""M8-U4 bridge from LLM capabilities into the M3/M7 lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal, InvalidOperation
from typing import Any

from flight_agent.application.requirement_normalization import NormalizationContext
from flight_agent.application.requirement_pipeline import (
    RequirementPipelineOutcome,
    RequirementPipelineOutcomeStatus,
    execute_initial_requirement,
    execute_patch_requirement,
)
from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import (
    AirportCode,
    CabinClass,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    LocalTime,
    PassengerCount,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    RequirementState,
    SoftPreference,
    StopCount,
    ValueRange,
    ValueSet,
)
from flight_agent.domain.shared import DomainInstant, RequirementVersion
from flight_agent.ports import (
    CapabilityFailure,
    CapabilityFailureKind,
    CapabilityGenerationMetadata,
    CapabilityResult,
    CapabilityResultStatus,
    CapabilitySemanticValidation,
    InitialInterpreterPayload,
    InitialRequirementInterpretationCapability,
    InitialRequirementInterpretationRequest,
    InitialRequirementProposal,
    InterpreterFailure,
    InterpreterInput,
    InterpreterMode,
    InterpreterResult,
    PatchInterpreterPayload,
    PatchProposalAction,
    PatchProposalOperation,
    PatchRequirementProposal,
    PatchUnderstandingCapability,
    PatchUnderstandingRequest,
    ProposalEvidence,
    RequirementInterpretationContext,
    RequirementInterpreter,
    RequirementRepository,
    SourceSpanHint,
    validate_initial_requirement_proposal,
    validate_patch_proposal,
)
from flight_agent.ports.llm_invocation import LLMInvocationId


@dataclass(frozen=True)
class LLMCapabilityInvocationMetadata:
    invocation_id: LLMInvocationId
    capability: str
    model_id: str
    prompt_template_version: str
    output_schema_version: str
    adapter_version: str
    attempt_count: int
    latency_ms: int
    token_count_observed: bool
    requirement_id: RequirementId | None = None
    requirement_version: RequirementVersion | None = None
    validation_outcome: str = "NOT_RUN"
    stale_outcome: str = "NOT_STALE"


@dataclass(frozen=True)
class RequirementLLMIntegrationResult:
    pipeline_outcome: RequirementPipelineOutcome
    invocation_metadata: LLMCapabilityInvocationMetadata | None
    downstream_invoked: bool = False


PatchCommittedCallback = Callable[[RequirementState], None]
class LLMRequirementInterpreter:
    """Adapts U1 LLM capability ports to the M3 RequirementInterpreter port."""

    def __init__(
        self,
        *,
        initial_capability: InitialRequirementInterpretationCapability,
        patch_capability: PatchUnderstandingCapability,
        locale: str,
    ) -> None:
        self._initial_capability = initial_capability
        self._patch_capability = patch_capability
        self._locale = locale
        self.last_metadata: LLMCapabilityInvocationMetadata | None = None

    def interpret(
        self,
        interpreter_input: InterpreterInput,
        context: RequirementInterpretationContext | None = None,
    ) -> InterpreterResult:
        if interpreter_input.mode is InterpreterMode.INITIAL:
            result = self._initial_capability.interpret_initial_requirement(
                InitialRequirementInterpretationRequest(
                    user_message=interpreter_input.source_input,
                    locale=self._locale,
                )
            )
            return self._interpreter_result(result, interpreter_input.source_input)

        if context is None:
            return InterpreterResult.failure_result(
                InterpreterFailure(
                    code="PATCH_CONTEXT_REQUIRED",
                    message="PATCH interpretation requires current requirement context",
                    source_input=interpreter_input.source_input,
                )
            )
        result = self._patch_capability.understand_patch(
            PatchUnderstandingRequest(
                user_message=interpreter_input.source_input,
                requirement_id=context.requirement_id,
                based_on_requirement_version=context.current_version,
                current_requirement_projection=requirement_context_projection(context),
                constraint_ids=tuple(item.value for item in context.constraint_ids),
                preference_ids=tuple(item.value for item in context.preference_ids),
            )
        )
        return self._interpreter_result(result, interpreter_input.source_input)

    def _interpreter_result(
        self,
        result: CapabilityResult[Any],
        source_input: str,
    ) -> InterpreterResult:
        if isinstance(result.metadata, LLMBackedCapabilityMetadata):
            self.last_metadata = result.metadata.invocation
        if result.status is CapabilityResultStatus.FAILURE:
            failure = result.failure
            return InterpreterResult.failure_result(
                InterpreterFailure(
                    code=failure.code if failure is not None else "LLM_CAPABILITY_FAILED",
                    message=failure.message if failure is not None else "LLM capability failed",
                    source_input=source_input,
                )
            )
        if result.output is None:
            return InterpreterResult.failure_result(
                InterpreterFailure(
                    code="LLM_CAPABILITY_EMPTY_OUTPUT",
                    message="LLM capability returned no proposal",
                    source_input=source_input,
                )
            )
        if result.status in {
            CapabilityResultStatus.AMBIGUOUS,
            CapabilityResultStatus.INSUFFICIENT_CONTEXT,
        }:
            return InterpreterResult.unresolved(result.output)
        return InterpreterResult.success(result.output)


@dataclass(frozen=True)
class LLMBackedCapabilityMetadata(CapabilityGenerationMetadata):
    invocation: LLMCapabilityInvocationMetadata | None = None


def execute_llm_initial_requirement(
    *,
    repository: RequirementRepository,
    interpreter: RequirementInterpreter,
    source_input: str,
    normalization_context: NormalizationContext,
    requirement_id: RequirementId,
    operation_id: str,
    recorded_at: DomainInstant,
) -> RequirementLLMIntegrationResult:
    outcome = execute_initial_requirement(
        repository=repository,
        interpreter=interpreter,
        interpreter_input=InterpreterInput(
            mode=InterpreterMode.INITIAL,
            payload=InitialInterpreterPayload(source_input),
        ),
        normalization_context=normalization_context,
        requirement_id=requirement_id,
        operation_id=operation_id,
        recorded_at=recorded_at,
    )
    return RequirementLLMIntegrationResult(
        pipeline_outcome=outcome,
        invocation_metadata=_last_metadata(interpreter, outcome),
    )


def execute_llm_patch_requirement(
    *,
    repository: RequirementRepository,
    interpreter: RequirementInterpreter,
    source_input: str,
    normalization_context: NormalizationContext,
    requirement_id: RequirementId,
    operation_id: str,
    recorded_at: DomainInstant,
    on_patch_committed: PatchCommittedCallback | None = None,
) -> RequirementLLMIntegrationResult:
    outcome = execute_patch_requirement(
        repository=repository,
        interpreter=interpreter,
        interpreter_input=InterpreterInput(
            mode=InterpreterMode.PATCH,
            payload=PatchInterpreterPayload(source_input),
        ),
        normalization_context=normalization_context,
        requirement_id=requirement_id,
        operation_id=operation_id,
        recorded_at=recorded_at,
    )
    downstream_invoked = False
    if (
        outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
        and outcome.requirement is not None
        and on_patch_committed is not None
    ):
        on_patch_committed(outcome.requirement)
        downstream_invoked = True
    return RequirementLLMIntegrationResult(
        pipeline_outcome=outcome,
        invocation_metadata=_last_metadata(interpreter, outcome),
        downstream_invoked=downstream_invoked,
    )


def requirement_context_projection(context: RequirementInterpretationContext) -> str:
    if context.current_requirement_projection.strip():
        return context.current_requirement_projection
    constraints = ", ".join(item.value for item in context.constraint_ids) or "NONE"
    preferences = ", ".join(item.value for item in context.preference_ids) or "NONE"
    return (
        f"requirement_id={context.requirement_id.value}; "
        f"current_version={context.current_version.value}; "
        f"constraint_ids={constraints}; preference_ids={preferences}"
    )


def initial_requirement_proposal_from_json(payload: dict[str, Any]) -> InitialRequirementProposal:
    return InitialRequirementProposal(
        constraints=tuple(
            _hard_constraint(item, f"llm-constraint-{index}")
            for index, item in enumerate(_list(payload, "constraints"), start=1)
        ),
        preferences=tuple(
            _soft_preference(item, f"llm-preference-{index}")
            for index, item in enumerate(_list(payload, "preferences"), start=1)
        ),
        unresolved_semantics=_strings(payload, "unresolved_semantics"),
        source_input=_string(payload, "source_input", required=False),
        evidence=_evidence(payload),
        ambiguity_reasons=_strings(payload, "ambiguity_reasons"),
        insufficient_context=_strings(payload, "insufficient_context"),
    )


def patch_requirement_proposal_from_json(
    payload: dict[str, Any],
    request: PatchUnderstandingRequest,
) -> PatchRequirementProposal:
    proposed_id = _optional_string(payload, "based_on_requirement_id")
    proposed_version = _optional_int(payload, "based_on_requirement_version")
    if proposed_id is not None and proposed_id != request.requirement_id.value:
        raise ValueError("Patch proposal requirement lineage conflicts with trusted context")
    if proposed_version is not None and proposed_version != request.based_on_requirement_version.value:
        raise ValueError("Patch proposal version lineage conflicts with trusted context")
    return PatchRequirementProposal(
        operations=tuple(
            _patch_operation(item, source_input=request.user_message)
            for item in _list(payload, "operations")
        ),
        unresolved_semantics=_strings(payload, "unresolved_semantics"),
        source_input=_string(payload, "source_input", required=False),
        based_on_requirement_id=request.requirement_id,
        based_on_requirement_version=request.based_on_requirement_version,
        evidence=_evidence(payload),
        ambiguity_reasons=_strings(payload, "ambiguity_reasons"),
        insufficient_context=_strings(payload, "insufficient_context"),
    )


def metadata_from_invocation(
    telemetry,
    validation_outcome: str,
    requirement_id: RequirementId | None = None,
    requirement_version: RequirementVersion | None = None,
) -> LLMBackedCapabilityMetadata:
    invocation = LLMCapabilityInvocationMetadata(
        invocation_id=telemetry.invocation_id,
        capability=telemetry.capability,
        model_id=telemetry.model_id,
        prompt_template_version=telemetry.prompt_template_version,
        output_schema_version=telemetry.output_schema_version,
        adapter_version=telemetry.adapter_version,
        attempt_count=telemetry.attempt_count,
        latency_ms=telemetry.latency_ms,
        token_count_observed=telemetry.usage is not None,
        requirement_id=requirement_id,
        requirement_version=requirement_version,
        validation_outcome=validation_outcome,
    )
    return LLMBackedCapabilityMetadata(
        capability=_capability_name(telemetry.capability),
        output_schema_version=telemetry.output_schema_version,
        adapter_version=telemetry.adapter_version,
        model_identity=telemetry.model_id,
        invocation=invocation,
    )


def _capability_name(value: str):
    from flight_agent.ports import LLMCapabilityName

    return LLMCapabilityName(value)


def _validated_initial_result(
    proposal: InitialRequirementProposal,
    metadata: LLMBackedCapabilityMetadata,
) -> CapabilityResult[InitialRequirementProposal]:
    validation = validate_initial_requirement_proposal(proposal)
    metadata = _metadata_with_validation(metadata, validation)
    if proposal.ambiguity_reasons or proposal.unresolved_semantics:
        return CapabilityResult.ambiguous(metadata, proposal, validation)
    if proposal.insufficient_context:
        return CapabilityResult.insufficient_context(metadata, proposal, validation)
    if not validation.is_semantically_valid:
        return CapabilityResult.failure_result(
            metadata,
            CapabilityFailure(
                CapabilityFailureKind.SEMANTIC_INVALID,
                validation.issues[0].code,
                validation.issues[0].message,
            ),
        )
    return CapabilityResult.success(metadata, proposal, validation)


def _validated_patch_result(
    proposal: PatchRequirementProposal,
    metadata: LLMBackedCapabilityMetadata,
) -> CapabilityResult[PatchRequirementProposal]:
    validation = validate_patch_proposal(proposal)
    metadata = _metadata_with_validation(metadata, validation)
    if proposal.ambiguity_reasons or proposal.unresolved_semantics:
        return CapabilityResult.ambiguous(metadata, proposal, validation)
    if proposal.insufficient_context:
        return CapabilityResult.insufficient_context(metadata, proposal, validation)
    if not validation.is_semantically_valid:
        return CapabilityResult.failure_result(
            metadata,
            CapabilityFailure(
                CapabilityFailureKind.SEMANTIC_INVALID,
                validation.issues[0].code,
                validation.issues[0].message,
            ),
        )
    return CapabilityResult.success(metadata, proposal, validation)


def _metadata_with_validation(
    metadata: LLMBackedCapabilityMetadata,
    validation: CapabilitySemanticValidation,
) -> LLMBackedCapabilityMetadata:
    if metadata.invocation is None:
        return metadata
    outcome = "SEMANTIC_VALID" if validation.is_semantically_valid else "SEMANTIC_INVALID"
    return LLMBackedCapabilityMetadata(
        capability=metadata.capability,
        output_schema_version=metadata.output_schema_version,
        adapter_version=metadata.adapter_version,
        model_identity=metadata.model_identity,
        invocation=LLMCapabilityInvocationMetadata(
            invocation_id=metadata.invocation.invocation_id,
            capability=metadata.invocation.capability,
            model_id=metadata.invocation.model_id,
            prompt_template_version=metadata.invocation.prompt_template_version,
            output_schema_version=metadata.invocation.output_schema_version,
            adapter_version=metadata.invocation.adapter_version,
            attempt_count=metadata.invocation.attempt_count,
            latency_ms=metadata.invocation.latency_ms,
            token_count_observed=metadata.invocation.token_count_observed,
            requirement_id=metadata.invocation.requirement_id,
            requirement_version=metadata.invocation.requirement_version,
            validation_outcome=outcome,
            stale_outcome=metadata.invocation.stale_outcome,
        ),
    )


def _capability_failure_result(
    metadata: LLMBackedCapabilityMetadata,
    failure: CapabilityFailure | None,
    source_input: str,
) -> CapabilityResult:
    return CapabilityResult.failure_result(
        metadata,
        failure
        or CapabilityFailure(
            CapabilityFailureKind.PROVIDER_TRANSPORT_FAILURE,
            "LLM_INVOCATION_FAILED",
            f"LLM invocation failed for source length {len(source_input)}",
        ),
    )


def _last_metadata(
    interpreter: RequirementInterpreter,
    outcome: RequirementPipelineOutcome,
) -> LLMCapabilityInvocationMetadata | None:
    metadata = getattr(interpreter, "last_metadata", None)
    if not isinstance(metadata, LLMCapabilityInvocationMetadata):
        return None
    return LLMCapabilityInvocationMetadata(
        invocation_id=metadata.invocation_id,
        capability=metadata.capability,
        model_id=metadata.model_id,
        prompt_template_version=metadata.prompt_template_version,
        output_schema_version=metadata.output_schema_version,
        adapter_version=metadata.adapter_version,
        attempt_count=metadata.attempt_count,
        latency_ms=metadata.latency_ms,
        token_count_observed=metadata.token_count_observed,
        requirement_id=outcome.requirement.requirement_id
        if outcome.requirement is not None
        else metadata.requirement_id,
        requirement_version=outcome.requirement.version
        if outcome.requirement is not None
        else metadata.requirement_version,
        validation_outcome=metadata.validation_outcome,
        stale_outcome="STALE_REJECTED"
        if outcome.status is RequirementPipelineOutcomeStatus.CONCURRENCY_CONFLICT
        else metadata.stale_outcome,
    )


def _hard_constraint(item: object, default_id: str) -> HardConstraint:
    data = _unwrap_named_item(_dict(item, "constraint"))
    scope = ConstraintScope(_constraint_scope_token(data, default_id))
    operator = ConstraintOperator(
        _enum_token(_string(data, "operator", required=False) or _default_operator(scope), _OPERATOR_ALIASES)
    )
    return HardConstraint(
        constraint_id=ConstraintId(_string(data, "constraint_id", required=False) or default_id),
        scope=scope,
        operator=operator,
        value=_constraint_value(scope, operator, data.get("value")),
    )


def _soft_preference(item: object, default_id: str) -> SoftPreference:
    data = _unwrap_named_item(_dict(item, "preference"))
    value = data.get("value")
    scope = PreferenceScope(
        _enum_token(_string(data, "scope", required=False) or _string(data, "type"), _PREFERENCE_SCOPE_ALIASES)
    )
    importance_value = _string(data, "importance", required=False) or "MEDIUM"
    if scope is PreferenceScope.PRICE and isinstance(value, str):
        normalized_value = _enum_token(value, _IMPORTANCE_ALIASES)
        if normalized_value in {item.value for item in PreferenceImportance}:
            importance_value = normalized_value
            value = None
    return SoftPreference(
        preference_id=PreferenceId(_string(data, "preference_id", required=False) or default_id),
        scope=scope,
        importance=PreferenceImportance(_enum_token(importance_value, _IMPORTANCE_ALIASES)),
        value=None if value is None else _preference_value(scope, value),
    )


def _patch_operation(item: object, source_input: str = "") -> PatchProposalOperation:
    data = _dict(item, "operation")
    action = PatchProposalAction(_enum_token(_string(data, "action"), _ACTION_ALIASES))
    target_id = _target_id(action, _optional_string(data, "target_id"))
    raw_item = data.get("item")
    proposal_item: HardConstraint | SoftPreference | None = None
    if raw_item is not None:
        raw_item = _item_with_inferred_scope(raw_item, action, target_id)
        if isinstance(raw_item, dict) and "scope" not in _unwrap_named_item(raw_item):
            inferred = _scope_from_constraint_payload(_unwrap_named_item(raw_item), source_input)
            if inferred is not None:
                raw_item = {**_unwrap_named_item(raw_item), "scope": inferred}
        proposal_item = (
            _hard_constraint(raw_item, "llm-patch-constraint")
            if _is_constraint_action(action)
            else _soft_preference(raw_item, "llm-patch-preference")
        )
    return PatchProposalOperation(action=action, item=proposal_item, target_id=target_id)


def _constraint_value(
    scope: ConstraintScope,
    operator: ConstraintOperator,
    value: object,
):
    if operator in {ConstraintOperator.IN, ConstraintOperator.NOT_IN}:
        values = _list(_dict(value, "value-set"), "items")
        return ValueSet(tuple(_scalar_constraint_value(scope, item) for item in values))
    if operator is ConstraintOperator.BETWEEN:
        data = _dict(value, "value-range")
        return ValueRange(
            _scalar_constraint_value(scope, data.get("start")),
            _scalar_constraint_value(scope, data.get("end")),
        )
    return _scalar_constraint_value(scope, value)


def _preference_value(scope: PreferenceScope, value: object):
    if isinstance(value, dict) and "start" in value and "end" in value:
        return ValueRange(
            _scalar_preference_value(scope, value.get("start")),
            _scalar_preference_value(scope, value.get("end")),
        )
    return _scalar_preference_value(scope, value)


def _scalar_constraint_value(scope: ConstraintScope, value: object):
    if scope in {ConstraintScope.ORIGIN_AIRPORT, ConstraintScope.DESTINATION_AIRPORT}:
        return AirportCode(_raw_value(value))
    if scope is ConstraintScope.DEPARTURE_DATE:
        return LocalDate(date.fromisoformat(_raw_value(value)))
    if scope is ConstraintScope.DEPARTURE_TIME:
        return LocalTime(time.fromisoformat(_raw_value(value)))
    if scope is ConstraintScope.CABIN_CLASS:
        return CabinClass(_raw_value(value))
    if scope is ConstraintScope.PASSENGER_COUNT:
        return PassengerCount(int(_raw_value(value)))
    if scope is ConstraintScope.MAX_STOPS:
        raw = _raw_value(value)
        if "." in raw:
            raise ValueError("MAX_STOPS requires a non-negative integer")
        return StopCount(int(raw))
    if scope is ConstraintScope.MAX_PRICE:
        if isinstance(value, str | int) and not isinstance(value, bool):
            try:
                return Money(Decimal(str(value)), "CNY")
            except InvalidOperation as exc:
                raise ValueError("Money value requires decimal amount") from exc
        data = _dict(value, "money")
        if isinstance(data.get("value"), dict):
            data = _dict(data["value"], "money")
        try:
            amount = Decimal(str(data["amount"]))
        except (KeyError, InvalidOperation) as exc:
            raise ValueError("Money value requires decimal amount") from exc
        return Money(amount, _string(data, "currency", required=False) or "CNY")
    raise ValueError(f"Unsupported constraint scope {scope.value}")


def _scalar_preference_value(scope: PreferenceScope, value: object):
    if scope in {PreferenceScope.DEPARTURE_TIME, PreferenceScope.ARRIVAL_TIME}:
        return LocalTime(time.fromisoformat(_raw_value(value)))
    if scope is PreferenceScope.AIRPORT_MATCH:
        return AirportCode(_raw_value(value))
    if scope in {PreferenceScope.PRICE, PreferenceScope.FEWER_STOPS}:
        return PassengerCount(int(_raw_value(value)))
    raise ValueError(f"Unsupported preference scope {scope.value}")


def _target_id(action: PatchProposalAction, value: str | None) -> ConstraintId | PreferenceId | None:
    if value is None:
        return None
    return ConstraintId(value) if _is_constraint_action(action) else PreferenceId(value)


def _item_with_inferred_scope(
    item: object,
    action: PatchProposalAction,
    target_id: ConstraintId | PreferenceId | None,
) -> object:
    if not isinstance(item, dict) or target_id is None:
        return item
    data = _unwrap_named_item(item)
    if "scope" in data or (
        "type" in data
        and _enum_token(str(data["type"]), _ITEM_KIND_ALIASES) not in _GENERIC_ITEM_KINDS
    ):
        return item
    inferred = _scope_from_target_id(action, target_id)
    if inferred is None and _is_constraint_action(action):
        inferred = _scope_from_constraint_payload(data, str(target_id.value) if target_id else "")
    return {**data, "scope": inferred} if inferred is not None else item


def _scope_from_target_id(
    action: PatchProposalAction,
    target_id: ConstraintId | PreferenceId,
) -> str | None:
    value = target_id.value.lower()
    if _is_constraint_action(action):
        if "origin" in value:
            return ConstraintScope.ORIGIN_AIRPORT.value
        if "destination" in value:
            return ConstraintScope.DESTINATION_AIRPORT.value
        if "date" in value:
            return ConstraintScope.DEPARTURE_DATE.value
        if "time" in value:
            return ConstraintScope.DEPARTURE_TIME.value
        if "price" in value:
            return ConstraintScope.MAX_PRICE.value
    elif "price" in value:
        return PreferenceScope.PRICE.value
    elif "time" in value:
        return PreferenceScope.DEPARTURE_TIME.value
    return None


def _constraint_scope_token(data: dict[str, Any], default_id: str) -> str:
    for key in ("scope", "constraint_scope", "field"):
        value = _optional_string(data, key)
        if value is not None:
            return _enum_token(value, _SCOPE_ALIASES)
    raw_type = _optional_string(data, "type")
    if raw_type is not None:
        token = _enum_token(raw_type, _ITEM_KIND_ALIASES)
        if token not in _GENERIC_ITEM_KINDS:
            return _enum_token(raw_type, _SCOPE_ALIASES)
    inferred = _scope_from_constraint_payload(data, default_id)
    if inferred is not None:
        return inferred
    raise ValueError("HardConstraint requires a scope")


def _scope_from_constraint_payload(data: dict[str, Any], identifier_hint: str) -> str | None:
    hint = " ".join(
        str(value)
        for value in (
            data.get("constraint_id"),
            data.get("name"),
            data.get("field"),
            identifier_hint,
        )
        if value is not None
    ).lower()
    if "price" in hint or "budget" in hint:
        return ConstraintScope.MAX_PRICE.value
    if "stop" in hint or "stops" in hint or "direct" in hint:
        return ConstraintScope.MAX_STOPS.value
    if "origin" in hint:
        return ConstraintScope.ORIGIN_AIRPORT.value
    if "destination" in hint:
        return ConstraintScope.DESTINATION_AIRPORT.value
    if "date" in hint:
        return ConstraintScope.DEPARTURE_DATE.value
    if "time" in hint:
        return ConstraintScope.DEPARTURE_TIME.value
    value = data.get("value")
    if isinstance(value, dict):
        nested = value.get("value")
        money = nested if isinstance(nested, dict) else value
        if "amount" in money and "currency" in money:
            return ConstraintScope.MAX_PRICE.value
    if isinstance(value, str) and ("cny" in value.lower() or "元" in value):
        return ConstraintScope.MAX_PRICE.value
    if isinstance(value, int | float) and not isinstance(value, bool):
        operator = _optional_string(data, "operator")
        if operator is not None and _enum_token(operator, _OPERATOR_ALIASES) == "AT_OR_BEFORE":
            return ConstraintScope.MAX_PRICE.value
    return None


def _is_constraint_action(action: PatchProposalAction) -> bool:
    return action in {
        PatchProposalAction.ADD_CONSTRAINT,
        PatchProposalAction.REPLACE_CONSTRAINT,
        PatchProposalAction.REMOVE_CONSTRAINT,
        PatchProposalAction.CLEAR_CONSTRAINTS,
    }


def _default_operator(scope: ConstraintScope) -> str:
    return "AT_OR_BEFORE" if scope is ConstraintScope.MAX_PRICE else "EQUALS"


def _enum_token(value: str, aliases: dict[str, str]) -> str:
    token = value.strip().upper().replace(" ", "_").replace("-", "_")
    return aliases.get(token, token)


_SCOPE_ALIASES = {
    "ORIGIN": "ORIGIN_AIRPORT",
    "DEPARTURE_AIRPORT": "ORIGIN_AIRPORT",
    "DESTINATION": "DESTINATION_AIRPORT",
    "ARRIVAL_AIRPORT": "DESTINATION_AIRPORT",
    "DATE": "DEPARTURE_DATE",
    "TIME": "DEPARTURE_TIME",
    "PRICE": "MAX_PRICE",
    "STOPS": "MAX_STOPS",
    "MAX_STOPS": "MAX_STOPS",
}
_PREFERENCE_SCOPE_ALIASES = {
    "PRICE_PREFERENCE": "PRICE",
    "LOWER_PRICE": "PRICE",
    "MORNING": "DEPARTURE_TIME",
    "TIME": "DEPARTURE_TIME",
}
_OPERATOR_ALIASES = {
    "EQ": "EQUALS",
    "EQUAL": "EQUALS",
    "LTE": "AT_OR_BEFORE",
    "LE": "AT_OR_BEFORE",
    "LESS_THAN_OR_EQUAL": "AT_OR_BEFORE",
    "LESS_THAN_OR_EQUAL_TO": "AT_OR_BEFORE",
    "LESS_OR_EQUAL": "AT_OR_BEFORE",
    "NO_MORE_THAN": "AT_OR_BEFORE",
    "MAX": "AT_OR_BEFORE",
    "GTE": "AT_OR_AFTER",
    "GE": "AT_OR_AFTER",
    "GREATER_THAN_OR_EQUAL": "AT_OR_AFTER",
    "GREATER_THAN_OR_EQUAL_TO": "AT_OR_AFTER",
}
_IMPORTANCE_ALIASES = {
    "NORMAL": "MEDIUM",
}
_ACTION_ALIASES = {
    "REPLACE": "REPLACE_CONSTRAINT",
    "ADD": "ADD_CONSTRAINT",
    "REMOVE": "REMOVE_CONSTRAINT",
}
_ITEM_KIND_ALIASES = {
    "CONSTRAINT": "HARD_CONSTRAINT",
    "HARD_CONSTRAINT": "HARD_CONSTRAINT",
    "HARDCONSTRAINT": "HARD_CONSTRAINT",
    "PREFERENCE": "SOFT_PREFERENCE",
    "SOFT_PREFERENCE": "SOFT_PREFERENCE",
    "SOFTPREFERENCE": "SOFT_PREFERENCE",
}
_GENERIC_ITEM_KINDS = frozenset({"HARD_CONSTRAINT", "SOFT_PREFERENCE"})


def _evidence(payload: dict[str, Any]) -> tuple[ProposalEvidence, ...]:
    evidence_items = payload.get("evidence", ())
    if not isinstance(evidence_items, list):
        return ()
    evidence: list[ProposalEvidence] = []
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        try:
            evidence.append(_proposal_evidence(item))
        except (TypeError, ValueError):
            continue
    return tuple(evidence)


def _proposal_evidence(item: dict[str, Any]) -> ProposalEvidence:
    span = item.get("span")
    return ProposalEvidence(
        source_input=_string(item, "source_input"),
        span=_source_span(span) if isinstance(span, dict) else None,
    )


def _source_span(item: dict[str, Any]) -> SourceSpanHint:
    return SourceSpanHint(
        start=int(item["start"]),
        end=int(item["end"]),
        text=_string(item, "text"),
    )


def _list(payload: object, key: str) -> tuple[object, ...]:
    data = _dict(payload, "payload")
    value = data.get(key, ())
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return tuple(value)


def _strings(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, ())
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return tuple(str(item) for item in value if str(item).strip())


def _dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return value


def _unwrap_named_item(data: dict[str, Any]) -> dict[str, Any]:
    if "scope" in data:
        return data
    if len(data) == 1:
        only_value = next(iter(data.values()))
        if isinstance(only_value, dict):
            return only_value
    return data


def _string(payload: dict[str, Any], key: str, required: bool = True) -> str:
    value = payload.get(key)
    if value is None and not required:
        return ""
    if isinstance(value, str) and value.strip() == "" and not required:
        return ""
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{key} must be a non-empty string when provided")
    return value


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer when provided")
    return value


def _raw_value(value: object) -> str:
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if isinstance(value, dict):
        for key in ("code", "airport", "airport_code", "iata"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise ValueError("Typed scalar value must be a string or integer")


def _assert_protocols(
    interpreter: RequirementInterpreter,
    initial: InitialRequirementInterpretationCapability,
    patch: PatchUnderstandingCapability,
) -> None:
    _ = interpreter
    _ = initial
    _ = patch
