-- 러프 계획: 일차별로 "이 근처를 돌아다니자"를 원(중심 + 반경)으로 표시한 영역.
-- 장소(stops)와는 FK 없이 좌표로만 연결한다. (영역 안에 있는 장소는 화면에서 계산)
create table trip_areas (
  id uuid primary key default gen_random_uuid(),
  trip_id uuid not null references trips(id) on delete cascade,
  day_index int check (day_index is null or day_index between 0 and 60),   -- null = 일차 미지정
  label text not null default '' check (char_length(label) <= 30),
  lat double precision not null check (lat between -90 and 90),
  lng double precision not null check (lng between -180 and 180),
  radius_m int not null check (radius_m between 100 and 5000),
  memo text check (memo is null or char_length(memo) <= 500),
  created_at timestamptz not null default now()
);
create index on trip_areas (trip_id, day_index);
