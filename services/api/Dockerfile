FROM python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322 AS runtime
WORKDIR /opt/forge
COPY requirements.lock.txt .
RUN apk update && apk upgrade --no-cache \
 && apk add --no-cache ffmpeg=6.1.2-r2 font-dejavu=2.37-r6 gosu \
 && rm -rf /usr/local/lib/python3.12/site-packages/setuptools* \
 && pip install --no-cache-dir --require-hashes -r requirements.lock.txt \
 && adduser -D -u 10001 forge \
 && mkdir -p /artifacts/projects && chown -R forge:forge /artifacts \
 && rm -rf /var/cache/apk/*
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY scripts/ ./scripts/
# Writable, persistent telemetry-state directory for replay-state + lock files.
# Owned by forge in-image; entrypoint re-chowns after named-volume mount (volumes are root-owned).
RUN mkdir -p /opt/forge/telemetry-state && chown forge:forge /opt/forge/telemetry-state
COPY services/docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
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
