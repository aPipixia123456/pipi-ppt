from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIPI_", extra="ignore")
    database_url: str = "postgresql+psycopg://pipi@postgres/pipi"
    redis_url: str = "redis://redis:6379/0"
    gateway_url: str = "https://api.example.invalid"
    public_url: str = "http://localhost:3000"
    research_provider: str = "gateway"
    research_model: str = ""  # Optional pipiapi model with /alpha/search access.
    minimax_api_key: str = ""
    minimax_api_host: str = "https://api.minimaxi.com"
    credential_key: str  # Fernet key, required; never generated into source files.
    storage_dir: Path = Path("/data")
    template_dir: Path = Path(__file__).resolve().parents[2] / "templates"
    exporter: Path = Path(__file__).resolve().parents[1] / "export" / "export.mjs"
    upload_limit: int = 20 * 1024 * 1024
    storage_quota: int = 1024 * 1024 * 1024
    request_timeout: int = 180
    converter_timeout: int = 90

    @field_validator("gateway_url", "public_url", "minimax_api_host")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("Expected an origin without credentials, path, query or fragment")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "gateway"}
        ):
            raise ValueError("HTTPS required except for local development")
        return value.rstrip("/")

    @field_validator("research_provider")
    @classmethod
    def validate_research_provider(cls, value: str) -> str:
        provider = value.strip().lower()
        if provider not in {"gateway", "minimax"}:
            raise ValueError("Unsupported research provider")
        return provider


@lru_cache
def settings() -> Settings:
    return Settings()
