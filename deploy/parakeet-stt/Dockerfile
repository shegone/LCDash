FROM pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
        ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir \
    accelerate==1.12.0 \
    fastapi==0.115.12 \
    librosa==0.11.0 \
    python-multipart==0.0.20 \
    soundfile==0.13.1 \
    transformers==5.9.0 \
    uvicorn[standard]==0.34.3

WORKDIR /app
COPY app.py .

RUN useradd --create-home --uid 1000 lcdash \
    && mkdir -p /models \
    && chown -R lcdash:lcdash /app /models

USER 1000:1000
EXPOSE 8004
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8004"]
