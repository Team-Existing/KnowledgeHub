# One image for every backend role in deploy/docker-compose.yml:
# the API instances, the Celery worker and beat, and the provisioning job.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf

# git: the git-ADR connector reads commit dates and authors
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# CPU-only PyTorch for sentence-transformers (the CUDA build is several GB larger)
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install -r requirements.txt

# Bake the embedding model into the image: at runtime the app is offline
# (TRANSFORMERS_OFFLINE=1) and never downloads anything.
ARG LOCAL_EMBED_MODEL=all-MiniLM-L6-v2
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${LOCAL_EMBED_MODEL}')" \
 && chmod -R a+rX /opt/hf

COPY app ./app
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 kh && chown -R kh /app
USER kh

ENV LOCAL_EMBED_MODEL=${LOCAL_EMBED_MODEL} \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    HF_HUB_OFFLINE=1

EXPOSE 8000
# --proxy-headers: trust X-Forwarded-* from the load balancer in front
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
