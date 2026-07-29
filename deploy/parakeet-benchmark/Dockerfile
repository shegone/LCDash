FROM pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir \
    accelerate==1.12.0 \
    librosa==0.11.0 \
    soundfile==0.13.1 \
    transformers==5.9.0

WORKDIR /benchmark

COPY transcribe.py /benchmark/transcribe.py

ENTRYPOINT ["python", "/benchmark/transcribe.py"]
