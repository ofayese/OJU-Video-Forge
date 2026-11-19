FROM python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322
WORKDIR /opt/forge
RUN apk update && apk upgrade --no-cache \
 && apk add --no-cache ffmpeg=6.1.2-r2 ca-certificates font-dejavu=2.37-r6 gosu espeak-ng=1.52.0-r0 \
 && adduser -D -u 10001 forge \
 && mkdir -p /artifacts/projects && chown -R forge:forge /artifacts \
 && rm -rf /var/cache/apk/*
COPY requirements.lock.txt .
# setuptools is removed only after the install: pip bootstraps an isolated build env for
# any sdist it has to build, so deleting it beforehand makes pip reinstall it.
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt \
 && rm -rf /usr/local/lib/python3.12/site-packages/setuptools* \
 && rm -rf /usr/local/lib/python3.12/site-packages/pip* \
 && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12
COPY app/ ./app/
# Writable, persistent telemetry-state directory for replay-state + lock files.
# Owned by forge in-image; entrypoint re-chowns after named-volume mount (volumes are root-owned).
RUN mkdir -p /opt/forge/telemetry-state && chown forge:forge /opt/forge/telemetry-state
# Read-only signed observation mount point (parent dir for observation.json).
# Owned by forge; the observation file itself is mounted read-only at runtime.
RUN mkdir -p /opt/forge/telemetry/observation && chown forge:forge /opt/forge/telemetry/observation
COPY services/docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8000
# Declare the non-root user (DS-0002). The gosu entrypoint still starts as root so it can
# re-chown the root-owned named volumes (cap_add SETUID/SETGID); docker run --user overrides it.
USER forge
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python","-m","app.worker"]
