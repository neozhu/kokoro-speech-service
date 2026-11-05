# Kokoro Speech Service

A lightweight FastAPI backend for generating WAV text-to-speech audio with Kokoro-ONNX 0.6.1.

## Features

- `GET /health` unauthenticated health check.
- `POST /v1/audio/speech` Kokoro speech generation endpoint.
- Static bearer-token API key authentication via `TTS_API_KEY`.
- WAV responses with `X-TTS-Cache: HIT | MISS` cache metadata.
- Local file cache with atomic temporary-file writes.
- Structured, engine-versioned cache keys and deduplication of identical in-flight requests within one worker.
- Startup-time Kokoro model loading so requests reuse one in-memory model.
- Async semaphore-based CPU concurrency control via `TTS_CONCURRENCY`.
- Docker and Docker Compose deployment files.

## Configuration

| Variable | Required | Default | Description |
| --- | ---: | --- | --- |
| `TTS_API_KEY` | yes | - | Bearer token used by `/v1/audio/speech`. |
| `KOKORO_MODEL_PATH` | no | `/app/models/kokoro-v1.0.onnx` | Kokoro ONNX model path. |
| `KOKORO_VOICES_PATH` | no | `/app/models/voices-v1.0.bin` | Kokoro voices file path. |
| `TTS_CACHE_DIR` | no | `/app/cache` | Directory for cached WAV files. |
| `TTS_CONCURRENCY` | no | `1` | Maximum concurrent TTS generations. |
| `OMP_NUM_THREADS` | no | `4` | ONNX Runtime CPU thread tuning. |

## Running locally

Install dependencies, download the Kokoro model files into `./models`, and start Uvicorn:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
./scripts/download-models.sh
export TTS_API_KEY=your-long-random-secret
export KOKORO_MODEL_PATH="$PWD/models/kokoro-v1.0.onnx"
export KOKORO_VOICES_PATH="$PWD/models/voices-v1.0.bin"
export TTS_CACHE_DIR="$PWD/cache"
uvicorn app:app --host 0.0.0.0 --port 48731 --workers 1
```

## API

### Health

```bash
curl http://localhost:48731/health
```

### Generate speech

```bash
curl -X POST http://localhost:48731/v1/audio/speech \
  -H "Authorization: Bearer $TTS_API_KEY" \
  -H "Content-Type: application/json" \
  -o speech.wav \
  -d '{
    "input": "Hello, this is a text to speech test.",
    "voice": "af_sarah",
    "speed": 1.0,
    "lang": "en-us",
    "response_format": "wav"
  }'
```

## Docker Compose

```bash
cp .env.example .env
# edit .env and set TTS_API_KEY
mkdir -p cache
./scripts/download-models.sh
docker compose up --build
```

After upgrading, rebuild and recreate the service with `docker compose up -d --build`.
The Docker image uses Python 3.12, within Kokoro-ONNX 0.6.1's supported Python range (3.10–3.13).

Cache keys include all speech parameters and the installed Kokoro-ONNX version. Replacing model or
voices files without changing the engine version requires clearing cached WAV files while the
service is stopped. Identical requests share one generation within the configured single worker;
different requests can still generate concurrently up to `TTS_CONCURRENCY`.

The compose file mounts `./models` from the host into `/app/models` inside the container. If startup fails with `Kokoro model files are missing`, verify these files exist on the host before starting Docker:

```bash
./scripts/download-models.sh
find models -maxdepth 1 -type f -name 'kokoro-v1.0.onnx' -o -name 'voices-v1.0.bin'
docker compose up --build
```

## GHCR images

The CI workflow builds a Linux AMD64 image and publishes it to
`ghcr.io/neozhu/kokoro-speech-service` after the Python checks pass:

- Pushes to `main` publish only the `latest` image tag.
- Pushing a `v*` Git tag also publishes only `latest`.
- Pull requests and pushes to `master` or `work` validate the build without publishing.
- The workflow can also be run manually; publishing is enabled only for the default branch or a `v*` tag.

Publishing uses the built-in `GITHUB_TOKEN` with `packages: write`; no additional secret is needed.
For anonymous pulls, set the package visibility to public in GitHub Packages after its first publication.

```bash
docker pull ghcr.io/neozhu/kokoro-speech-service:latest
```

Model files and generated audio are excluded from the build context. Continue mounting `/app/models`
and `/app/cache` and supplying `TTS_API_KEY` when running the image, as in the Compose configuration.

## Cache regression tests

With the dependencies installed, run `python -m unittest discover -s tests -v`.
These tests use a fake synthesis model and real WAV writes; model files are not required.
