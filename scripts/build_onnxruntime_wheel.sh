#!/usr/bin/env bash
# Build and GPU-qualify the ARM64 ONNX Runtime CUDA wheel used by the Appliance image.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-$ROOT/dist/onnxruntime}"
IMAGE_TAG="ksi-cctv-onnxruntime-wheel:local-$$"
CONTAINER_ID=""
ARTIFACT_DIR=""

cleanup() {
    if [ -n "$CONTAINER_ID" ]; then
        docker rm -f "$CONTAINER_ID" >/dev/null 2>&1 || true
    fi
    if [ -n "$ARTIFACT_DIR" ]; then
        rm -rf "$ARTIFACT_DIR"
    fi
    docker image rm "$IMAGE_TAG" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if ! docker info >/dev/null 2>&1; then
    printf 'error: a running Docker daemon is required\n' >&2
    exit 1
fi

case "$(uname -m)" in
    aarch64|arm64) ;;
    *) printf 'error: GPU wheel qualification requires an ARM64 DGX Spark runner\n' >&2; exit 1 ;;
esac

for model in yolo26.onnx yolo26_ppe.onnx yolo26_fire_smoke.onnx; do
    if [ ! -s "$ROOT/models/$model" ]; then
        printf 'error: required model is missing: %s\n' "$ROOT/models/$model" >&2
        exit 1
    fi
done

mkdir -p "$(dirname "$OUTPUT_DIR")"
ARTIFACT_DIR="$(mktemp -d "$(dirname "$OUTPUT_DIR")/.onnxruntime-artifact.XXXXXX")"
docker build \
    --platform linux/arm64 \
    --file "$ROOT/docker/onnxruntime-wheel.Dockerfile" \
    --tag "$IMAGE_TAG" \
    "$ROOT"

# Provider enumeration alone does not prove CUDA-only execution. Require two inferences for every
# signed model with ORT's CPU EP fallback disabled.
docker run --rm --gpus all \
    --volume "$ROOT/models:/models:ro" \
    --volume "$ARTIFACT_DIR:/artifacts" \
    "$IMAGE_TAG" \
    python3 /usr/local/bin/qualify_onnxruntime_gpu.py \
    /models/yolo26.onnx /models/yolo26_ppe.onnx /models/yolo26_fire_smoke.onnx \
    --json-out /artifacts/qualification.json

CONTAINER_ID="$(docker create "$IMAGE_TAG")"
docker cp "$CONTAINER_ID:/wheel/." "$ARTIFACT_DIR/"
docker rm "$CONTAINER_ID" >/dev/null
CONTAINER_ID=""

shopt -s nullglob
wheels=("$ARTIFACT_DIR"/*.whl)
if [ "${#wheels[@]}" -ne 1 ]; then
    printf 'error: expected exactly one wheel in %s\n' "$ARTIFACT_DIR" >&2
    exit 1
fi

python3 - "${wheels[0]}" "$ARTIFACT_DIR/qualification.json" "$ARTIFACT_DIR/manifest.json" "$IMAGE_TAG" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

wheel = Path(sys.argv[1])
qualification = Path(sys.argv[2])
manifest = Path(sys.argv[3])
image = sys.argv[4]
labels = json.loads(
    subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{json .Config.Labels}}"], text=True)
)
payload = {
    "wheel": {"filename": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()},
    "qualification_report": {
        "filename": qualification.name,
        "sha256": hashlib.sha256(qualification.read_bytes()).hexdigest(),
    },
    "build": {
        "onnxruntime_version": labels["org.ksi-cctv.onnxruntime.version"],
        "onnxruntime_commit": labels["org.ksi-cctv.onnxruntime.commit"],
        "cuda_base": labels["org.ksi-cctv.cuda-base"],
        "cuda_architectures": labels["org.ksi-cctv.cuda-architectures"],
    },
}
manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

mkdir -p "$OUTPUT_DIR"
cp "$ARTIFACT_DIR"/*.whl "$ARTIFACT_DIR/qualification.json" "$ARTIFACT_DIR/manifest.json" "$OUTPUT_DIR/"
ARTIFACT_DIR=""
sha256sum "$OUTPUT_DIR"/*.whl
