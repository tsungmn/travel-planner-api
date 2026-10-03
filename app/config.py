from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    session_secret: str
    public_url: str
    cookie_secure: bool = True
    docs_enabled: bool = True          # 운영에서 /api/docs 를 숨기려면 DOCS_ENABLED=false
    google_client_id: str
    google_client_secret: str
    google_places_server_key: str

    @field_validator("public_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")


settings = Settings()