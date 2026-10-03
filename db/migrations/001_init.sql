-- ===== 인증 =====
create table users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique check (email = lower(email)),
  password_hash text,                 -- Google 전용 계정이면 null
  google_sub text unique,
  name text,
  created_at timestamptz not null default now()
);

-- 세션 토큰은 해시로만 저장 (DB가 유출돼도 토큰을 재사용할 수 없음)
create table sessions (
  token_hash text primary key,
  user_id uuid not null references users(id) on delete cascade,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index on sessions (user_id);
create index on sessions (expires_at);

-- ===== 여행 =====
create table trips (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references users(id) on delete cascade,
  title text not null check (char_length(title) between 1 and 100),
  destination text not null check (char_length(destination) between 1 and 100),
  dest_lat double precision,          -- 장소 검색 지역 편향용 (선택)
  dest_lng double precision,
  start_date date not null,
  end_date date not null,
  share_token text unique,            -- null이면 비공개
  created_at timestamptz not null default now(),
  check (end_date >= start_date and end_date - start_date <= 60)
);
create index on trips (owner_id, start_date desc);

create table flights (
  id uuid primary key default gen_random_uuid(),
  trip_id uuid not null references trips(id) on delete cascade,
  direction text not null check (direction in ('outbound','inbound')),
  flight_no text not null,
  dep_airport text, arr_airport text, -- IATA
  dep_local timestamp, arr_local timestamp,
  dep_tz text, arr_tz text
);
create index on flights (trip_id);

create table lodgings (
  id uuid primary key default gen_random_uuid(),
  trip_id uuid not null references trips(id) on delete cascade,
  name text not null,
  address_text text,
  lat double precision not null,
  lng double precision not null,
  place_id text,                      -- manual이면 null
  source text not null default 'manual' check (source in ('google','manual')),
  check_in date, check_out date,
  memo text
);
create index on lodgings (trip_id);

create table stops (
  id uuid primary key default gen_random_uuid(),
  trip_id uuid not null references trips(id) on delete cascade,
  day_index int not null check (day_index between 0 and 60),   -- 0부터
  sort_order int not null,
  name text not null,
  address_text text,
  lat double precision not null,
  lng double precision not null,
  place_id text,
  memo text check (char_length(memo) <= 1000)
);
create index on stops (trip_id, day_index, sort_order);

-- ===== 장소 검색 일일 사용량 =====
create table place_usage (
  user_id uuid not null references users(id) on delete cascade,
  day date not null default current_date,
  kind text not null check (kind in ('autocomplete','details')),
  n int not null default 0,
  primary key (user_id, day, kind)
);