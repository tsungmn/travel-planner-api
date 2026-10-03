-- 장소 검색 사용량: 'search' 종류 추가 (기존 값은 호환을 위해 유지)
alter table place_usage drop constraint place_usage_kind_check;
alter table place_usage add constraint place_usage_kind_check
  check (kind in ('search', 'autocomplete', 'details'));

-- stops: 검색 공급자 정보
alter table stops rename column place_id to provider_place_id;
alter table stops add column provider text not null default 'manual'
  check (char_length(provider) between 1 and 20);
update stops set provider = 'google' where provider_place_id is not null;   -- 기존 개발 데이터 보존용

-- lodgings: source -> provider
alter table lodgings drop constraint lodgings_source_check;
alter table lodgings rename column source to provider;
alter table lodgings rename column place_id to provider_place_id;
alter table lodgings add constraint lodgings_provider_check
  check (char_length(provider) between 1 and 20);