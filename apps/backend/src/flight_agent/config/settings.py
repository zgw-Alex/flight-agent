"""Backend settings sourced from environment variables."""

from functools import cached_property
from urllib.parse import quote

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed backend settings with a single derived database URL authority."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = Field(default="local", alias="APP_ENV")
    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT", ge=1, le=65535)
    postgres_host: str = Field(default="127.0.0.1", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=55432, alias="POSTGRES_PORT", ge=1, le=65535)
    postgres_db: str = Field(default="flight_agent", alias="POSTGRES_DB")
    postgres_user: str = Field(default="flight_agent", alias="POSTGRES_USER")
    postgres_password: str = Field(
        default="flight_agent_local_password",
        alias="POSTGRES_PASSWORD",
        min_length=1,
    )
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_default_model: str = Field(default="deepseek-v4-flash", alias="DEEPSEEK_DEFAULT_MODEL")
    deepseek_timeout_seconds: float = Field(default=15.0, alias="DEEPSEEK_TIMEOUT_SECONDS", gt=0)
    deepseek_total_deadline_seconds: float = Field(
        default=30.0, alias="DEEPSEEK_TOTAL_DEADLINE_SECONDS", gt=0
    )
    deepseek_max_attempts: int = Field(default=2, alias="DEEPSEEK_MAX_ATTEMPTS", ge=1)

    @field_validator(
        "app_env",
        "app_host",
        "postgres_host",
        "postgres_db",
        "postgres_user",
        "postgres_password",
        "deepseek_base_url",
        "deepseek_default_model",
    )
    @classmethod
    def reject_blank_strings(cls, value: str) -> str:
        """Reject blank values that would otherwise create ambiguous runtime config."""
        if value.strip() == "":
            raise ValueError("must not be blank")
        return value

    @cached_property
    def database_url(self) -> str:
        """Build the PostgreSQL URL from POSTGRES_* settings."""
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        database = quote(self.postgres_db, safe="")
        return (
            f"postgresql://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )

    @property
    def deepseek_configured(self) -> bool:
        return self.deepseek_api_key is not None and self.deepseek_api_key.strip() != ""
