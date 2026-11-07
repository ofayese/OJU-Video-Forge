FROM python:3.12-alpine3.22
WORKDIR /opt/forge
RUN apk update && apk upgrade --no-cache \
 && apk add --no-cache ffmpeg ca-certificates \
 && adduser -D -u 10001 forge \
 && rm -rf /var/cache/apk/*
COPY requirements.lock.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt
COPY app/ ./app/
USER forge
CMD ["rq","worker","video","--url","redis://redis:6379/0"]
