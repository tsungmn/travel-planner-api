# travel-planner-api

여행 계획 서비스 **'여행길'** 의 백엔드(API) 서버입니다.
로그인, 여행 데이터 저장, 장소 검색을 맡고, 데이터는 홈서버의 PostgreSQL에 보관합니다.

## 1. 이 서버가 하는 일

- 이메일/비밀번호 로그인과 Google 로그인
- 여행, 날짜별 방문 장소, 항공편, 숙소 저장
- 장소 검색 (입력 중 추천은 Geoapify, 전체 검색은 OpenStreetMap Nominatim)
- 로그인 없이 볼 수 있는 읽기 전용 공유 링크

모든 주소는 `/api`로 시작합니다. 자세한 주소 목록과 요청·응답 모양은 서버를 켠 뒤 `/api/docs`(Swagger 문서)에서 볼 수 있습니다.

## 2. 전체 구조

```
브라우저 / 앱
   ↓
travel-planner-web   (화면)
   ↓  /api/... 요청
travel-planner-api   (이 저장소)   ← 쿠버네티스 클러스터에서 실행 (ArgoCD가 배포)
   ↓
PostgreSQL           ← 홈서버에서 Docker로 실행 (이 저장소의 docker-compose.db.yml)
```

| 저장소 | 역할 |
| --- | --- |
| `travel-planner-api` | API 서버 (이 저장소) |
| `travel-planner-web` | 웹 화면 (Next.js) |
| `travel-planner-config` | 배포 설정 (ArgoCD가 읽는 저장소) |

| 구분 | 사용 기술 |
| --- | --- |
| 서버 | Python 3.12, FastAPI, Uvicorn |
| 데이터베이스 | PostgreSQL 17 (asyncpg, 순수 SQL 마이그레이션) |
| 인증 | 서버 세션, Argon2 비밀번호 해시, Google OAuth |
| 실행 | Docker |

---

## 3. 내 컴퓨터에서 실행하기 (개발용)

준비물은 Docker(Compose v2)와 Python 3.12입니다.

```bash
# 1. 설정 파일 만들기
cp .env.example .env
```

`.env`를 열어 아래처럼 고칩니다. (값의 뜻은 4장에 있습니다.)

| 항목 | 개발용 값 |
| --- | --- |
| `POSTGRES_PASSWORD` | `openssl rand -hex 24`로 만든 값 |
| `DATABASE_URL` | `<POSTGRES_PASSWORD>` 자리에 위 비밀번호를 넣습니다 |
| `DB_BIND_IP` | `127.0.0.1` (비워 두면 DB가 켜지지 않습니다) |
| `SESSION_SECRET` | `openssl rand -base64 32`로 만든 값 |
| `PUBLIC_URL` | `http://localhost:3000` |
| `COOKIE_SECURE` | `false` (개발은 http라서) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | 비워도 됩니다 (Google 로그인만 안 됩니다) |

```bash
# 2. DB 켜기
docker compose -f docker-compose.db.yml up -d

# 3. 파이썬 환경 만들기
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\activate
pip install -r requirements.txt

# 4. 테이블 만들기 (마이그레이션)
python -m app.migrate

# 5. 서버 켜기
uvicorn app.main:app --reload --port 8000
```

`http://127.0.0.1:8000/api/readyz`에서 `{"ok":true}`가 나오면 성공입니다. API 문서는 `http://127.0.0.1:8000/api/docs`입니다.

쿠키는 주소별로 따로 저장되므로 `localhost`와 `127.0.0.1`을 섞어 쓰지 않습니다.

---

## 4. 환경 변수

서버는 시작할 때 설정을 검사하고, 잘못되어 있으면 **일부러 켜지지 않습니다.** 로그에 무엇이 문제인지 적어 줍니다.

| 이름 | 설명 |
| --- | --- |
| `APP_ENV` | `dev`(개발) 또는 `prod`(운영). 기본값은 `dev` |
| `DATABASE_URL` | DB 접속 주소. `postgres://<유저>:<비밀번호>@<주소>:5432/<DB이름>` |
| `SESSION_SECRET` | 로그인 관련 쿠키를 서명하는 비밀 키 (32자 이상) |
| `PUBLIC_URL` | 사용자가 접속하는 웹 주소. Google 로그인 후 돌아올 주소에 쓰입니다 |
| `COOKIE_SECURE` | https에서만 쿠키 사용. 운영은 `true`, 개발은 `false` |
| `DOCS_ENABLED` | `/api/docs` 공개 여부. 운영은 `false` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google 로그인용 키 (변수 자체는 있어야 하고, 개발에서는 값이 비어도 됩니다) |
| `GEOAPIFY_API_KEY` | 입력 중 장소 추천용 키. 비어 있으면 추천 기능만 503 |
| `NOMINATIM_USER_AGENT` | 전체 검색 때 보내는 이름. 연락처를 넣습니다 |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | DB 컨테이너를 만들 때 쓰는 값 |
| `DB_BIND_IP` | DB가 문을 열 IP (5장 참고) |

**개발/운영 섞임 방지:** `APP_ENV=dev`이면 DB 주소가 내 컴퓨터(`localhost`, `127.0.0.1` 등)여야만 켜집니다. 그래서 개발 중에 실수로 운영 DB에 접속하는 일이 없습니다. `APP_ENV=prod`이면 반대로 로컬 DB를 쓸 수 없고, `https` 주소, `COOKIE_SECURE=true`, `DOCS_ENABLED=false`, 32자 이상의 `SESSION_SECRET`, Google과 Geoapify 키가 모두 있어야 합니다.

운영 환경의 값은 이 저장소가 아니라 클러스터(ConfigMap과 Vault)에서 넣습니다.

---

## 5. 데이터베이스 관리 (홈서버)

### 5-1. DB는 어떻게 돌아가나요?

- PostgreSQL이 **홈서버의 Docker 컨테이너**로 실행됩니다. 설정은 `docker-compose.db.yml`에 있습니다.
- 데이터는 홈서버의 **`data/pg` 폴더**에 저장됩니다. 컨테이너를 껐다 켜거나 지워도 이 폴더가 남아 있으면 데이터는 안전합니다.
- 클러스터에서 실행되는 api가 `DATABASE_URL`로 홈서버의 5432번 포트(DB의 문 번호)에 접속합니다.
- 쿠버네티스나 ArgoCD가 관리하지 않습니다. **DB는 홈서버에서 직접 관리**합니다.

### 5-2. 처음 켜기

홈서버에서 한 번만 합니다. DB에는 아래 4개 값만 있으면 됩니다. (api는 클러스터에서 돌기 때문에 나머지 설정은 필요 없습니다.)

```bash
# Docker가 재부팅 후에도 자동으로 켜지게 하기
sudo systemctl enable --now docker

# 코드 받기
git clone https://github.com/tsungmn/travel-planner-api.git
cd travel-planner-api

# .env 만들기 (값을 채웁니다)
cat > .env <<'EOF'
POSTGRES_USER=app
POSTGRES_PASSWORD=<openssl rand -hex 24 로 만든 값>
POSTGRES_DB=travel
DB_BIND_IP=<홈서버 IP>
EOF
chmod 600 .env
nano .env

# DB 켜기
docker compose -f docker-compose.db.yml up -d
docker compose -f docker-compose.db.yml ps      # STATUS 가 healthy 가 될 때까지 기다립니다
```

- 비밀번호는 `openssl rand -hex 24`처럼 **영문과 숫자로만** 만듭니다. `@`, `/`, `:` 같은 문자가 있으면 `DATABASE_URL`이 깨집니다.
- `DB_BIND_IP`는 "이 IP로 들어오는 접속만 받겠다"는 뜻입니다. 홈서버가 실제로 가진 IP여야 하고, `0.0.0.0`(모든 곳)은 쓰지 않습니다.
- `POSTGRES_PASSWORD`는 `data/pg`가 **처음 만들어질 때만** 적용됩니다. 나중에 `.env`만 고쳐도 DB의 비밀번호는 바뀌지 않습니다. 바꾸는 방법은 5-8에 있습니다.

켠 뒤에는 클러스터에서 DB 문이 열려 있는지 확인하고, 테이블을 만듭니다.

```bash
# 클러스터에서: 문이 열려 있는지
kubectl run dbtest --image=postgres:17 --restart=Never --command -- \
  sh -c 'pg_isready -h <홈서버IP> -p 5432; echo "exit code: $?"'
kubectl logs dbtest            # accepting connections 가 나오면 성공
kubectl delete pod dbtest

# 클러스터에서: 테이블 만들기 (마이그레이션, 5-5 참고)
kubectl -n travel-planner-prod exec deploy/api-deployment -- python -m app.migrate
```

### 5-3. 아무나 들어오지 못하게 막기

DB의 문(5432번)은 **클러스터 서버만** 지나갈 수 있어야 합니다. 비밀번호를 무작위로 찍어 보는 프로그램은 열린 포트를 계속 찾아다닙니다.

**주의:** Docker가 연 포트는 `ufw` 같은 일반 방화벽 규칙을 **우회할 수 있습니다.** `ufw allow ...`만 해 두면 막은 줄 알았는데 열려 있는 경우가 있습니다. Docker 전용 규칙 칸인 `DOCKER-USER`를 사용합니다.

```bash
ip route | grep default       # "dev" 뒤에 나오는 이름이 외부와 연결된 NIC 이름입니다

# 1) 5432로 들어오는 접속은 일단 모두 막고
sudo iptables -I DOCKER-USER -i <NIC이름> -p tcp --dport 5432 -j DROP
# 2) 클러스터 서버의 IP만 통과시킵니다 (서버가 여러 대면 IP마다 한 줄씩 실행)
sudo iptables -I DOCKER-USER -i <NIC이름> -p tcp --dport 5432 -s <클러스터IP> -j ACCEPT

sudo iptables -L DOCKER-USER -n --line-numbers     # ACCEPT 가 DROP 보다 위에 있어야 합니다
```

- 재부팅하면 규칙이 사라지므로 저장합니다: `sudo apt install iptables-persistent` 후 `sudo netfilter-persistent save`
- 적용한 직후 **양쪽을 모두 시험**합니다. 클러스터에서 `pg_isready`가 성공해야 하고, 허용하지 않은 다른 컴퓨터에서는 응답 없이 시간이 초과되어야 합니다.
- 홈서버 앞단(공유기, 학교망 등)에서 이미 5432를 막고 있다면 더 안전합니다. 이 규칙은 그 위에 한 겹 더 두는 것입니다.
- 사용하는 방화벽 설정에 따라 명령이 다를 수 있습니다. 위 시험으로 반드시 확인합니다.

### 5-4. 자주 쓰는 명령

모두 저장소 폴더(`docker-compose.db.yml`이 있는 곳)에서 실행합니다. 아래 `app`, `travel`은 `.env`의 `POSTGRES_USER`, `POSTGRES_DB` 값입니다.

| 하고 싶은 일 | 명령 |
| --- | --- |
| 상태 보기 | `docker compose -f docker-compose.db.yml ps` |
| 로그 보기 | `docker compose -f docker-compose.db.yml logs --tail=50 db` |
| 다시 시작 | `docker compose -f docker-compose.db.yml restart db` |
| 잠깐 끄기 / 켜기 | `docker compose -f docker-compose.db.yml stop` / `start` |
| DB 안으로 들어가기 | `docker compose -f docker-compose.db.yml exec db psql -U app -d travel` |
| 디스크 사용량 | `du -sh data/pg` 와 `df -h .` |

`psql`(DB 안)에서 쓰는 명령입니다. `\q`로 나옵니다.

```
\dt                                            -- 테이블 목록
select count(*) from users;                    -- 가입자 수
select count(*) from trips;                    -- 여행 수
select pg_size_pretty(pg_database_size('travel'));   -- DB 크기
select version from schema_migrations order by version;   -- 적용된 마이그레이션
```

**절대 하면 안 되는 것:** `data/pg` 폴더를 지우거나 옮기는 일입니다. 모든 데이터가 사라집니다. `docker compose down`은 컨테이너만 지우고 `data/pg`는 남기지만, 습관적으로 `down` 대신 `stop`을 쓰는 편이 안전합니다.

PostgreSQL 이미지 버전(`postgres:17`)은 함부로 바꾸지 않습니다. 큰 버전(17 → 18)을 바꾸려면 백업한 뒤 새 버전에서 복원하는 절차가 필요합니다.

### 5-5. 테이블 모양 바꾸기 (마이그레이션)

**마이그레이션**은 DB의 설계도(테이블 모양)를 바꾼 기록을 파일로 남겨 두고, 번호 순서대로 적용하는 방법입니다. 파일은 `db/migrations/`에 있습니다.

| 파일 | 내용 |
| --- | --- |
| `001_init.sql` | 처음 스키마: 사용자, 세션, 여행, 항공편, 숙소, 방문 장소 등 |
| `002_search_provider.sql` | 장소 검색 공급자 컬럼 |
| `003_stop_alias.sql` | 방문 장소의 별칭 |
| `004_trip_areas.sql` | 대략적인 계획 영역 |
| `005_area_radius_50km.sql` | 영역 반경 상한을 50km로 변경 |
| `006_account_deletions.sql` | 계정 삭제 사유 (익명) |
| `007_share_options.sql` | 공유 링크의 공개 범위 |

규칙은 세 가지입니다.

1. **이미 만든 파일은 고치지 않습니다.** 바꾸고 싶으면 `008_...sql`처럼 **새 파일**을 만듭니다.
2. 파일 이름의 번호 순서대로 적용됩니다. 한 번 적용한 파일은 `schema_migrations` 표에 기록되어 다시 실행되지 않습니다.
3. 각 파일은 통째로 적용되거나, 실패하면 통째로 취소됩니다. 중간까지만 적용되는 일이 없습니다.

적용은 이렇게 합니다. 새 이미지가 배포된 **뒤에** 클러스터에서 실행합니다. (이미지에 `db/` 폴더가 들어 있습니다.)

```bash
kubectl -n travel-planner-prod exec deploy/api-deployment -- python -m app.migrate
```

`skip 001_init.sql`처럼 이미 한 것은 건너뛰고, `applying 008_...sql`처럼 새것만 적용한다는 글이 나옵니다.

### 5-6. 백업 (세이브 파일 만들기)

백업은 게임의 세이브 파일과 같습니다. DB가 망가지거나 실수로 데이터를 지웠을 때 되돌릴 수 있는 유일한 방법입니다.

`scripts/backup.sh`가 `pg_dump`로 DB 전체를 압축 파일로 저장합니다. 홈서버에서 실행합니다.

```bash
./scripts/backup.sh
ls -lh backups/        # travel-20261018-030000.sql.gz 같은 파일이 생깁니다
```

- 저장 위치는 `backups/`이고, 파일은 본인만 읽을 수 있게 만들어집니다.
- 14일이 지난 파일은 자동으로 지웁니다. 기간은 `KEEP_DAYS=30 ./scripts/backup.sh`처럼 바꿀 수 있습니다.
- 덤프가 실패하거나 파일이 비어 있으면 파일을 남기지 않고 오류로 끝냅니다.

**매일 자동으로 하기 (cron):**

```bash
crontab -e
# 아래 한 줄을 추가: 매일 새벽 3시
0 3 * * * /home/<유저>/travel-planner-api/scripts/backup.sh >> /home/<유저>/travel-planner-api/backups/backup.log 2>&1
```

다음 날 `backups/backup.log`에 `ok` 줄이 찍혔는지 확인합니다. 실행하는 사용자는 `docker` 그룹에 속해 있어야 합니다.

**다른 곳에도 복사합니다.** 홈서버의 디스크가 고장 나면 `backups/`도 함께 사라집니다. 다른 컴퓨터나 외부 저장소에 주기적으로 복사해 둡니다.

```bash
rsync -av backups/ <유저>@<다른컴퓨터>:/path/travel-backups/
```

### 5-7. 복원 (세이브 파일 불러오기)

**먼저 연습부터 합니다.** 백업 파일이 진짜 쓸 수 있는지는 복원해 봐야 압니다. 임시 DB에 넣어 보면 운영 데이터에 영향이 없습니다.

```bash
FILE=backups/<백업파일>.sql.gz

docker compose -f docker-compose.db.yml exec db createdb -U app travel_restore_test
gunzip -c "$FILE" | docker compose -f docker-compose.db.yml exec -T db psql -U app -d travel_restore_test
docker compose -f docker-compose.db.yml exec db psql -U app -d travel_restore_test -c "select count(*) from trips;"
docker compose -f docker-compose.db.yml exec db dropdb -U app travel_restore_test     # 확인 끝나면 지우기
```

**진짜로 되돌려야 할 때:** 복원하면 **현재 데이터가 사라집니다.** 순서를 지킵니다.

1. 지금 상태도 한 번 백업해 둡니다. (`./scripts/backup.sh`)
2. api를 멈춥니다. 연결이 남아 있으면 DB를 지울 수 없습니다. (클러스터에서 `kubectl -n travel-planner-prod scale deploy/api-deployment --replicas=0`. ArgoCD의 자동 동기화가 켜져 있으면 되돌릴 수 있으니, 사용자가 없는 시간에 빠르게 하거나 자동 동기화를 잠시 끕니다.)
3. DB를 지우고 다시 만든 뒤 백업을 넣습니다.

```bash
docker compose -f docker-compose.db.yml exec db dropdb -U app travel
docker compose -f docker-compose.db.yml exec db createdb -U app travel
gunzip -c backups/<백업파일>.sql.gz | docker compose -f docker-compose.db.yml exec -T db psql -U app -d travel
```

4. api를 다시 켜고(`--replicas=1`) `https://<도메인>/api/readyz`가 `{"ok":true}`인지 확인합니다.

### 5-8. DB 비밀번호 바꾸기

`.env`의 `POSTGRES_PASSWORD`만 고쳐서는 바뀌지 않습니다. DB 안에서 바꿉니다.

```bash
docker compose -f docker-compose.db.yml exec db psql -U app -d travel
```

```
\password app          -- 새 비밀번호를 두 번 입력합니다. 명령 기록에 남지 않습니다.
\q
```

그다음 두 곳을 같이 바꿔야 합니다. 하나라도 빠지면 api가 DB에 접속하지 못합니다.

1. Vault의 `DATABASE_URL`에 들어 있는 비밀번호
2. 홈서버의 `.env` (기록용)

```bash
kubectl -n travel-planner-prod rollout restart deploy/api-deployment
```

### 5-9. DB 문제가 생겼을 때

| 증상 | 원인 | 확인할 곳 |
| --- | --- | --- |
| `/api/readyz`가 503 `{"ok":false}` | api가 DB에 연결하지 못합니다 | 홈서버에서 `ps`가 healthy인지, `DATABASE_URL`, 방화벽 |
| DB 컨테이너가 계속 꺼짐 | `DB_BIND_IP`가 비었거나 홈서버의 IP가 아닙니다 | `.env`, `logs db` |
| `password authentication failed` | 비밀번호가 맞지 않습니다 | `DATABASE_URL`과 DB 안의 비밀번호 (5-8) |
| 클러스터에서 `no response` | 5432 문이 막혀 있거나 DB가 꺼져 있습니다 | `ps`, 5-3의 규칙에서 클러스터 IP 허용 여부 |
| 홈서버 재부팅 후 DB가 안 켜짐 | Docker 자동 시작이 꺼져 있습니다 | `sudo systemctl enable --now docker` |
| 백업 파일이 안 생김 | cron 환경이나 권한 문제입니다 | `backups/backup.log`, `docker` 그룹 |
| 디스크가 부족함 | 데이터나 백업이 쌓였습니다 | `du -sh data/pg backups`, `KEEP_DAYS` 줄이기 |

---

## 6. 배포 (자동)

`main` 브랜치에 push하면 GitHub Actions가 다음을 자동으로 합니다.

1. Docker 이미지를 만들어 Docker Hub(`tsungmn/travel-planner-api`)에 올립니다. 태그는 커밋 번호입니다.
2. `travel-planner-config` 저장소의 api 이미지 태그를 새 값으로 바꿉니다.
3. ArgoCD가 바뀐 설정을 보고 클러스터에 새 버전을 반영합니다.

필요한 GitHub Secrets는 `DOCKER_PASSWORD`(Docker Hub 로그인)와 `CONFIG_REPO_TOKEN`(config 저장소에 쓰기 위한 토큰)입니다.

클러스터 설정, 도메인, 인증서는 [travel-planner-config](https://github.com/tsungmn/travel-planner-config) 저장소에 있습니다.

**새 버전에 DB 변경(새 마이그레이션)이 들어 있을 때의 순서**

```
1. 홈서버에서 ./scripts/backup.sh          ← 먼저 백업
2. main에 push → 새 버전 배포 완료까지 기다림
3. 클러스터에서 python -m app.migrate       ← 5-5
4. https://<도메인>/api/readyz 확인
```

---

## 7. 테스트

통합 테스트는 실제 PostgreSQL이 필요합니다. `TEST_DATABASE_URL`이 없으면 건너뜁니다.

테스트가 시작될 때 `public` 스키마를 **통째로 지우기 때문에**, DB 이름에 `test`가 들어 있지 않으면 실행되지 않습니다. 개발·운영 DB를 지우는 사고를 막기 위한 장치입니다.

```bash
docker compose -f docker-compose.db.yml exec db createdb -U app travel_test
pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgres://app:<비밀번호>@127.0.0.1:5432/travel_test pytest
```

## 8. 폴더 구조

```
app/
  main.py        서버 시작점, 상태 확인 (/api/healthz, /api/readyz)
  config.py      환경 변수와 설정 검사
  db.py          DB 연결 관리
  migrate.py     마이그레이션 실행기
  routers/       기능별 주소 (auth, trips, stops, flights, lodgings, areas, places)
  providers/     장소 검색 공급자 (Geoapify, Nominatim)
db/migrations/   테이블 모양을 바꾼 기록 (번호 순서대로 적용)
scripts/         backup.sh (홈서버에서만 사용, 이미지에는 들어가지 않습니다)
tests/           통합 테스트
docker-compose.db.yml   홈서버 DB 실행 설정
Dockerfile              API 서버 이미지
```

상태 확인 주소는 둘로 나뉩니다. `/api/healthz`는 서버가 살아 있는지만, `/api/readyz`는 DB 연결까지 확인합니다.

## 9. 라이선스

Apache License 2.0 ([LICENSE](LICENSE))