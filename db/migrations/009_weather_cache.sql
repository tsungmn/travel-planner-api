-- 날씨 예보 캐시. 장소 격자(0.1° ≈ 11km)마다 한 줄이라, 같은 도시를 보는 여행은 예보를 나눠 쓴다.
-- 좌표는 소수점 비교를 피하려고 10배한 정수로 저장한다. (50.07° → 501)
-- days 는 일별 예보 배열: [{"date": "2026-11-01", "code": 3, "tmin": 4.1, "tmax": 9.8, "precip": 40}, ...]
-- date 는 그 위치의 현지 날짜, precip(강수확률 %)은 없으면 null.
create table weather_cache (
  lat10 integer not null check (lat10 between -900 and 900),
  lng10 integer not null check (lng10 between -1800 and 1800),
  fetched_at timestamptz not null default now(),
  days jsonb not null,
  primary key (lat10, lng10)
);
create index on weather_cache (fetched_at);   -- 오래된 줄 정리용
