from typing import Literal
from typing_extensions import Self
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

    # 메일 발송 (SMTP). 이메일 인증, 비밀번호 재설정 메일에 쓴다.
    # dev 에서 SMTP_HOST 가 비어 있으면 메일을 보내지 않고 링크를 로그에 남긴다.
    smtp_host: str = ""                         # 예: smtp.zoho.com (Zoho 데이터센터에 따라 .eu / .in / .com.au ...)
    smtp_port: int = 465
    smtp_security: Literal["ssl", "starttls"] = "ssl"   # 465 → ssl, 587 → starttls
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""                         # 보내는 주소. Zoho 는 로그인한 계정(또는 허용된 별칭)과 같아야 한다
    smtp_from_name: str = "여행길"
    email_verify_ttl_hours: int = 24            # 인증 링크 유효 시간
    password_reset_ttl_minutes: int = 60        # 재설정 링크 유효 시간

    # 클라이언트 IP 판별 (속도 제한의 키). app/clientip.py 참고.
    # 앞단 프록시(ingress-nginx 등)가 몇 단인지와, 그 프록시가 붙는 곳(사설 대역)을 지정한다.
    # 프록시를 거치지 않는 환경(로컬 개발)에서는 X-Forwarded-For 를 읽지 않는다.
    trusted_proxy_hops: int = 1
    trusted_proxy_cidrs: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,fc00::/7"

    # Cloudflare Turnstile (가입, 비밀번호 찾기, 인증 메일 재전송의 봇 방지).
    # 비어 있으면 검사하지 않는다. 값을 넣는 순서는 docs 참고: 웹에 사이트 키를 먼저 배포한 뒤 이 값을 넣는다.
    turnstile_secret_key: str = ""

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
            # 메일이 안 나가면 가입한 사람이 영영 로그인할 수 없으므로 운영에서는 필수
            if not (self.smtp_host and self.smtp_user and self.smtp_password and self.smtp_from):
                problems.append("SMTP_HOST/USER/PASSWORD/FROM 이 필요합니다")

        if problems:
            raise ValueError(f"[APP_ENV={self.app_env}] 잘못된 설정: " + "; ".join(problems))
        return self


# 필수 값은 환경 변수(.env)에서 읽는다. 타입 검사기는 생성자 인자가 빠졌다고 보므로 이 줄만 예외로 둔다.
settings = Settings()  # type: ignore[call-arg]