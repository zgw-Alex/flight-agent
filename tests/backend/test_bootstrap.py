from fastapi import FastAPI
from fastapi.testclient import TestClient

import flight_agent
from flight_agent.bootstrap.app import create_app


def test_flight_agent_import_smoke() -> None:
    assert flight_agent.__version__ == "0.1.0"


def test_create_app_returns_fastapi_app() -> None:
    assert isinstance(create_app(), FastAPI)


def test_healthz_returns_stable_payload_without_external_dependencies() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
