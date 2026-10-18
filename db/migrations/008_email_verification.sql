-- 이메일 인증 + 비밀번호 재설정

-- null 이면 아직 인증하지 않은 계정. 로그인(이메일/비밀번호)이 막힌다.
alter table users add column email_verified_at timestamptz;

-- 이 기능이 생기기 전에 가입한 계정은 모두 인증 완료로 처리한다.
-- (Google 로 만든 계정은 Google 이 이미 이메일을 확인했으므로 앞으로도 가입 시점에 채워진다.)
update users set email_verified_at = now();

-- 메일로 보내는 일회용 링크 토큰. 세션 토큰처럼 해시만 저장한다.
-- (DB 가 유출돼도 메일로 나간 링크를 재현할 수 없다)
-- 한 사용자에 목적(purpose)마다 토큰은 하나만 유효하다. 새로 발급하면 이전 것은 지운다.
create table email_tokens (
  token_hash text primary key,
  user_id uuid not null references users(id) on delete cascade,
  purpose text not null check (purpose in ('verify_email', 'reset_password')),
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index on email_tokens (user_id, purpose);
create index on email_tokens (expires_at);
