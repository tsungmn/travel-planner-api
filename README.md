# travel-planner-api

여행 계획 서비스 '여행길'의 백엔드 API입니다. 인증, 여행 데이터 저장, 장소 검색을 제공합니다.

## 주요 기능

- 이메일/비밀번호 및 Google 계정 로그인 (서버 세션)
- 여행, 일자별 방문 장소, 항공편, 숙소 CRUD
- 장소의 순서와 일차 이동을 한 번에, 전부 성공하거나 전부 취소되게 저장
- 장소 검색: 입력 중 제안(Geoapify)과 검색 확정 시 전체 검색(Nominatim)을 하나의 엔드포인트로 제공하며, 사용자별 일일 호출 한도 적용
- 읽기 전용 공유 링크 (로그인 없이 조회)

## 기술 스택

| 구분 | 사용 기술 |
| --- | --- |
| 언어 | 프레임워크 | Python 3.12, FastAPI, Uvicorn |
| 데이터베이스 | PostgreSQL 17 (asyncpg, 순수 SQL 마이그레이션) |
| 인증 | 서버 세션(토큰은 SHA-256 해시로 저장), Argon2 비밀번호 해시, Google OAuth (Authlib) |
| 장소 검색 | Geoapify Geocoding Autocomplete, OpenStreetMap Nominatim |
| 실행 환경 | Docker, Docker Compose |

## 관련 저장소

| 저장소 | 역할 |
| --- | --- |
| `travel-planner-api` | 백엔드 API (이 저장소) |
| `travel-planner-web` | 웹 클라이언트 (Next.js). `/api/*` 요청을 이 API로 프록시합니다. |
| `travel-planner-app` | 모바일 클라이언트 (예정) |

## 시작하기 (로컬 개발)

### 요구 사항

- Docker, Docker Compose v2
- Python 3.12

### 실행

```bash
cp .env.example .env            # 값 입력 (아래 "환경 변수" 참고)

docker compose up -d db         # PostgreSQL 실행

python -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\activate
pip install -r requirements.txt
python -m app.migrate           # 마이그레이션 적용
uvicorn app.main:app --reload --port 8000
```

- API 문서(Swagger UI): `http://127.0.0.1:8000/api/docs`
- 웹 클라이언트와 함께 실행하려면 웹 저장소의 `API_ORIGIN`을 `http://127.0.0.1:8000`으로 지정합니다.
- 쿠키는 호스트별로 저장되므로 `localhost`와 `127.0.0.1`을 섞어 쓰지 않습니다.
- `docker compose up -d --build`로 API까지 컨테이너로 실행할 수도 있습니다. 이때 `DATABASE_URL`은 compose 설정이 `db` 호스트 주소로 덮어씁니다.

Google OAuth 키가 비어 있어도 서버는 실행됩니다. 이메일 가입, CRUD, 전체 검색(`mode=full`)은 사용할 수 있고, Google 로그인은 OAuth 키가, `mode=suggest`는 Geoapify 키가 있어야 동작합니다.

## 환경 변수

`.env.example`을 복사해 `.env`를 만듭니다. `.env`는 커밋하지 않습니다.

| 변수 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `APP_ENV` | 아니오 | `dev` | 실행 환경 `dev` 또는 `prod`. 아래 "환경 검증" 참고 |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | compose 사용 시 | - | DB 컨테이너 설정. 비밀번호는 `openssl rand -hex 24` 권장 |
| `DATABASE_URL` | 예 | - | DB 접속 주소. 예: `postgres://app:<비밀번호>@127.0.0.1:5432/travel` |
| `SESSION_SECRET` | 예 | - | OAuth state 쿠키 서명 키. `openssl rand -base64 32` |
| `PUBLIC_URL` | 예 | - | 사용자가 접속하는 웹 주소. OAuth 콜백 주소와 로그인 후 이동 주소에 사용 |
| `COOKIE_SECURE` | 아니오 | `true` | 쿠키 Secure 속성. HTTP 개발 환경에서는 `false` |
| `DOCS_ENABLED` | 아니오 | `true` | `/api/docs`, `/api/openapi.json` 노출 여부 |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | 예 | - | Google OAuth 클라이언트. 값은 비어 있어도 되지만 변수 선언은 필요합니다. |
| `GEOAPIFY_API_KEY` | 아니오 | 빈 값 | 입력 중 제안(`mode=suggest`)용. 비어 있으면 해당 모드는 503 |
| `NOMINATIM_USER_AGENT` | 아니오 | `travel-planner-api/0.1` | Nominatim 호출 시 보내는 User-Agent. 연락처 포함 필요 (공용 서버 정책) |

- `POSTGRES_PASSWORD`는 DB 데이터 디렉터리(`data/pg`)가 처음 만들어질 때만 적용됩니다. 이후에 바꿔도 기존 DB의 비밀번호는 바뀌지 않습니다.
- 비밀 값은 환경마다 새로 생성합니다. 환경 간에 공유가 필요한 값은 외부 서비스가 발급하는 키뿐입니다.

### 환경 검증

설정이 잘못되면 서버가 시작되지 않으며, 개발 환경과 운영 환경의 설정이 섞이는 것을 막기 위한 장치입니다.

| `APP_ENV` | 검증 내용 |
| --- | --- |
| `dev` | DB 호스트가 로컬(`localhost`, `127.0.0.1`, `::1`, `db`, `host.docker.internal`)이어야 합니다. |
| `prod` | DB 호스트가 로컬이 아니어야 하고, `PUBLIC_URL`은 `https` 운영 도메인, `COOKIE_SECURE=true`, `DOCS_ENABLED=false`, `SESSION_SECRET` 32자 이상, Google OAuth 키와 Geoapify 키가 모두 필요합니다. |

따라서 이 저장소의 `docker-compose.yml`(DB 호스트 `db`)은 개발용입니다. 운영(`prod`) 설정은 배포 환경(예: 클러스터의 ConfigMap)에서 주입하는 것을 전제로 합니다.

### Google OAuth 설정

- 애플리케이션 유형: 웹 애플리케이션
- 승인된 JavaScript 원본: 비워 둡니다. (서버 리다이렉트 방식)
- 승인된 리디렉션 URI: `{PUBLIC_URL}/api/auth/google/callback`
- 스코프: `openid`, `email`, `profile`. 동의 화면이 Testing 상태이면 등록된 테스트 사용자만 로그인할 수 있습니다.

Google Cloud는 로그인에만 사용하며 Places API 등 결제가 필요한 API는 쓰지 않습니다.

## 인증

- **웹**: 로그인하면 `sid` 쿠키(HttpOnly, SameSite=Lax)가 설정됩니다.
- **앱 / Swagger UI**: `Authorization: Bearer <토큰>` 헤더를 같은 세션으로 처리합니다. 토큰은 로그인·가입 응답의 `token` 필드로 받습니다. 헤더와 쿠키가 모두 있으면 헤더를 우선합니다.
- 세션 유효 기간은 30일이며, 만료된 세션은 서버 시작 시 삭제합니다.
- 비밀번호는 8~128자입니다. 존재하지 않는 계정이나 Google 전용 계정으로 로그인해도 같은 비용의 해시 검증을 수행해, 응답 시간으로 계정 존재 여부가 드러나지 않게 합니다.
- Google 로그인은 이메일 인증이 완료된 계정만 허용합니다. 같은 이메일의 비밀번호 계정이 이미 있으면 Google 계정에 연결하며, 이때 기존 비밀번호와 세션은 폐기합니다. (가입 시 이메일 인증이 없어서, 이메일을 선점한 계정이 남지 않게 하기 위함)

## API

모든 경로는 `/api` 아래에 있으며, 별도 표기가 없으면 로그인이 필요합니다. 상세 스키마는 `/api/docs`에서 확인할 수 있습니다.

| 분류 | 메서드 | 경로 | 설명 |
| --- | --- | --- | --- |
| 인증 | POST | `/auth/register` | 이메일 가입 (공개) |
| | POST | `/auth/login` | 이메일 로그인 (공개) |
| | POST | `/auth/logout` | 로그아웃 |
| | GET | `/auth/me` | 현재 사용자 |
| | GET | `/auth/google`, `/auth/google/callback` | Google 로그인 (공개) |
| 장소 검색 | GET | `/places/search` | 장소 검색 |
| 여행 | GET, POST | `/trips` | 목록, 생성 |
| | GET, PATCH, DELETE | `/trips/{trip_id}` | 상세(하위 데이터 포함), 수정, 삭제 |
| 공유 | POST, DELETE | `/trips/{trip_id}/share` | 공유 링크 켜기, 끄기 |
| | GET | `/shared/{token}` | 공유된 여행 조회 (공개) |
| 장소 | POST | `/trips/{trip_id}/stops` | 일차의 맨 뒤에 추가 |
| | PUT | `/trips/{trip_id}/stops/order` | 일차 이동과 순서 일괄 저장 |
| | PATCH, DELETE | `/trips/{trip_id}/stops/{stop_id}` | 수정, 삭제 |
| 영역 | POST | `/trips/{trip_id}/areas` | 러프 계획 영역(원) 추가. `day_index`가 없으면 일차 미지정 |
| | PATCH, DELETE | `/trips/{trip_id}/areas/{area_id}` | 수정, 삭제 |
| 항공편 | POST | `/trips/{trip_id}/flights` | 추가 |
| | PATCH, DELETE | `/trips/{trip_id}/flights/{flight_id}` | 수정, 삭제 |
| 숙소 | POST | `/trips/{trip_id}/lodgings` | 추가 |
| | PATCH, DELETE | `/trips/{trip_id}/lodgings/{lodging_id}` | 수정, 삭제 |
| 상태 | GET | `/healthz` | 프로세스 확인 (DB 미확인) |
| | GET | `/readyz`, `/health` | DB 연결 확인 (`/health`는 기존 경로 호환) |

### 사용 한도

| 항목 | 한도 | 초과 시 |
| --- | --- | --- |
| 사용자당 여행 수 | 30 | `409 too_many_trips` |
| 여행 기간 | 최대 61일 | `422` |
| 여행당 방문 장소 | 200 | `409 too_many_stops` |
| 여행당 항공편 | 10 | `409 too_many_flights` |
| 여행당 숙소 | 20 | `409 too_many_lodgings` |
| 여행당 영역 | 100 | `409 too_many_areas` |
| 순서 일괄 저장 1회 | 500건 | `422` |
| 장소 검색 (사용자당 하루, `suggest`와 `full` 합산) | 500 | `429 rate_limited` |

검색 한도는 DB의 `current_date` 기준으로 초기화됩니다. DB 서버의 시간대가 기본값(UTC)이면 UTC 자정에 초기화됩니다.

### 오류 응답

오류는 `{"detail": "<코드>"}` 형태입니다. 입력 검증 오류는 `{"detail": "validation_error", "fields": ["body.name", ...]}`로 문제 필드 위치를 함께 내려줍니다.

| 상태 | 코드 |
| --- | --- |
| 400 | `bad_request` |
| 401 | `unauthorized`, `invalid_credentials` |
| 404 | `not_found` |
| 409 | `email_taken`, `too_many_trips`, `too_many_stops`, `too_many_flights`, `too_many_lodgings`, `too_many_areas`, `stops_out_of_range` |
| 422 | `validation_error`, `constraint_violation`, `invalid_dates`, `day_out_of_range`, `duplicate_stop`, `unknown_stop`, `no_fields`, `<필드>_cannot_be_null` |
| 429 | `rate_limited` |
| 502 | `upstream_error` |
| 503 | `search_not_configured` |

## 장소 검색

`GET /api/places/search`는 용도에 따라 두 공급자를 나누어 호출합니다.

| `mode` | 공급자 | 용도 | 비고 |
| --- | --- | --- | --- |
| `suggest` (기본) | Geoapify Autocomplete | 입력 중 제안 | API 키 필요. 입력마다 호출되므로 클라이언트에서 디바운스를 적용합니다. |
| `full` | OpenStreetMap Nominatim (공용 서버) | 검색 확정(Enter, 버튼) 시 전체 검색 | 키 불필요. 서버가 호출 간격을 1.1초 이상으로 직렬화합니다. |

| 파라미터 | 설명 |
| --- | --- |
| `q` | 검색어 (2~100자) |
| `mode` | `suggest` 또는 `full` |
| `lang` | 결과 표시 언어 `ko`, `en`, `zh`, `ja`. 그 외 값이나 생략 시 언어를 지정하지 않음 |
| `lat`, `lng` | 여행지 중심 좌표 (여행의 `dest_lat`, `dest_lng`) |
| `nearby` | 기본 `true`. `lat`/`lng` 중심 약 50km 안에서만 검색. `false`이면 전 세계에서 검색 (여행지 검색 등) |

응답은 최대 8건입니다.

```json
{
  "items": [
    {
      "provider": "nominatim",
      "provider_place_id": "node/<osm_id>",
      "name": "栈桥",
      "address_text": "栈桥, 泰安路社区, 中山路街道, 스난구, 칭다오시, 산둥성, 266001, 중국",
      "lat": 36.0619734,
      "lng": 120.3143809
    }
  ],
  "attribution": "© OpenStreetMap contributors"
}
```

- 검색 결과를 보여주는 화면에는 응답의 `attribution`을 표시해야 합니다.
- 지도에서 직접 지정한 장소는 `provider: "manual"`로 저장하며 `provider_place_id`는 null입니다.
- 공급자 호출이 실패하면 `502 upstream_error`입니다. 공급자 응답의 상세 내용과 API 키는 클라이언트에 노출하지 않습니다.
- Geoapify의 `place_id`가 200자를 넘으면 `sha256:` 해시로 줄여 저장합니다.
- Nominatim 공용 서버는 입력 중 자동완성 용도의 호출을 허용하지 않고, 초당 1회 이하 요청과 식별 가능한 User-Agent를 요구합니다. 요청 간격 제한은 Uvicorn 워커 1개 기준으로 동작합니다.
- 검색 품질: 중국어·영어 입력은 주요 관광지와 식당에서 잘 동작합니다. 한국어 입력은 OpenStreetMap에 한국어 이름이 등록된 장소만 검색되며, `lang=ko`는 한국어 이름이 있는 항목의 표시만 한국어로 바꿉니다.
- 공급자 모듈은 `app/providers/`에 분리되어 있어, 같은 시그니처의 모듈을 추가하고 `routers/places.py`의 호출부만 바꾸면 교체할 수 있습니다.
- 각 공급자의 무료 한도, 결과 저장 가능 여부, 표기 요건은 사용 전에 공급자 약관에서 확인해야 합니다.

## 데이터베이스

PostgreSQL 17을 사용하며, 스키마는 `db/migrations/*.sql`을 파일명 순서로 적용합니다. 적용 이력은 `schema_migrations` 테이블에 기록됩니다. 스키마를 바꿀 때는 기존 파일을 수정하지 않고 새 파일을 추가합니다.

| 파일 | 내용 |
| --- | --- |
| `001_init.sql` | 초기 스키마: `users`, `sessions`, `trips`, `flights`, `lodgings`, `stops`, `place_usage` |
| `002_search_provider.sql` | 장소·숙소의 검색 공급자 컬럼(`provider`, `provider_place_id`), 검색 사용량 종류 `search` 추가 |
| `003_stop_alias.sql` | 장소의 별칭(`alias`) 컬럼 |
| `004_trip_areas.sql` | 러프 계획 영역 `trip_areas` (일차, 라벨, 중심 좌표, 반경 100~5000m, 메모) |

- 여행을 삭제하면 장소, 항공편, 숙소, 영역이 함께 삭제됩니다. 여행 기간을 줄여 범위를 벗어난 영역은 삭제하지 않고 일차 미지정(`day_index = null`)으로 돌립니다. 사용자를 삭제하면 세션과 여행도 삭제됩니다.
- 세션 토큰은 해시로만 저장하므로 DB가 유출되어도 토큰을 재사용할 수 없습니다.

마이그레이션은 `python -m app.migrate`로 적용합니다. `.env`(또는 환경 변수)의 `DATABASE_URL`로 접속하고, advisory lock으로 동시 실행을 막으며, 이미 적용된 파일은 건너뜁니다.

```bash
python -m app.migrate                       # 호스트(venv)에서
docker compose run --rm api python -m app.migrate   # 컨테이너에서 (DATABASE_URL은 compose가 db 호스트로 지정)
```

## 스크립트

| 스크립트 | 설명 |
| --- | --- |
| `scripts/probe_search.py <공급자>` | 장소 검색 공급자(`maptiler`, `geoapify`, `photon`, `nominatim`)별 검색 품질 비교용. 서비스 코드와 무관 |

## 컨테이너와 배포

- 이미지는 `python:3.12-slim` 기반이며 비root 사용자(uid 10001)로 실행합니다. 이미지에는 `app/`과 `db/`만 포함되고 `scripts/`는 포함되지 않습니다.
- Uvicorn은 프록시 헤더를 신뢰하도록(`--proxy-headers`) 실행합니다. 포트 8000을 외부에 직접 노출하지 말고 리버스 프록시나 터널 뒤에 두어야 합니다.
- `docker-compose.yml`은 `db`(5432)와 `api`(8000)를 모두 `127.0.0.1`에만 바인딩합니다. Docker가 게시한 포트는 ufw 같은 방화벽 규칙을 우회할 수 있으므로 바인딩 주소를 `0.0.0.0`으로 바꾸지 않습니다.
- Compose 네트워크 이름은 `travel-planner`로 고정되어 있어, 웹이나 터널 등 다른 compose 구성이 같은 네트워크에 붙을 수 있습니다.
- 두 서비스 모두 `restart: unless-stopped`입니다.
- 운영 환경에서는 `APP_ENV=prod`와 위 "환경 검증" 조건을 충족해야 하며, 프록시 계층에서 `/api/auth/login`, `/api/auth/register`에 요청 빈도 제한을 적용합니다.

업데이트 배포 순서:

```bash
git pull
docker compose build api
docker compose run --rm api python -m app.migrate   # 미적용 마이그레이션만 적용
docker compose up -d api
```

## 프로젝트 구조

```
app/
  main.py          애플리케이션 진입점, 라우터 등록, 오류 응답 형식, 상태 확인
  config.py        환경 변수 설정과 환경 검증
  db.py            DB 커넥션 풀
  deps.py          인증, 여행 소유자 검사 의존성
  security.py      비밀번호·토큰 해시
  sql.py           PATCH용 UPDATE 쿼리 생성
  schemas.py       요청·응답 모델, 입력 검증
  migrate.py       Python 마이그레이션 실행기
  providers/       장소 검색 공급자 어댑터 (geoapify, nominatim)
  routers/         auth, places, trips, stops, flights, lodgings, areas
db/migrations/     SQL 마이그레이션
scripts/           probe_search.py
Dockerfile
docker-compose.yml  db + api (개발용)
.env.example
```

## Windows 개발 환경 참고

파일이 CRLF로 저장되면 컨테이너나 Linux에서 문제가 생길 수 있어, `.gitattributes`가 `*.sql`, `*.py`, `*.yml`, `Dockerfile`을 LF로 고정합니다. 이미 CRLF로 커밋된 파일은 `git add --renormalize .`로 정리합니다.

## 보안 참고

- `.env`, `data/`, `backups/`는 `.gitignore`에 포함되어 있으며 커밋하지 않습니다.
- 가입 시 이메일 인증, 비밀번호 재설정 기능은 아직 없습니다.
- 로그인 시도 횟수 제한은 애플리케이션에 없으므로 프록시 계층에서 적용합니다.
- 장소 검색 공급자 키는 서버에서만 사용하며 클라이언트에 노출되지 않습니다. 사용자별 일일 한도로 외부 호출량을 제한합니다.

## 데이터 출처

- 장소 검색: [Geoapify](https://www.geoapify.com/), [OpenStreetMap Nominatim](https://nominatim.org/) (© OpenStreetMap contributors)