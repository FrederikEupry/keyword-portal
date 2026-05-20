from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    session_secret: str = "dev-secret-change-me"
    app_base_url: str = "http://localhost:8000"
    debug: bool = False

    google_client_id: str = ""
    google_client_secret: str = ""
    allowed_email_domain: str = "eupry.com"

    dataforseo_login: str = ""
    dataforseo_password: str = ""

    max_cost_per_run_usd: float = 2.00
    eupry_domain: str = "eupry.com"

    competitors_sheet_id: str = ""
    google_service_account_json: str = ""

    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4.6"
    openrouter_app_url: str = ""
    openrouter_app_title: str = "Eupry Keyword Portal"

    db_path: str = "data/portal.db"
    dossier_dir: str = "data/dossiers"


@lru_cache
def get_settings() -> Settings:
    return Settings()
