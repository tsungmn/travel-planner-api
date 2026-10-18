"""메일 조립/발송 단위 테스트와 운영 설정 검사. DB 는 쓰지 않지만 conftest 가 설정을 채워 준다.

app 은 함수 안에서 불러온다. (TEST_DATABASE_URL 이 없을 때는 설정이 비어 있어 import 만으로 실패하기 때문)
"""
import asyncio
from email import message_from_bytes
from email.policy import default

import pytest


def _smtp(monkeypatch, **over):
    from app import mailer

    values = dict(
        smtp_host="127.0.0.1", smtp_port=1, smtp_security="ssl",
        smtp_user="no-reply@example.com", smtp_password="pw", smtp_from="no-reply@example.com",
        smtp_from_name="여행길",
    )
    for key, value in (values | over).items():
        monkeypatch.setattr(mailer.settings, key, value)
    return mailer


def test_message_has_korean_sender_and_both_text_and_html(monkeypatch):
    mailer = _smtp(monkeypatch)
    subject, text, body = mailer.verification_email("https://x.test/verify-email?token=abc", 24)
    msg = message_from_bytes(mailer._build("a@example.com", subject, text, body).as_bytes(), policy=default)
    assert msg["From"].addresses[0].display_name == "여행길"
    assert msg["From"].addresses[0].addr_spec == "no-reply@example.com"
    assert msg["To"].addresses[0].addr_spec == "a@example.com"
    assert msg["Subject"] == subject and msg["Message-ID"] and msg["Date"]
    plain = msg.get_body(("plain",)).get_content()
    page = msg.get_body(("html",)).get_content()
    assert "https://x.test/verify-email?token=abc" in plain and "https://x.test/verify-email?token=abc" in page


def test_html_escapes_the_link(monkeypatch):
    mailer = _smtp(monkeypatch)
    _, _, body = mailer.password_reset_email('https://x.test/r?token=a"><script>', 60)
    assert "<script>" not in body and "&lt;script&gt;" in body


def test_send_failure_returns_false_instead_of_raising(monkeypatch):
    mailer = _smtp(monkeypatch)       # 127.0.0.1:1 은 아무도 듣지 않는다
    assert asyncio.run(mailer.send_email("a@example.com", "s", "t", "<p>t</p>")) is False


def test_dev_without_smtp_logs_instead_of_sending(monkeypatch, caplog):
    mailer = _smtp(monkeypatch, smtp_host="")
    with caplog.at_level("WARNING", logger="mailer"):
        ok = asyncio.run(mailer.send_email("a@example.com", "제목", "본문 링크 http://x", "<p>x</p>"))
    assert ok is True and "본문 링크 http://x" in caplog.text


PROD = dict(
    app_env="prod", database_url="postgres://u:p@db.example.com:5432/x", session_secret="x" * 40,
    public_url="https://example.com", cookie_secure=True, docs_enabled=False,
    google_client_id="a", google_client_secret="b", geoapify_api_key="k",
)


def test_prod_refuses_to_start_without_smtp():
    from app.config import Settings

    with pytest.raises(ValueError, match="SMTP"):
        Settings(**PROD, _env_file=None)
    Settings(
        **PROD, smtp_host="smtp.zoho.com", smtp_user="u", smtp_password="p",
        smtp_from="no-reply@example.com", _env_file=None,
    )
