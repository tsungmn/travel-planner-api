-- 공유 링크 공개 범위. 장소(stops)와 영역(areas)은 항상 공유하고, 아래 항목은 여행마다 선택한다.
alter table trips
  add column share_flights  boolean not null default false,
  add column share_lodgings boolean not null default false,
  add column share_memos    boolean not null default false;

-- 이미 공유 중인 링크는 지금까지처럼 전부 공개로 유지한다. (바뀌는 건 새로 켜는 링크부터)
update trips
   set share_flights = true, share_lodgings = true, share_memos = true
 where share_token is not null;