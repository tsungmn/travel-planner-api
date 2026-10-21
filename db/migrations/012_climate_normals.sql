-- 평년(과거 10년 같은 시기 평균) 캐시. 예보 창(오늘부터 16일) 밖의 날에 쓴다.
-- 격자는 weather_cache 와 같은 0.1° 격자다. 한 격자의 10년치 일별 기록을 한 번 받아서 1년 365(366)일치
-- 평년을 한꺼번에 계산해 저장하므로, 같은 격자는 해가 바뀔 때까지 외부 요청이 없다.
-- through_year: 계산에 쓴 마지막 해. 새해가 되면 한 해 밀려서 다시 계산한다.
-- days 는 "MM-DD" 를 키로 하는 객체다. 각 날짜 앞뒤 3일(7일 창) × 10년의 값으로 계산한다:
--   {"11-01": {"tmin": 3.2, "tmax": 10.1, "cold": -1.0, "hot": 13.5, "rainy": 0.3}, ...}
--   tmin/tmax: 일 최저/최고기온의 평균(℃). cold: 일 최저기온의 하위 10%, hot: 일 최고기온의 상위 10%.
--   rainy: 비 온 날(비 1mm 이상, 눈은 뺀다)의 비율 0~1. 예보의 "비 오는 날"과 같은 기준이다.
create table climate_normals (
  lat10 integer not null check (lat10 between -900 and 900),
  lng10 integer not null check (lng10 between -1800 and 1800),
  through_year integer not null,
  computed_at timestamptz not null default now(),
  days jsonb not null,
  primary key (lat10, lng10)
);
