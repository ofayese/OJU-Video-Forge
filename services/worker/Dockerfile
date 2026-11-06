FROM python:3.12-slim-bookworm
WORKDIR /opt/forge
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/* && useradd -r -u 10001 forge
COPY requirements.lock.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt
COPY app/ ./app/
USER forge
CMD ["rq","worker","video","--url","redis://redis:6379/0"]
