"""간단한 메모리 기반 시도 횟수 제한. Uvicorn 워커 1개 기준이고, 재시작하면 초기화된다.
프록시(Cloudflare 등)의 제한이 1차 방어이고, 이건 프록시를 우회하거나 설정이 빠졌을 때의 안전망이다."""
import time
from collections import deque


class AttemptLimiter:
    def __init__(self, limit: int, window_s: int) -> None:
        self.limit = limit
        self.window = window_s
        self._hits: dict[str, deque[float]] = {}

    def _prune(self, key: str) -> deque[float]:
        q = self._hits.get(key)
        if q is None:
            return deque()
        cutoff = time.monotonic() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        if not q:
            del self._hits[key]
            return deque()
        return q

    def blocked(self, key: str) -> bool:
        return len(self._prune(key)) >= self.limit

    def add(self, key: str) -> None:
        if len(self._hits) > 10_000:                # 키 폭증(메모리) 방지: 만료된 키부터 정리
            for k in list(self._hits):
                self._prune(k)
            if len(self._hits) > 10_000:
                self._hits.clear()
        self._hits.setdefault(key, deque()).append(time.monotonic())

    def clear(self, key: str) -> None:
        self._hits.pop(key, None)