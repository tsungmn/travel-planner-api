"""메일 발송과 메일 문구.

SMTP 로 보낸다. (Zoho, Gmail 등 SMTP 를 지원하는 서버면 같은 방식으로 동작한다)
dev 에서 SMTP_HOST 가 비어 있으면 실제로 보내지 않고, 메일 내용(인증 링크 포함)을 로그에 남긴다.
"""
import html
import logging
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from fastapi.concurrency import run_in_threadpool

from .config import settings

log = logging.getLogger("mailer")

SMTP_TIMEOUT_S = 15


def _build(to: str, subject: str, text: str, html_body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = Address(settings.smtp_from_name, addr_spec=settings.smtp_from)
    msg["To"] = Address(addr_spec=to)
    msg["Date"] = formatdate()
    msg["Message-ID"] = make_msgid(domain=settings.smtp_from.rpartition("@")[2] or None)
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    return msg


def _send_sync(msg: EmailMessage) -> None:
    ctx = ssl.create_default_context()
    if settings.smtp_security == "ssl":
        with smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_S, context=ctx
        ) as smtp:
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_S) as smtp:
            smtp.starttls(context=ctx)
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)


async def send_email(to: str, subject: str, text: str, html_body: str) -> bool:
    """메일을 보낸다. 실패해도 예외를 던지지 않고 False 를 돌려준다.

    호출하는 쪽(가입, 재전송, 비밀번호 찾기)의 응답은 메일 성공 여부와 상관없이 같아야 하므로
    백그라운드에서 부르고, 실패는 로그로만 남긴다. 링크는 비밀값이라 운영 로그에는 내용을 남기지 않는다.
    """
    if not settings.smtp_host:
        if settings.app_env == "dev":
            log.warning("SMTP 미설정(dev): 메일을 보내지 않고 내용을 남깁니다.\nTo: %s\nSubject: %s\n\n%s", to, subject, text)
            return True
        log.error("SMTP 가 설정되지 않아 메일을 보낼 수 없습니다")
        return False
    try:
        msg = _build(to, subject, text, html_body)
        await run_in_threadpool(_send_sync, msg)
        return True
    except Exception:
        log.exception("메일 발송 실패 (받는 도메인: %s)", to.rpartition("@")[2])
        return False


# ---------------------------------------------------------------- 문구

def _render(heading: str, intro: str, button: str, link: str, outro: str) -> tuple[str, str]:
    """같은 내용을 (텍스트, HTML) 두 가지로 만든다. HTML 을 못 보는 메일 앱은 텍스트를 보여 준다."""
    text = f"{heading}\n\n{intro}\n\n{link}\n\n{outro}\n"
    e = html.escape
    body = f"""\
<div style="font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1f2937">
  <h1 style="font-size:20px;margin:0 0 16px">{e(heading)}</h1>
  <p style="line-height:1.6;margin:0 0 24px">{e(intro)}</p>
  <p style="margin:0 0 24px"><a href="{e(link)}" style="display:inline-block;padding:12px 24px;background:#2563eb;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600">{e(button)}</a></p>
  <p style="font-size:13px;line-height:1.6;color:#6b7280;margin:0 0 8px">버튼이 눌러지지 않으면 아래 주소를 복사해서 주소창에 붙여 넣어 주세요.<br><span style="word-break:break-all">{e(link)}</span></p>
  <p style="font-size:13px;line-height:1.6;color:#6b7280;margin:16px 0 0">{e(outro)}</p>
</div>"""
    return text, body


def verification_email(link: str, ttl_hours: int) -> tuple[str, str, str]:
    """(제목, 텍스트, HTML)"""
    text, body = _render(
        heading="이메일 인증을 완료해 주세요",
        intro=f"{settings.smtp_from_name}에 가입해 주셔서 감사해요. 아래 링크를 누르면 가입이 끝나요. 링크는 {ttl_hours}시간 동안만 쓸 수 있어요.",
        button="이메일 인증하기",
        link=link,
        outro="직접 가입하지 않았다면 이 메일은 무시해 주세요.",
    )
    return f"[{settings.smtp_from_name}] 이메일 인증을 완료해 주세요", text, body


def account_exists_email(login_link: str, reset_link: str) -> tuple[str, str, str]:
    """이미 가입된 이메일로 다시 가입을 시도했을 때. 토큰이 든 링크는 넣지 않는다.
    (가입 화면의 응답을 계정 유무와 상관없이 같게 하려고, 이미 있는 계정에는 이 안내만 보낸다)"""
    text = (
        "이미 가입된 이메일이에요\n\n"
        f"{settings.smtp_from_name}에 이 이메일로 가입된 계정이 있어요. 아래 주소에서 로그인해 주세요.\n\n"
        f"{login_link}\n\n"
        f"비밀번호가 기억나지 않으면 여기에서 다시 정할 수 있어요.\n\n{reset_link}\n\n"
        "직접 가입하려던 게 아니라면 이 메일은 무시해 주세요. 계정은 바뀌지 않아요.\n"
    )
    e = html.escape
    body = f"""\
<div style="font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1f2937">
  <h1 style="font-size:20px;margin:0 0 16px">이미 가입된 이메일이에요</h1>
  <p style="line-height:1.6;margin:0 0 24px">{e(settings.smtp_from_name)}에 이 이메일로 가입된 계정이 있어요. 아래 버튼에서 로그인해 주세요.</p>
  <p style="margin:0 0 24px"><a href="{e(login_link)}" style="display:inline-block;padding:12px 24px;background:#2563eb;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600">로그인하기</a></p>
  <p style="font-size:13px;line-height:1.6;color:#6b7280;margin:0 0 8px">비밀번호가 기억나지 않으면 <a href="{e(reset_link)}">여기에서 다시 정할 수 있어요.</a></p>
  <p style="font-size:13px;line-height:1.6;color:#6b7280;margin:16px 0 0">직접 가입하려던 게 아니라면 이 메일은 무시해 주세요. 계정은 바뀌지 않아요.</p>
</div>"""
    return f"[{settings.smtp_from_name}] 이미 가입된 이메일이에요", text, body


def password_reset_email(link: str, ttl_minutes: int) -> tuple[str, str, str]:
    """(제목, 텍스트, HTML)"""
    text, body = _render(
        heading="비밀번호를 다시 설정해 주세요",
        intro=f"비밀번호 재설정을 요청하셨어요. 아래 링크에서 새 비밀번호를 정해 주세요. 링크는 {ttl_minutes}분 동안만 쓸 수 있고, 한 번만 쓸 수 있어요.",
        button="비밀번호 재설정하기",
        link=link,
        outro="요청하지 않았다면 이 메일은 무시해 주세요. 비밀번호는 바뀌지 않아요.",
    )
    return f"[{settings.smtp_from_name}] 비밀번호 재설정 안내", text, body
