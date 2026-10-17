-- 계정 삭제 사유 (익명). 개인 식별 정보(user_id, email)는 저장하지 않는다.
create table account_deletions (
  id bigint generated always as identity primary key,
  reason text not null
    check (reason in ('not_useful','privacy_concern','too_difficult','found_alternative','other')),
  detail text check (char_length(detail) <= 1000),
  deleted_at timestamptz not null default now()
);
