FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/tsungmn/travel-planner-api"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY db ./db

RUN useradd --system --uid 10001 appuser
USER appuser

EXPOSE 8000

# --forwarded-allow-ips "*" : 앞단(Cloudflare Tunnel)의 프록시 헤더를 그대로 신뢰한다.
# 따라서 이 포트(8000)는 외부에 직접 노출하지 말고 클러스터 내부 Service 로만 연결할 것.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]