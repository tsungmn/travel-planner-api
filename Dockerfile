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

# 클라이언트 IP 는 uvicorn 이 아니라 앱(app/clientip.py)이 판단한다.
# uvicorn 에 "*" 를 주면 클라이언트가 쓴 X-Forwarded-For 의 왼쪽 값을 그대로 믿게 되어
# 요청마다 IP 를 바꿔서 속도 제한을 피할 수 있다. 그래서 uvicorn 의 프록시 헤더 처리는 끈다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]