from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    session_secret: str
    public_url: str
    cookie_secure: bool = True
    docs_enabled: bool = True   # 운영에서 /api/docs 를 숨기려면 DOCS_ENABLED=false
    google_client_id: str
    google_client_secret: str
    geoapify_api_key: str = ""  # 비어 있으면 mode=suggest 가 503 을 반환
    nominatim_user_agent: str = "travel-planner-api/0.1"    # 연락처를 넣을 것 (공용 서버 정책)

    @field_validator("public_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")


settings = Settings()