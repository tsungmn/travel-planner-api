# travel-planner-api

여행 계획 서비스의 백엔드 API입니다. 지도 위에서 일자별 방문 장소의 순서와 동선을 편집하는 웹/앱 클라이언트를 위해 인증, 데이터 저장, 장소 검색 기능을 제공합니다.

## 주요 기능

- 이메일/비밀번호 및 Google 계정 로그인 (서버 세션, HttpOnly 쿠키, Bearer 토큰 호환)
- 여행, 일자별 방문 장소, 항공편, 숙소 CRUD
- 장소 순서 및 일차 이동의 일괄·원자적 저장
- Google Places API (New) 프록시: 자동완성, 장소 상세 (사용자별 일일 호출 한도 적용)
- 읽기 전용 공유 링크

## 기술 스택

| 구분 | 사용 기술 |
|---|---|
| 언어/프레임워크 | Python 3.12, FastAPI |
| 데이터베이스 | PostgreSQL 17 (asyncpg) |
| 인증 | 서버 세션(토큰 해시 저장), Argon2 비밀번호 해시, Google OAuth (Authlib) |
| 실행 환경 | Docker, Docker Compose |

## 관련 저장소

| 저장소 | 역할 |
|---|---|
| `travel-planner-api` | 백엔드 API (본 저장소) |
| `travel-planner-web` | 웹 클라이언트 (Next.js) |
| `travel-planner-app` | 모바일 클라이언트 (예정) |

## 디렉터리 구조

```
travel-planner-api/
├─ app/
│  ├─ main.py          # 애플리케이션 진입점, 라우터 등록
│  ├─ config.py        # 환경 변수 설정
│  ├─ db.py            # DB 커넥션 풀
│  ├─ security.py      # 비밀번호/토큰 해시
│  ├─ deps.py          # 인증 및 소유자 검사 의존성
│  ├─ sql.py           # PATCH용 UPDATE 쿼리 생성
│  ├─ schemas.py       # 요청/응답 모델 및 입력 검증
│  └─ routers/         # auth, places, trips, stops, flights, lodgings
├─ db/migrations/      # SQL 마이그레이션 (파일명 순서로 적용)
├─ scripts/            # migrate.sh, backup.sh, restore.sh
├─ Dockerfile
├─ docker-compose.yml  # db + api
├─ requirements.txt
├─ .env.example        # 환경 변수 템플릿
└─ .gitattributes      # 줄바꿈(LF) 고정
```

## 요구 사항

- Docker 및 Docker Compose v2
- Python 3.12 (컨테이너 밖에서 개발 서버를 직접 실행하는 경우)
- Bash (`scripts/*.sh` 실행용. Windows에서는 Git Bash 또는 WSL)
- Google Cloud 프로젝트 (Google 로그인과 장소 검색을 사용하는 경우)

## 빠른 시작 (로컬 개발)

```bash
cp .env.example .env          # 값 입력 (아래 "환경 변수" 참고)

docker compose up -d db       # PostgreSQL 실행
scripts/migrate.sh            # 마이그레이션 적용

python -m venv .venv
source .venv/bin/activate     # Windows PowerShell: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

- API 문서(Swagger UI): `http://127.0.0.1:8000/api/docs`
- 상태 확인: `GET /api/health`
- Google 키가 비어 있어도 서버는 실행되며, 이메일 가입과 CRUD는 사용할 수 있습니다.
- 쿠키는 호스트별로 저장되므로 `localhost`와 `127.0.0.1`을 섞어 사용하지 않습니다.

## 환경 변수

`.env` 파일은 저장소에 커밋하지 않습니다. `.env.example`을 복사해 사용합니다.

| 변수 | 설명 | 예시 / 기본값 |
|---|---|---|
| `POSTGRES_USER` | DB 사용자 | `app` |
| `POSTGRES_PASSWORD` | DB 비밀번호 | `openssl rand -hex 24` |
| `POSTGRES_DB` | DB 이름 | `travel` |
| `DATABASE_URL` | 컨테이너 밖에서 API를 실행할 때 사용하는 접속 주소 | `postgres://app:<비밀번호>@127.0.0.1:5432/travel` |
| `SESSION_SECRET` | OAuth state 쿠키 서명 키 | `openssl rand -base64 32` |
| `PUBLIC_URL` | 사용자가 접속하는 웹 주소. OAuth 콜백 주소 생성에 사용 | `http://localhost:3000` |
| `COOKIE_SECURE` | 쿠키 Secure 속성. HTTPS 환경에서는 `true` | 기본 `true` |
| `DOCS_ENABLED` | `/api/docs`, `/api/openapi.json` 노출 여부 | 기본 `true` |
| `GOOGLE_CLIENT_ID` | Google OAuth 클라이언트 ID | |
| `GOOGLE_CLIENT_SECRET` | Google OAuth 클라이언트 시크릿 | |
| `GOOGLE_PLACES_SERVER_KEY` | Places API (New) 서버용 키 | |

참고 사항:

- `docker compose`로 `api` 컨테이너를 실행하면 `DATABASE_URL`은 compose 설정이 `db` 호스트 주소로 덮어씁니다.
- `POSTGRES_PASSWORD`는 DB 데이터 디렉터리(`data/pg`)가 처음 생성될 때만 적용됩니다. 이후 값을 바꿔도 기존 DB의 비밀번호는 변경되지 않습니다.
- `GOOGLE_*` 변수는 값이 비어 있어도 되지만, 변수 선언 자체는 `.env`에 있어야 합니다.
- 비밀 값은 환경마다 새로 생성하는 것을 권장합니다. 환경 간에 공유가 필요한 값은 Google이 발급하는 키 3종뿐입니다.

## Google 설정

### OAuth 클라이언트 (로그인)

- 애플리케이션 유형: **웹 애플리케이션**
- 승인된 JavaScript 원본: 비워 둡니다. (서버가 리다이렉트하는 방식이므로 사용하지 않습니다.)
- 승인된 리디렉션 URI: `{PUBLIC_URL}/api/auth/google/callback`
  - 로컬: `http://localhost:3000/api/auth/google/callback`
  - 운영: `https://<도메인>/api/auth/google/callback`
- 동의 화면의 사용자 유형은 External, 스코프는 `openid`, `email`, `profile`입니다. 게시 상태가 Testing이면 등록된 테스트 사용자만 로그인할 수 있습니다.

### Places API 키 (장소 검색)

- **Places API (New)** 를 활성화하고 API 키를 발급합니다.
- API 제한을 `Places API (New)`로 지정합니다.
- 비용 통제를 위해 해당 API의 일일 요청 상한(Quotas)을 설정합니다.
- 지도 표시용 브라우저 키(Maps JavaScript API, HTTP referrer 제한)는 이 키와 분리하여 웹 클라이언트에서 별도로 사용합니다.

## 데이터베이스 마이그레이션

`db/migrations/*.sql`을 파일명 순서로 적용하며, 적용 이력은 `schema_migrations` 테이블에 기록됩니다.

```bash
scripts/migrate.sh
```

스키마 변경은 기존 파일을 수정하지 않고 `002_*.sql`과 같이 새 파일을 추가합니다.

## API 개요

모든 경로는 `/api` 하위에 있으며, 별도 표기가 없으면 로그인이 필요합니다. 상세 스키마는 `/api/docs`에서 확인할 수 있습니다.

| 분류 | 메서드 | 경로 | 설명 |
|---|---|---|---|
| 인증 | POST | `/auth/register` | 이메일 가입 (공개) |
| | POST | `/auth/login` | 이메일 로그인 (공개) |
| | POST | `/auth/logout` | 로그아웃 |
| | GET | `/auth/me` | 현재 사용자 |
| | GET | `/auth/google`, `/auth/google/callback` | Google 로그인 (공개) |
| 장소 검색 | GET | `/places/autocomplete` | 장소 자동완성 |
| | GET | `/places/details` | 장소 상세 (이름, 주소, 좌표) |
| 여행 | GET, POST | `/trips` | 목록 조회, 생성 |
| | GET, PATCH, DELETE | `/trips/{trip_id}` | 상세(하위 데이터 포함), 수정, 삭제 |
| 공유 | POST, DELETE | `/trips/{trip_id}/share` | 공유 링크 활성화, 해제 |
| | GET | `/shared/{token}` | 공유된 여행 조회 (공개) |
| 장소 | POST | `/trips/{trip_id}/stops` | 일차의 마지막에 장소 추가 |
| | PUT | `/trips/{trip_id}/stops/order` | 순서/일차 일괄 저장 |
| | PATCH, DELETE | `/trips/{trip_id}/stops/{stop_id}` | 수정, 삭제 |
| 항공편 | POST | `/trips/{trip_id}/flights` | 추가 |
| | PATCH, DELETE | `/trips/{trip_id}/flights/{flight_id}` | 수정, 삭제 |
| 숙소 | POST | `/trips/{trip_id}/lodgings` | 추가 |
| | PATCH, DELETE | `/trips/{trip_id}/lodgings/{lodging_id}` | 수정, 삭제 |
| 상태 | GET | `/health` | DB 연결 확인 (공개) |

### 인증 방식

- 웹: 로그인 시 `sid` 쿠키(HttpOnly, SameSite=Lax)가 설정됩니다.
- 앱: `Authorization: Bearer <토큰>` 헤더를 같은 세션으로 처리합니다.
- 세션 토큰은 SHA-256 해시로만 DB에 저장되며, 유효 기간은 30일입니다. 만료된 세션은 서버 시작 시 정리됩니다.

### 사용 한도

| 항목 | 한도 |
|---|---|
| 사용자당 여행 수 | 30 |
| 여행당 방문 장소 / 항공편 / 숙소 | 200 / 10 / 20 |
| 여행 기간 | 최대 61일 |
| 장소 자동완성 / 상세 (사용자당 일일) | 300 / 150 |

일일 한도는 DB의 `current_date`(UTC) 기준으로 초기화됩니다.

### 주요 입력 규칙

- 항공편명은 공백 제거 후 대문자로 정규화되며(`mu 2040` → `MU2040`), 공항 코드는 IATA 3글자, 시간대는 IANA 이름(예: `Asia/Seoul`)이어야 합니다.
- 항공편 시각은 타임존 없는 현지 시각과 `dep_tz`/`arr_tz`를 함께 저장합니다.
- 방문 장소의 `day_index`는 0부터 시작하며 여행 일수 범위 안이어야 합니다.
- PATCH는 전달된 필드만 수정하며, null을 허용하지 않는 필드에 null을 보내면 422를 반환합니다.
- 다른 사용자의 여행에 접근하면 존재 여부를 노출하지 않도록 404를 반환합니다.

## 배포 (Ubuntu 서버)

Ubuntu 22.04 / 24.04 LTS와 Docker Compose를 기준으로 합니다.

### 1. Docker 설치

공식 apt 저장소 방식입니다. 절차는 변경될 수 있으므로 Docker 공식 문서("Install Docker Engine on Ubuntu")를 함께 확인합니다.

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" \
| sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```

그룹 적용을 위해 재로그인한 뒤 확인합니다.

```bash
docker run --rm hello-world
docker compose version
sudo systemctl is-enabled docker    # enabled: 재부팅 후 자동 시작
```

### 2. 소스 배치와 환경 설정

```bash
sudo mkdir -p /srv && sudo chown $USER:$USER /srv
cd /srv
git clone <repository-url> travel-planner-api
cd travel-planner-api
chmod +x scripts/*.sh

cp .env.example .env
nano .env
chmod 600 .env
```

- `.env`와 `data/` 디렉터리는 다른 환경에서 복사하지 않습니다. `.env`는 `.env.example`로부터 새로 작성하고 비밀 값은 서버에서 생성합니다.
- 다른 환경에서 만든 `.env` 파일을 가져온 경우 줄바꿈을 변환합니다: `sed -i 's/\r$//' .env`
- `PUBLIC_URL`에 HTTPS 주소가 없는 단계에서는 `COOKIE_SECURE=false`로 설정합니다. HTTP 연결에서 `true`이면 로그인 쿠키가 저장되지 않습니다.

### 3. 실행

```bash
docker compose up -d db
docker compose ps                   # db 상태가 healthy인지 확인
scripts/migrate.sh
docker compose up -d --build api
docker compose logs -f api          # "Application startup complete" 확인
curl http://127.0.0.1:8000/api/health
```

`docker-compose.yml`은 `db`(5432)와 `api`(8000)를 모두 `127.0.0.1`에만 바인딩합니다. 외부 공개는 리버스 프록시나 터널(예: Cloudflare Tunnel)을 통해 별도로 구성합니다. Docker가 게시한 포트는 ufw 규칙을 우회할 수 있으므로 바인딩 주소를 `0.0.0.0`으로 변경하지 않습니다.

원격 서버의 API 문서를 로컬 브라우저에서 확인하려면 SSH 포트 포워딩을 사용합니다.

```bash
ssh -L 8000:127.0.0.1:8000 <user>@<server-ip>
# 이후 http://127.0.0.1:8000/api/docs
```

### 4. 방화벽

```bash
sudo ufw allow OpenSSH              # 활성화 전에 먼저 허용 (SSH 접속 차단 방지)
sudo ufw enable
sudo ufw status
ss -tlnp | grep -E '5432|8000'      # 127.0.0.1에서만 listen하는지 확인
```

### 5. 재부팅 후 자동 시작 확인

컨테이너는 `restart: unless-stopped`로 설정되어 있어 서버 재부팅 후 자동으로 시작됩니다.

```bash
sudo reboot
docker compose ps
curl http://127.0.0.1:8000/api/health
```

## 운영

### HTTPS 도메인 연결 시 설정

- `.env`: `PUBLIC_URL=https://<도메인>`, `COOKIE_SECURE=true`, `DOCS_ENABLED=false`
- Google OAuth 클라이언트의 승인된 리디렉션 URI에 `https://<도메인>/api/auth/google/callback` 추가
- 프록시 계층에서 `/api/auth/login`, `/api/auth/register`에 요청 빈도 제한(rate limiting) 적용
- Places 서버 키의 API 제한과 일일 쿼터 상한 확인
- 설정 변경 후 `docker compose up -d api`로 재시작

### 업데이트 배포

```bash
cd /srv/travel-planner-api
git pull
scripts/migrate.sh                  # 미적용 마이그레이션만 적용
docker compose up -d --build api
```

### 백업

`scripts/backup.sh`는 `pg_dump` 결과를 gzip으로 저장하고 오래된 파일을 삭제합니다.

| 환경 변수 | 기본값 | 설명 |
|---|---|---|
| `BACKUP_DIR` | `~/backups/travel-planner` | 백업 저장 경로 |
| `KEEP_DAYS` | `14` | 보관 일수 |
| `RCLONE_REMOTE` | (없음) | 지정 시 rclone으로 외부 저장소에 복사 (예: `mycloud:travel-backups`) |

수동 실행 후 정상 동작을 확인하고 cron에 등록합니다.

```bash
scripts/backup.sh

crontab -e
# 매일 04:00 (서버 시간 기준)
0 4 * * * cd /srv/travel-planner-api && scripts/backup.sh >> $HOME/travel-backup.log 2>&1
```

동일 서버의 디스크에만 보관하면 디스크 장애 시 함께 손실되므로 외부 저장소로의 복사를 권장합니다.

### 복원

```bash
scripts/restore.sh <backup.sql.gz>
```

현재 DB를 삭제하고 백업 내용으로 교체합니다. 실행 전 확인 절차가 있으며, `api` 컨테이너를 중지했다가 다시 시작합니다. 복원 절차는 실제 데이터를 사용하기 전에 한 번 검증해 두는 것을 권장합니다.

### 다른 환경의 데이터 이전

개발 환경의 테스트 데이터는 이전하지 않고 새로 시작하는 것을 기본으로 합니다. 이전이 필요한 경우 원본 환경에서 덤프를 생성합니다. PowerShell의 `>` 리다이렉션은 파일을 UTF-16으로 저장하므로 컨테이너 안에서 파일을 만든 뒤 꺼냅니다.

```bash
docker compose exec -T db sh -c "pg_dump -U app travel | gzip > /tmp/dump.sql.gz"
docker compose cp db:/tmp/dump.sql.gz ./dump.sql.gz
```

대상 서버에서는 `migrate.sh`를 실행하지 않고 덤프로 대체합니다.

```bash
docker compose up -d --build
scripts/restore.sh dump.sql.gz
```

## Windows 개발 환경 참고

Windows에서 작성한 셸 스크립트가 CRLF로 저장되면 Linux에서 `bash\r: No such file or directory` 오류가 발생합니다. 저장소 루트에 아래 `.gitattributes`를 두어 줄바꿈을 LF로 고정합니다.

```gitattributes
*.sh   text eol=lf
*.sql  text eol=lf
*.py   text eol=lf
*.yml  text eol=lf
Dockerfile text eol=lf
```

이미 CRLF로 커밋된 경우 다음 명령으로 정규화합니다.

```bash
git add --renormalize . && git commit -m "normalize line endings"
```

실행 권한이 보존되지 않은 경우 `chmod +x scripts/*.sh`를 실행합니다.

## 문제 해결

| 증상 | 원인 및 조치 |
|---|---|
| `bash\r: No such file or directory` | 스크립트가 CRLF입니다. `sed -i 's/\r$//' scripts/*.sh` 후 `.gitattributes`를 적용합니다. |
| 스크립트 실행 시 `Permission denied` | `chmod +x scripts/*.sh` |
| `permission denied ... docker.sock` | `sudo usermod -aG docker $USER` 후 재로그인 |
| `port 5432 is already allocated` | 호스트에 PostgreSQL이 이미 실행 중입니다. 중지하거나 compose의 포트 매핑을 `127.0.0.1:5433:5432`로 변경합니다. |
| `password authentication failed for user "app"` | `.env`의 비밀번호가 DB 최초 생성 시 값과 다릅니다. 데이터가 불필요하면 `docker compose down && rm -rf data/pg` 후 재시작합니다. |
| 로그인 직후 401 | HTTP 연결에서 `COOKIE_SECURE=true`로 설정된 경우입니다. HTTP 환경에서는 `false`로 설정합니다. |
| `redirect_uri_mismatch` | Google에 등록한 리디렉션 URI와 `{PUBLIC_URL}/api/auth/google/callback`이 일치하지 않습니다. |
| `api` 컨테이너가 반복 재시작 | `docker compose logs api`를 확인합니다. 대개 `.env`의 필수 변수 누락이 원인입니다. |
| `scripts/*.sh`가 DB 사용자명을 찾지 못함 | `.env`가 CRLF입니다. `sed -i 's/\r$//' .env` |

## 유용한 명령어

```bash
docker compose ps                          # 상태 확인
docker compose logs -f api                 # API 로그
docker compose restart api                 # API 재시작
docker compose exec db psql -U app travel  # DB 접속
docker compose down                        # 중지 (데이터는 ./data/pg에 유지)
```

## 보안 참고

- `.env`, `data/`, `backups/`는 `.gitignore`에 포함되어 있으며 커밋하지 않습니다.
- 이메일 인증 메일 발송은 아직 구현되어 있지 않습니다. 같은 이메일로 Google 로그인이 이루어지면 기존 비밀번호 계정은 Google 계정에 연결되고, 기존 비밀번호와 세션은 폐기됩니다.
- 로그인 시도 횟수 제한은 애플리케이션에 포함되어 있지 않으므로 프록시 계층에서 적용합니다.
- 비밀번호 재설정 기능은 아직 제공되지 않습니다.