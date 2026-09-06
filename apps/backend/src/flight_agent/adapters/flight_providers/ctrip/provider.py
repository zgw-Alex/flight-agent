"""CTRIP provider-local runtime adapter for the FlightProvider port."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC
from uuid import uuid4

from flight_agent.adapters.flight_providers.ctrip.assisted_capture import (
    UnsafeAssistedEvidenceError,
    validate_sanitized_ctrip_evidence,
)
from flight_agent.adapters.flight_providers.ctrip.browser_probe import (
    CTRIP_PROVIDER_ID,
    BrowserProbeOutcome,
    CtripProbeInput,
    CtripProbeRunResult,
    run_ctrip_browser_probe,
    sanitize_probe_payload,
)
from flight_agent.domain.search import RequestedSearchScope, SearchPlan
from flight_agent.ports import (
    CoverageCompleteness,
    FlightProvider,
    ProviderAcquisitionId,
    ProviderCoverage,
    ProviderCoverageLimitation,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderId,
    ProviderRawEvidence,
    ProviderSearchResult,
)

CTRIP_RUNTIME_ADAPTER_VERSION = "m9-ctrip-runtime-flight-provider-v1"

CtripAcquisition = Callable[[CtripProbeInput], CtripProbeRunResult]
IdFactory = Callable[[], str]


class CtripFlightProvider(FlightProvider):
    """Adapt structured SearchPlan input to the existing CTRIP browser acquisition path."""

    def __init__(
        self,
        *,
        acquisition: CtripAcquisition | None = None,
        id_factory: IdFactory | None = None,
        overall_deadline_seconds: float = 45.0,
        max_attempts: int = 1,
    ) -> None:
        if overall_deadline_seconds <= 0:
            raise ValueError("overall_deadline_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._acquisition = acquisition or _run_live_ctrip_browser_probe
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._overall_deadline_seconds = overall_deadline_seconds
        self._max_attempts = max_attempts

    @property
    def provider_id(self) -> ProviderId:
        return ProviderId(CTRIP_PROVIDER_ID)

    def search(self, search_plan: SearchPlan) -> ProviderSearchResult:
        acquisition_id = ProviderAcquisitionId(self._id_factory())
        probe_input = _probe_input_from_search_plan(
            search_plan,
            overall_deadline_seconds=self._overall_deadline_seconds,
        )
        result = self._run_bounded_acquisition(probe_input)
        execution_status, data_status = _provider_statuses(result.outcome)
        coverage = _provider_coverage(
            result=result,
            requested_scope=search_plan.requested_scope,
            data_status=data_status,
        )
        raw_evidence = None
        if execution_status is ProviderExecutionStatus.SUCCESS:
            try:
                raw_evidence = _raw_evidence_from_result(
                    provider_id=self.provider_id,
                    acquisition_id=acquisition_id,
                    search_plan=search_plan,
                    result=result,
                )
            except UnsafeAssistedEvidenceError:
                execution_status = ProviderExecutionStatus.INVALID_RESPONSE
                data_status = ProviderDataStatus.UNUSABLE
                coverage = _provider_coverage(
                    result=result,
                    requested_scope=search_plan.requested_scope,
                    data_status=data_status,
                )
        return ProviderSearchResult.for_search_plan(
            provider_id=self.provider_id,
            acquisition_id=acquisition_id,
            search_plan=search_plan,
            execution_status=execution_status,
            data_status=data_status,
            coverage=coverage,
            raw_evidence=raw_evidence,
        )

    def _run_bounded_acquisition(self, probe_input: CtripProbeInput) -> CtripProbeRunResult:
        last_result: CtripProbeRunResult | None = None
        for _attempt in range(self._max_attempts):
            last_result = self._acquisition(probe_input)
            if last_result.outcome not in _RETRYABLE_OUTCOMES:
                return last_result
        if last_result is None:
            raise RuntimeError("CTRIP acquisition produced no result")
        return last_result


_RETRYABLE_OUTCOMES = {
    BrowserProbeOutcome.NETWORK_ERROR,
    BrowserProbeOutcome.TIMEOUT,
    BrowserProbeOutcome.PROVIDER_ERROR,
}


def _run_live_ctrip_browser_probe(probe_input: CtripProbeInput) -> CtripProbeRunResult:
    return asyncio.run(run_ctrip_browser_probe(probe_input))


def _probe_input_from_search_plan(
    search_plan: SearchPlan,
    *,
    overall_deadline_seconds: float,
) -> CtripProbeInput:
    scope = search_plan.requested_scope
    return CtripProbeInput(
        origin_text=scope.origin.airport.value,
        destination_text=scope.destination.airport.value,
        departure_date=scope.departure_date.departure_date.value,
        search_plan_id=search_plan.search_plan_id.value,
        overall_deadline_seconds=overall_deadline_seconds,
    )


def _provider_statuses(
    outcome: BrowserProbeOutcome,
) -> tuple[ProviderExecutionStatus, ProviderDataStatus]:
    if outcome is BrowserProbeOutcome.SUCCESS_COMPLETE:
        return ProviderExecutionStatus.SUCCESS, ProviderDataStatus.COMPLETE
    if outcome is BrowserProbeOutcome.SUCCESS_PARTIAL:
        return ProviderExecutionStatus.SUCCESS, ProviderDataStatus.PARTIAL
    if outcome is BrowserProbeOutcome.SUCCESS_EMPTY:
        return ProviderExecutionStatus.SUCCESS, ProviderDataStatus.EMPTY
    if outcome is BrowserProbeOutcome.TIMEOUT:
        return ProviderExecutionStatus.TIMEOUT, ProviderDataStatus.UNKNOWN
    if outcome in {BrowserProbeOutcome.ACCESS_CHALLENGE, BrowserProbeOutcome.LOGIN_REQUIRED}:
        return ProviderExecutionStatus.AUTH_ERROR, ProviderDataStatus.UNKNOWN
    if outcome in {BrowserProbeOutcome.PROVIDER_ERROR, BrowserProbeOutcome.NETWORK_ERROR}:
        return ProviderExecutionStatus.UPSTREAM_ERROR, ProviderDataStatus.UNKNOWN
    return ProviderExecutionStatus.INVALID_RESPONSE, ProviderDataStatus.UNUSABLE


def _provider_coverage(
    *,
    result: CtripProbeRunResult,
    requested_scope: RequestedSearchScope,
    data_status: ProviderDataStatus,
) -> ProviderCoverage:
    if data_status is ProviderDataStatus.PARTIAL:
        return ProviderCoverage(
            requested_scope=requested_scope,
            actual_scope=requested_scope,
            completeness=CoverageCompleteness.PARTIAL,
            limitations=(
                ProviderCoverageLimitation(
                    code="CTRIP_PARTIAL_EVIDENCE",
                    detail=f"CTRIP acquisition outcome was {result.outcome.value}",
                ),
            ),
        )
    if result.outcome in {
        BrowserProbeOutcome.SUCCESS_COMPLETE,
        BrowserProbeOutcome.SUCCESS_EMPTY,
    }:
        return ProviderCoverage(
            requested_scope=requested_scope,
            actual_scope=requested_scope,
            completeness=CoverageCompleteness.COMPLETE,
        )
    return ProviderCoverage(
        requested_scope=requested_scope,
        actual_scope=None,
        completeness=CoverageCompleteness.UNKNOWN,
    )


def _raw_evidence_from_result(
    *,
    provider_id: ProviderId,
    acquisition_id: ProviderAcquisitionId,
    search_plan: SearchPlan,
    result: CtripProbeRunResult,
) -> ProviderRawEvidence:
    payload = validate_sanitized_ctrip_evidence(_drop_sensitive_keys(result.to_dict()))
    if not isinstance(payload, dict):
        raise TypeError("CTRIP sanitized runtime evidence must be an object")
    return ProviderRawEvidence(
        provider_id=provider_id,
        acquisition_id=acquisition_id,
        search_plan_id=search_plan.search_plan_id,
        retrieved_at=result.acquired_at.astimezone(UTC),
        payload={
            **payload,
            "runtime_adapter_version": CTRIP_RUNTIME_ADAPTER_VERSION,
        },
        source_refs=_safe_source_refs(result.sanitized_source_ref),
    )


def _safe_source_refs(source_ref: str | None) -> tuple[str, ...]:
    if not source_ref:
        return ()
    payload = validate_sanitized_ctrip_evidence(sanitize_probe_payload({"source_ref": source_ref}))
    if not isinstance(payload, dict):
        return ()
    safe_ref = payload.get("source_ref")
    return (safe_ref,) if isinstance(safe_ref, str) and safe_ref.strip() else ()


_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "csrf",
    "har",
    "localstorage",
    "passenger",
    "password",
    "payment",
    "profile",
    "secret",
    "session",
    "set-cookie",
    "storage",
    "token",
)


def _drop_sensitive_keys(value: object) -> object:
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower().replace("_", "-")
            if any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS):
                continue
            cleaned[key_text] = _drop_sensitive_keys(item)
        return cleaned
    if isinstance(value, list):
        return [_drop_sensitive_keys(item) for item in value]
    return value


def ctrip_probe_input_for_search_plan(
    search_plan: SearchPlan,
    *,
    overall_deadline_seconds: float = 45.0,
) -> CtripProbeInput:
    """Expose deterministic SearchPlan conversion for provider-local contract tests."""

    return _probe_input_from_search_plan(
        search_plan,
        overall_deadline_seconds=overall_deadline_seconds,
    )
