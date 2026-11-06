FROM python:3.12-alpine3.22
WORKDIR /opt/forge
COPY requirements.lock.txt .
RUN apk update && apk upgrade --no-cache \
 && pip install --no-cache-dir --require-hashes -r requirements.lock.txt \
 && adduser -D -u 10001 forge \
 && rm -rf /var/cache/apk/*
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
USER forge
EXPOSE 8000
CMD ["uvicorn","app.api:app","--host","0.0.0.0","--port","8000","--proxy-headers","--forwarded-allow-ips",""]
