FROM python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322
WORKDIR /opt/forge
RUN apk update && apk upgrade --no-cache \
 && apk add --no-cache ffmpeg=6.1.2-r2 ca-certificates font-dejavu=2.37-r6 \
 && adduser -D -u 10001 forge \
 && mkdir -p /artifacts/projects && chown -R forge:forge /artifacts \
 && rm -rf /var/cache/apk/*
COPY requirements.lock.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt
COPY app/ ./app/
USER forge
CMD ["python","-m","app.worker"]
