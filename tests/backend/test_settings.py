from pydantic import ValidationError

from flight_agent.config import Settings


def test_settings_defaults_derive_database_url_without_external_services() -> None:
    settings = Settings.model_validate({})

    assert settings.app_env == "local"
    assert settings.app_host == "127.0.0.1"
    assert settings.app_port == 8000
    assert settings.postgres_host == "127.0.0.1"
    assert settings.postgres_port == 55432
    assert settings.database_url == (
        "postgresql://flight_agent:flight_agent_local_password"
        "@127.0.0.1:55432/flight_agent"
    )


def test_settings_use_postgres_parts_as_database_url_authority(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "25432")
    monkeypatch.setenv("POSTGRES_DB", "flight_agent_test")
    monkeypatch.setenv("POSTGRES_USER", "flight_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "local secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://ignored:ignored@example.com/ignored")

    settings = Settings()

    assert settings.database_url == (
        "postgresql://flight_user:local%20secret@localhost:25432/flight_agent_test"
    )


def test_settings_reject_invalid_postgres_port() -> None:
    try:
        Settings.model_validate({"POSTGRES_PORT": 70000})
    except ValidationError as exc:
        assert "POSTGRES_PORT" in str(exc)
    else:
        raise AssertionError("Expected invalid POSTGRES_PORT to fail validation")


def test_settings_reject_blank_postgres_password() -> None:
    try:
        Settings.model_validate({"postgres_password": " "})
    except ValidationError as exc:
        assert "postgres_password" in str(exc)
    else:
        raise AssertionError("Expected blank POSTGRES_PASSWORD to fail validation")
