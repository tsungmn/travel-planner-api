from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# dev 환경에서 접속을 허용하는 DB 호스트 (로컬 / docker compose 의 db 서비스)
LOCAL_DB_HOSTS = {"localhost", "127.0.0.1", "::1", "db", "host.docker.internal"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "prod"] = "dev"   # prod 는 클러스터의 ConfigMap 에서만 지정
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

    @model_validator(mode="after")
    def _guard_environment(self) -> Self:
        """dev 와 prod 설정이 섞이면 서버가 시작되지 않도록 막는다."""
        db_host = urlparse(self.database_url).hostname or ""
        problems: list[str] = []

        if self.app_env == "dev":
            # dev 는 로컬 DB 에만 붙을 수 있다 (prod DB 오염 방지)
            if db_host not in LOCAL_DB_HOSTS:
                problems.append(f"dev 환경에서 로컬이 아닌 DB 호스트({db_host})는 허용되지 않습니다")
        else:
            public_host = urlparse(self.public_url).hostname or ""
            if db_host in LOCAL_DB_HOSTS:
                problems.append("prod 환경에서 로컬 DB 주소는 허용되지 않습니다")
            if not self.public_url.startswith("https://") or public_host in {"localhost", "127.0.0.1"}:
                problems.append("PUBLIC_URL 은 https 운영 도메인이어야 합니다")
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE 는 true 여야 합니다")
            if self.docs_enabled:
                problems.append("DOCS_ENABLED 는 false 여야 합니다")
            if len(self.session_secret) < 32:
                problems.append("SESSION_SECRET 은 32자 이상이어야 합니다")
            if not (self.google_client_id and self.google_client_secret):
                problems.append("GOOGLE_CLIENT_ID/SECRET 이 필요합니다")
            if not self.geoapify_api_key:
                problems.append("GEOAPIFY_API_KEY 가 필요합니다")

        if problems:
            raise ValueError(f"[APP_ENV={self.app_env}] 잘못된 설정: " + "; ".join(problems))
        return self


settings = Settings()