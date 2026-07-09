FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=4

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 espeak-ng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
RUN mkdir -p /app/models /app/cache

EXPOSE 8880
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8880", "--workers", "1"]
