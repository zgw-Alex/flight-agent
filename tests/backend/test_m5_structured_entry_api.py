from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.api.structured_entry import (
    StructuredRequirementRequest,
    create_structured_entry_router,
    structured_request_to_command,
)
from flight_agent.application import NormalizationContext
from flight_agent.application.structured_entry import StartStructuredRequirement
from flight_agent.bootstrap.app import create_app
from flight_agent.domain.requirements import RequirementState
from flight_agent.domain.shared import DomainInstant


def test_structured_request_mapper_is_transport_only_and_does_not_assign_version() -> None:
    request = StructuredRequirementRequest(
        origin="PEK",
        destination="SHA",
        departure_date=date(2026, 9, 1),
        max_price_cny=1200,
        lower_price_preferred=True,
    )

    command = structured_request_to_command(request)

    assert not isinstance(command, RequirementState)
    assert not hasattr(command, "requirement_version")
    assert command.origin == "PEK"
    assert command.destination == "SHA"
    assert command.max_price_cny == 1200


def test_structured_entry_api_returns_ready_conversation_status() -> None:
    eligible_calls: list[object] = []
    client = TestClient(app_with_ids(eligible_calls))

    response = client.post(
        "/conversations",
        json={
            "origin": "PEK",
            "destination": "SHA",
            "departure_date": "2026-09-01",
            "max_price_cny": 1200,
            "lower_price_preferred": True,
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "conversation_id": "conversation-1",
        "execution_id": "execution-1",
        "requirement_id": "requirement-1",
        "requirement_version": 1,
        "status": "SEARCH_ELIGIBLE",
        "search_readiness": "READY",
        "downstream_search_eligible": True,
        "validation_issues": [],
    }
    assert len(eligible_calls) == 1


def test_structured_entry_api_returns_not_ready_without_downstream_search() -> None:
    eligible_calls: list[object] = []
    client = TestClient(app_with_ids(eligible_calls))

    response = client.post(
        "/conversations",
        json={
            "origin": "PEK",
            "destination": "SHA",
            "max_price_cny": 1200,
            "lower_price_preferred": True,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "NOT_READY"
    assert body["requirement_version"] == 1
    assert body["search_readiness"] == "NOT_READY"
    assert body["downstream_search_eligible"] is False
    assert body["validation_issues"] == ["MISSING_DEPARTURE_DATE"]
    assert eligible_calls == []


def test_default_app_includes_structured_entry_router() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/conversations",
        json={"origin": "PEK", "destination": "LAX", "departure_date": "2026-09-01"},
    )

    assert response.status_code == 201
    assert response.json()["search_readiness"] == "READY"


def app_with_ids(eligible_calls: list[object]):
    app = FastAPI()
    ids = iter(("conversation-1", "execution-1", "requirement-1", "operation-1"))
    use_case = StartStructuredRequirement(
        repository=InMemoryRequirementRepository(),
        normalization_context=NormalizationContext(
            reference_instant=instant(),
            timezone="Asia/Shanghai",
            locale="zh-CN",
            reference_data_version="test-v1",
        ),
        recorded_at=instant,
        id_factory=lambda: next(ids),
        on_search_eligible=eligible_calls.append,
    )
    app.include_router(create_structured_entry_router(use_case))
    return app


def instant() -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 25, 8, 0, tzinfo=UTC))
