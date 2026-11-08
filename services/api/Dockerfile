FROM python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322 AS runtime
WORKDIR /opt/forge
COPY requirements.lock.txt .
RUN apk update && apk upgrade --no-cache \
 && apk add --no-cache ffmpeg=6.1.2-r2 font-dejavu=2.37-r6 \
 && pip install --no-cache-dir --require-hashes -r requirements.lock.txt \
 && adduser -D -u 10001 forge \
 && mkdir -p /artifacts/projects && chown -R forge:forge /artifacts \
 && rm -rf /var/cache/apk/*
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY scripts/ ./scripts/
USER forge
EXPOSE 8000
CMD ["uvicorn","app.api:app","--host","0.0.0.0","--port","8000","--proxy-headers","--forwarded-allow-ips",""]

FROM runtime AS test
USER root
COPY requirements-dev.lock.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.lock.txt
COPY tests/ ./tests/
COPY scripts/ ./scripts/
COPY services/ ./services/
COPY .github/ ./.github/
COPY docs/ ./docs/
COPY compose*.yaml .env*.example requirements*.txt ./
USER forge
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

FROM runtime AS production
