"""
Centralized, env-driven settings.

Everything that differs between local dev / staging / prod (DB url, mock
flags, thresholds used by the rules engine) lives here so no other module
reaches into os.environ directly.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "sqlite:///./timesheet_ai.db"

    # Workfront
    workfront_mode: str = "mock"  # "mock" | "real"
    workfront_base_url: str = ""

    # Auth: prefer client-credentials auto-refresh (client_id + client_secret).
    # workfront_api_key is kept as a fallback for a static pasted token, but
    # if client_id/secret are set, the app fetches and refreshes its own
    # tokens automatically — no manual pasting or expiry babysitting needed.
    workfront_client_id: str = ""
    workfront_client_secret: str = ""
    workfront_token_url: str = "https://ims-na1.adobelogin.com/ims/token/v3"
    workfront_token_scope: str = "openid,AdobeID,read_organizations,additional_info.projectedProductContext"
    workfront_api_key: str = ""  # fallback static token (legacy / manual mode)

    # LLM
    mock_llm: bool = True
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"

    # Business rules (mirrors the Rules Engine table in the architecture doc)
    default_expected_hours: float = 40.0
    non_standard_util_threshold: float = 0.3
    recommendation_staleness_minutes: int = 30

    # Real-time
    redis_url: str = "redis://localhost:6379/0"

    # App
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()