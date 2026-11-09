FROM python:3.12-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /opt/forge
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg=7:7.1.5-0+deb13u1 \
    espeak-ng=1.52.0+dfsg-5 tesseract-ocr=5.5.0-1+b1 \
    tesseract-ocr-eng=1:4.1.0-2 libgomp1=14.2.0-19 fonts-dejavu-core=2.37-8 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 forge && useradd --uid 10001 --gid 10001 --create-home forge \
 && mkdir -p /artifacts/projects /models/asr /usr/share/fonts/dejavu \
 && ln -s /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf /usr/share/fonts/dejavu/DejaVuSans.ttf \
 && chown -R forge:forge /artifacts
COPY requirements.lock.txt requirements-narration.lock.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt -r requirements-narration.lock.txt
COPY app/ ./app/
ENV HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 \
    OMP_NUM_THREADS=2 NARRATION_ASR_MODEL=/models/asr/faster-whisper-tiny
USER 10001:10001
CMD ["python", "-m", "app.worker"]
