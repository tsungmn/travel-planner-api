"""요청의 실제 클라이언트 IP. 속도 제한(app/routers/auth.py)이 이 값을 키로 쓴다.

X-Forwarded-For 는 클라이언트가 마음대로 써서 보낼 수 있고, 프록시는 그 뒤에 자기가 본 주소를 덧붙인다.
그래서 왼쪽 값(클라이언트가 쓴 쪽)을 믿으면 요청마다 다른 IP 를 흉내 내서 제한을 피할 수 있다.
여기서는 신뢰하는 프록시가 덧붙인 오른쪽 값만 쓴다.

  - 직접 연결한 상대(request.client)가 TRUSTED_PROXY_CIDRS 안에 있을 때만 헤더를 읽는다.
  - 오른쪽에서 TRUSTED_PROXY_HOPS 번째 값을 쓴다. (프록시 1단이면 가장 오른쪽 = 프록시가 본 클라이언트)
  - 값이 없거나 형식이 이상하면 직접 연결한 상대의 주소를 쓴다.
"""
import ipaddress
import logging

from fastapi import Request

from .config import settings

log = logging.getLogger("clientip")

_TRUSTED = tuple(
    ipaddress.ip_network(c.strip(), strict=False)
    for c in settings.trusted_proxy_cidrs.split(",")
    if c.strip()
)
_warned = False


def _is_trusted_proxy(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _TRUSTED)


def _warn_if_private(ip: str) -> None:
    """운영에서 사용자 IP 가 사설 주소로 보이면 모든 사용자가 한 IP 로 묶인다. 설정을 점검하라고 한 번만 알린다."""
    global _warned
    if _warned or settings.app_env != "prod":
        return
    try:
        private = not ipaddress.ip_address(ip).is_global
    except ValueError:
        return
    if private:
        _warned = True
        log.warning(
            "클라이언트 IP 가 사설 주소(%s)로 보입니다. 모든 사용자가 같은 IP 로 묶여 IP 별 제한이 서로 영향을 줍니다. "
            "ingress-nginx Service 의 externalTrafficPolicy 를 Local 로 하거나 TRUSTED_PROXY_HOPS 를 확인하세요.",
            ip,
        )


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else None
    if peer is None:
        return "unknown"

    hops = settings.trusted_proxy_hops
    if hops > 0 and _is_trusted_proxy(peer):
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= hops:
            try:
                ip = str(ipaddress.ip_address(parts[-hops]))
            except ValueError:
                return peer
            _warn_if_private(ip)
            return ip
    return peer
