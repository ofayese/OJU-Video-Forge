# Pin this base digest and ComfyUI commit during Gate D approval.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime
RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
ARG COMFYUI_REF=master
RUN git clone https://github.com/Comfy-Org/ComfyUI.git /opt/ComfyUI \
 && cd /opt/ComfyUI && git checkout "$COMFYUI_REF" \
 && pip install --no-cache-dir -r requirements.txt
WORKDIR /opt/ComfyUI
RUN useradd --create-home --uid 10001 --user-group forge \
 && mkdir -p user \
 && chown forge:forge user
USER forge
EXPOSE 8188
CMD ["python","main.py","--listen","0.0.0.0","--port","8188","--lowvram"]
