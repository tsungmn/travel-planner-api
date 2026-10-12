-- stops: 사용자가 정한 표시 이름(별칭). 없으면 name 을 그대로 보여준다.
alter table stops add column alias text
  check (alias is null or char_length(alias) between 1 and 50);
