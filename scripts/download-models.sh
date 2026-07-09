#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-models}"
MODEL_VARIANT="${MODEL_VARIANT:-kokoro-v1.0.onnx}"
VOICES_FILE="voices-v1.0.bin"
BASE_URL="${KOKORO_MODEL_BASE_URL:-https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0}"

mkdir -p "$MODEL_DIR"

download_if_missing() {
  local file_name="$1"
  local target_path="$MODEL_DIR/$file_name"
  local url="$BASE_URL/$file_name"

  if [[ -s "$target_path" ]]; then
    echo "Already exists: $target_path"
    return
  fi

  echo "Downloading $url -> $target_path"
  python - "$url" "$target_path" <<'PY'
import sys
import urllib.request
from pathlib import Path

url = sys.argv[1]
target = Path(sys.argv[2])
tmp = target.with_suffix(target.suffix + ".part")
try:
    with urllib.request.urlopen(url) as response, tmp.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            downloaded += len(chunk)
            if total:
                percent = downloaded * 100 // total
                print(f"\r{target.name}: {percent}%", end="", flush=True)
    if total:
        print()
    tmp.replace(target)
except Exception:
    tmp.unlink(missing_ok=True)
    raise
PY
}

download_if_missing "$MODEL_VARIANT"
download_if_missing "$VOICES_FILE"

echo "Kokoro model files are ready in $MODEL_DIR"
