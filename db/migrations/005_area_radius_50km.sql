-- 영역 반경 상한을 5km에서 50km로 올린다. (웹의 AREA_MAX_RADIUS_M과 같아야 한다)
alter table trip_areas drop constraint if exists trip_areas_radius_m_check;
alter table trip_areas add constraint trip_areas_radius_m_check
  check (radius_m between 100 and 50000);