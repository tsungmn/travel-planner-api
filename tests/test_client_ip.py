"""속도 제한의 키가 되는 클라이언트 IP 판별. X-Forwarded-For 위조로 제한을 피할 수 없어야 한다."""
import pytest
from starlette.requests import Request

from app import clientip
from app.config import settings

pytestmark = pytest.mark.no_db


def req(peer: str | None, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({
        "type": "http", "method": "GET", "path": "/", "headers": headers,
        "client": (peer, 1234) if peer else None,
    })


def test_uses_the_address_the_trusted_proxy_appended():
    assert clientip.client_ip(req("10.42.0.7", "203.0.113.9")) == "203.0.113.9"


def test_ignores_the_left_side_that_the_client_wrote():
    # 클라이언트가 "1.1.1.1" 을 써서 보내도, 프록시가 덧붙인 오른쪽 값이 쓰인다.
    assert clientip.client_ip(req("10.42.0.7", "1.1.1.1, 203.0.113.9")) == "203.0.113.9"
    assert clientip.client_ip(req("10.42.0.7", "2.2.2.2, 3.3.3.3, 203.0.113.9")) == "203.0.113.9"


def test_header_from_an_untrusted_peer_is_ignored():
    assert clientip.client_ip(req("8.8.8.8", "1.1.1.1")) == "8.8.8.8"


def test_falls_back_to_the_peer_when_header_is_missing_or_malformed():
    assert clientip.client_ip(req("10.42.0.7")) == "10.42.0.7"
    assert clientip.client_ip(req("10.42.0.7", "")) == "10.42.0.7"
    assert clientip.client_ip(req("10.42.0.7", "not-an-ip")) == "10.42.0.7"
    assert clientip.client_ip(req(None)) == "unknown"


def test_ipv6_is_normalized():
    assert clientip.client_ip(req("10.42.0.7", "2001:DB8:0:0:0:0:0:1")) == "2001:db8::1"


def test_two_proxies_in_front(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_hops", 2)
    assert clientip.client_ip(req("10.42.0.7", "9.9.9.9, 203.0.113.9")) == "9.9.9.9"
    assert clientip.client_ip(req("10.42.0.7", "9.9.9.9, 7.7.7.7, 203.0.113.9")) == "7.7.7.7"
    assert clientip.client_ip(req("10.42.0.7", "203.0.113.9")) == "10.42.0.7"   # 단수가 모자라면 믿지 않는다


def test_hops_zero_never_reads_the_header(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_hops", 0)
    assert clientip.client_ip(req("10.42.0.7", "203.0.113.9")) == "10.42.0.7"
