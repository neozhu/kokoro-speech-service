from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import anyio
import soundfile as sf
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import FileResponse
from kokoro_onnx import Kokoro
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("kokoro_tts")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

DEFAULT_MODEL_PATH = "/app/models/kokoro-v1.0.onnx"
DEFAULT_VOICES_PATH = "/app/models/voices-v1.0.bin"
DEFAULT_CACHE_DIR = "/app/cache"
MAX_INPUT_LENGTH = 3000


class SpeechRequest(BaseModel):
    input: str = Field(..., max_length=MAX_INPUT_LENGTH)
    voice: str = "af_sarah"
    speed: float = Field(1.0, ge=0.5, le=2.0)
    lang: str = "en-us"
    response_format: Literal["wav"] = "wav"

    @field_validator("input")
    @classmethod
    def trim_and_validate_input(cls, value: str) -> str:
        value = value.strip()
        return value


class TTSState:
    model: Kokoro | None = None
    semaphore: asyncio.Semaphore | None = None
    cache_dir: Path | None = None


state = TTSState()


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    model_path = os.getenv("KOKORO_MODEL_PATH", DEFAULT_MODEL_PATH)
    voices_path = os.getenv("KOKORO_VOICES_PATH", DEFAULT_VOICES_PATH)
    cache_dir = Path(os.getenv("TTS_CACHE_DIR", DEFAULT_CACHE_DIR))
    concurrency = _env_int("TTS_CONCURRENCY", 1)

    logger.info("Loading Kokoro model", extra={"model_path": model_path, "voices_path": voices_path})
    if not Path(model_path).is_file() or not Path(voices_path).is_file():
        logger.error("Kokoro model files are not configured or missing")
        raise RuntimeError("Kokoro model files are not configured or missing")

    cache_dir.mkdir(parents=True, exist_ok=True)
    state.model = Kokoro(model_path, voices_path)
    state.semaphore = asyncio.Semaphore(concurrency)
    state.cache_dir = cache_dir
    logger.info("Kokoro model loaded", extra={"cache_dir": str(cache_dir), "concurrency": concurrency})
    try:
        yield
    finally:
        state.model = None
        state.semaphore = None
        state.cache_dir = None


app = FastAPI(title="Kokoro Speech Service", version="1.0.0", lifespan=lifespan)


def require_api_key(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("TTS_API_KEY")
    if not expected:
        logger.error("TTS_API_KEY is not configured")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="TTS API key is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def cache_key(request: SpeechRequest) -> str:
    payload = f"{request.input}{request.voice}{request.speed}{request.lang}{request.response_format}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_wav(path: Path, text: str, voice: str, speed: float, lang: str) -> None:
    if state.model is None:
        raise RuntimeError("Kokoro model is not loaded")
    samples, sample_rate = state.model.create(text, voice=voice, speed=speed, lang=lang)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        sf.write(tmp_path, samples, sample_rate, format="WAV")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/audio/speech", dependencies=[Depends(require_api_key)])
async def create_speech(request: SpeechRequest) -> Response:
    if not request.input:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Input must not be empty")

    if state.cache_dir is None or state.semaphore is None or state.model is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="TTS model is not configured")

    logger.info("TTS request received", extra={"input_length": len(request.input), "voice": request.voice, "lang": request.lang})
    audio_path = state.cache_dir / f"{cache_key(request)}.wav"
    start = time.perf_counter()

    if audio_path.exists():
        logger.info("TTS cache HIT", extra={"generation_time": 0.0})
        return FileResponse(audio_path, media_type="audio/wav", headers={"X-TTS-Cache": "HIT"})

    async with state.semaphore:
        if audio_path.exists():
            logger.info("TTS cache HIT", extra={"generation_time": 0.0})
            return FileResponse(audio_path, media_type="audio/wav", headers={"X-TTS-Cache": "HIT"})
        try:
            await anyio.to_thread.run_sync(_write_wav, audio_path, request.input, request.voice, request.speed, request.lang)
        except Exception as exc:
            logger.exception("TTS generation failed")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="TTS generation failed") from exc

    elapsed = time.perf_counter() - start
    logger.info("TTS cache MISS", extra={"generation_time": elapsed})
    return FileResponse(audio_path, media_type="audio/wav", headers={"X-TTS-Cache": "MISS"})
