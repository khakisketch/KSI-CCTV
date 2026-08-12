# DGX Spark (GB10, sm_121, CUDA 13 / cu130 / ARM64) target base image.
FROM nvidia/cuda@sha256:f2c12914cf4751e61073843724275aa35d4817e01dbdc87eac03905971628c6e

# ARM64(aarch64) + CUDA 13 환경 빌드 인자
# GPU 빌드 여부 — CPU-only 개발/랩 이미지에서만 --build-arg ENABLE_GPU=0을 사용한다.
ARG ENABLE_GPU=1
# ARM64 CUDA provider wheels are not published by PyPI. The release runner must supply
# a reviewed, immutable internal artifact URL and SHA-256 for signed Appliance builds.
ARG ONNXRUNTIME_GPU_WHEEL_URL=""
ARG ONNXRUNTIME_GPU_WHEEL_SHA256=""
ARG OCI_SOURCE=""
ARG OCI_REVISION=""
ARG OCI_VERSION=""
ENV CUDA_INDEX=https://download.pytorch.org/whl/cu130 \
    ONNXRUNTIME_VERSION=1.28.0 \
    GENERAL_MODEL_SHA256=9773cd4f4d07fe55f8c86f38f1bd4a7c2679754d44170abbf20d87f4b8d17aad \
    PPE_MODEL_SHA256=1ba914b176fa3072a2a2b019404535a2995abe6fe473041975484b543f1078f1 \
    FIRE_SMOKE_MODEL_SHA256=a639beb65e3a1f2a5e476852d2aba278070ab88422b33fa79eeed6149f816ab2

# CUDA 13 Ubuntu 24.04 base includes the distribution Python (3.12).
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-dev \
    python3-pip \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    ffmpeg \
    curl \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# 작업 디렉토리
WORKDIR /app

# Ubuntu 24.04 marks its system Python as externally managed. This image intentionally
# installs the application runtime into that isolated container environment.
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# requirements 복사 및 설치 (레이어 캐시 효율화)
COPY requirements.txt requirements-onvif.txt ./

# CUDA 13/cu130 호환 패키지를 먼저 설치 후 나머지 requirements 설치
RUN python -c "from urllib.parse import urlsplit; import sys; v=sys.argv[1]; u=urlsplit(v); raise SystemExit(0 if v and all(32 < ord(c) < 127 and not c.isspace() for c in v) and u.scheme == 'https' and u.hostname and not u.username and not u.password and not u.query and not u.fragment else 1)" "${CUDA_INDEX}" || { \
        echo "CUDA_INDEX must be HTTPS without credentials, query, or fragment" >&2; exit 1; \
    }; \
    pip install --no-cache-dir \
        torch==2.13.0 \
        torchvision==0.28.0 \
        --index-url "${CUDA_INDEX}" && \
    pip install --no-cache-dir -r requirements.txt -r requirements-onvif.txt --extra-index-url "${CUDA_INDEX}" && \
    # Signed GPU Appliance images must fail closed when the required provider cannot be installed.
    # CPU fallback is permitted only for an explicitly requested development/lab image.
    if [ "${ENABLE_GPU}" = "1" ]; then \
        test -n "${ONNXRUNTIME_GPU_WHEEL_URL}" || { \
            echo "ONNXRUNTIME_GPU_WHEEL_URL is required for ARM64 Appliance GPU builds" >&2; exit 1; \
        }; \
        python -c "import re,sys; raise SystemExit(0 if re.fullmatch(r'[0-9a-fA-F]{64}', sys.argv[1]) else 1)" "${ONNXRUNTIME_GPU_WHEEL_SHA256}" || { \
            echo "ONNXRUNTIME_GPU_WHEEL_SHA256 must be exactly 64 hexadecimal characters" >&2; exit 1; \
        }; \
        python -c "from urllib.parse import urlsplit; import sys; v=sys.argv[1]; u=urlsplit(v); raise SystemExit(0 if v and all(32 < ord(c) < 127 and not c.isspace() for c in v) and u.scheme == 'https' and u.hostname and not u.username and not u.password and not u.query and not u.fragment else 1)" "${ONNXRUNTIME_GPU_WHEEL_URL}" || { \
            echo "ONNXRUNTIME_GPU_WHEEL_URL must be HTTPS without credentials, query, or fragment" >&2; exit 1; \
        }; \
        wheel_name="$(basename "${ONNXRUNTIME_GPU_WHEEL_URL}")"; \
        case "${wheel_name}" in *.whl) ;; *) echo "ONNXRUNTIME_GPU_WHEEL_URL must end in .whl" >&2; exit 1 ;; esac; \
        wheel_path="/tmp/${wheel_name}"; \
        pip uninstall -y onnxruntime && \
        curl --disable --fail --location --proto '=https' --proto-redir '=https' --max-redirs 3 --retry 3 --output "${wheel_path}" "${ONNXRUNTIME_GPU_WHEEL_URL}" && \
        python -c "import hashlib,hmac,sys; raise SystemExit(0 if hmac.compare_digest(hashlib.file_digest(open(sys.argv[2], 'rb'), 'sha256').hexdigest(), sys.argv[1].lower()) else 1)" "${ONNXRUNTIME_GPU_WHEEL_SHA256}" "${wheel_path}" && \
        pip install --no-cache-dir "${wheel_path}" && \
        rm "${wheel_path}" && \
        python -c "import onnxruntime as ort, sys; assert ort.__version__ == sys.argv[1], (ort.__version__, sys.argv[1]); assert 'CUDAExecutionProvider' in ort.get_available_providers(), ort.get_available_providers()" "${ONNXRUNTIME_VERSION}"; \
    fi

# Copy only runtime inputs. Site camera records, backups, reports, tests, and tooling never
# enter an image layer, even if a developer's working tree contains ignored files.
COPY src/ ./src/
COPY config/alert_preferences.yaml config/camera_profiles.yaml config/default.yaml config/event_policies.yaml config/model_profiles.yaml config/proximity.example.yaml config/zones.example.yaml ./config/
COPY config/prompts/construction.yaml config/prompts/factory.yaml config/prompts/general.yaml config/prompts/parking.yaml config/prompts/school.yaml config/prompts/warehouse.yaml ./config/prompts/
COPY config/prompts/__init__.py ./config/prompts/__init__.py
COPY models/yolo26.onnx models/yolo26_ppe.onnx models/yolo26_fire_smoke.onnx ./models/
COPY scripts/docker-entrypoint.sh ./scripts/docker-entrypoint.sh
RUN printf '[]\n' > /app/config/cameras.json

# The signed release builder forbids separate model payloads, so every enabled built-in model
# must be present in the engine image before it can be signed.
RUN test -s /app/models/yolo26.onnx && \
    test -s /app/models/yolo26_ppe.onnx && \
    test -s /app/models/yolo26_fire_smoke.onnx && \
    python -c "import hashlib,hmac,re,sys; pairs=zip(sys.argv[1::2],sys.argv[2::2],strict=True); raise SystemExit(0 if all(re.fullmatch(r'[0-9a-fA-F]{64}', expected) and hmac.compare_digest(hashlib.file_digest(open(path, 'rb'), 'sha256').hexdigest(), expected.lower()) for expected,path in pairs) else 1)" \
        "${GENERAL_MODEL_SHA256}" /app/models/yolo26.onnx \
        "${PPE_MODEL_SHA256}" /app/models/yolo26_ppe.onnx \
        "${FIRE_SMOKE_MODEL_SHA256}" /app/models/yolo26_fire_smoke.onnx

RUN python -c "from urllib.parse import urlsplit; import re,sys; u=urlsplit(sys.argv[1]); raise SystemExit(0 if u.scheme == 'https' and u.hostname and not u.username and not u.password and not u.query and not u.fragment and re.fullmatch(r'[0-9a-f]{40}', sys.argv[2]) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', sys.argv[3]) else 1)" "${OCI_SOURCE}" "${OCI_REVISION}" "${OCI_VERSION}"

LABEL org.ksi-cctv.onnxruntime.version="${ONNXRUNTIME_VERSION}" \
      org.ksi-cctv.gpu.enabled="${ENABLE_GPU}" \
      org.ksi-cctv.onnxruntime.wheel.sha256="${ONNXRUNTIME_GPU_WHEEL_SHA256}" \
      org.ksi-cctv.model.general.sha256="${GENERAL_MODEL_SHA256}" \
      org.ksi-cctv.model.ppe.sha256="${PPE_MODEL_SHA256}" \
      org.ksi-cctv.model.fire-smoke.sha256="${FIRE_SMOKE_MODEL_SHA256}" \
      org.opencontainers.image.source="${OCI_SOURCE}" \
      org.opencontainers.image.revision="${OCI_REVISION}" \
      org.opencontainers.image.version="${OCI_VERSION}"

# 비root 사용자 생성 (least-privilege — systemd의 User=ksi-cctv 와 대칭)
# UID 1000은 compose 볼륨 호스트 소유권(chown 1000:1000)과 맞춰야 함
RUN useradd --non-unique -m -u 1000 -s /bin/bash appuser && \
    mkdir -p /app/logs /app/data/captures /app/models/uploaded && \
    chown -R appuser:appuser /app

# 진입점 스크립트 실행 권한 부여
RUN chmod +x /app/scripts/docker-entrypoint.sh

# 비root 사용자로 전환
USER appuser

# 헬스체크 엔드포인트 (8081) + 대시보드 (8080) 동시 노출
EXPOSE 8080 8081

# HEALTHCHECK: liveness 전용 엔드포인트(/health/live, 프로세스 생존 시 항상 200) 사용.
# /health는 카메라 stale·ollama 미가동 등에서 503을 반환하므로 컨테이너 liveness로 쓰면
# 일시적 degraded에도 컨테이너가 unhealthy→재시작 루프에 빠진다. 준비도/진단은 /health, /health/ready로.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8081/health/live || exit 1

CMD ["/app/scripts/docker-entrypoint.sh"]
