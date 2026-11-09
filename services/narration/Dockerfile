FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
WORKDIR /opt/forge
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg=7:5.1.9-0+deb12u1 \
    espeak-ng=1.51+dfsg-10+deb12u2 tesseract-ocr=5.3.0-2 \
    tesseract-ocr-eng=1:4.1.0-2 libgomp1=12.2.0-14+deb12u1 fonts-dejavu-core=2.37-6 \
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
