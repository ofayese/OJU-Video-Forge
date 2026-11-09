FROM pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime@sha256:c16f4c749e2d9e96878875cdf6cc45cddda1d1a36fddd371dd6f2360f1b6e2a2
RUN apt-get update && apt-get install -y --no-install-recommends gcc git ffmpeg \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
ARG COMFYUI_COMMIT=0decb39ee5b5e17c200334589fc317dbbe98310e
RUN git clone --filter=blob:none --no-checkout https://github.com/Comfy-Org/ComfyUI.git /opt/ComfyUI \
 && cd /opt/ComfyUI \
 && git fetch --depth 1 origin "$COMFYUI_COMMIT" \
 && git checkout --detach FETCH_HEAD \
 && test "$(git rev-parse HEAD)" = "$COMFYUI_COMMIT" \
 && pip install --no-cache-dir -r requirements.txt
WORKDIR /opt/ComfyUI
RUN groupadd --gid 10001 forge \
 && useradd --create-home --uid 10001 --gid 10001 forge \
 && mkdir -p user models input output \
 && chown forge:forge user models input output
USER 10001:10001
RUN for path in models input output; do touch "$path/.write-check"; rm "$path/.write-check"; done
EXPOSE 8188
CMD ["python","main.py","--listen","0.0.0.0","--port","8188","--lowvram","--temp-directory","/home/forge","--previews-directory","/home/forge/previews"]
