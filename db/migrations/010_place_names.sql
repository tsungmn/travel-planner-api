-- 날씨 격자(weather_cache 와 같은 0.1° 격자)의 도시 이름. 예보와 달리 이름은 잘 바뀌지 않아서
-- 만료 없이 한 번만 조회해 저장한다. 한국어 이름을 받고, 없으면 현지어가 들어온다.
create table place_names (
  lat10 integer not null check (lat10 between -900 and 900),
  lng10 integer not null check (lng10 between -1800 and 1800),
  name text not null check (char_length(name) between 1 and 100),
  created_at timestamptz not null default now(),
  primary key (lat10, lng10)
);
