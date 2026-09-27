# Воркер пайплайна для сервера без GPU: python + ffmpeg + faster-whisper (CPU int8).
# Собирается один раз на VDS: scripts/remote/remote.py setup
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "faster-whisper>=1.0.3" "jsonschema>=4.20"
ENV HF_HOME=/work/.hf-cache PYTHONUNBUFFERED=1 OMP_NUM_THREADS=3
WORKDIR /work
