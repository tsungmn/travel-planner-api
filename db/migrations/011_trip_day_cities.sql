-- 사용자가 직접 정한 일차별 날씨 도시. 정하지 않은 일차는 행이 없고, 서버가 일정에서 추정한다.
-- "끝까지", "범위 지정"은 저장할 때 일차마다 한 줄씩 펼쳐서 넣는다. (추정 규칙이 일차 단위라 단순해진다)
-- 이름과 좌표는 도시 검색 결과를 그대로 저장한다. (좌표는 날씨 격자 계산에만 쓴다)
create table trip_day_cities (
  trip_id uuid not null references trips(id) on delete cascade,
  day_index int not null check (day_index between 0 and 60),
  name text not null check (char_length(name) between 1 and 100),
  lat double precision not null check (lat between -90 and 90),
  lng double precision not null check (lng between -180 and 180),
  created_at timestamptz not null default now(),
  primary key (trip_id, day_index)
);
