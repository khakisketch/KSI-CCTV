# DGX Spark (GB10, sm_121, CUDA 13 / cu130 / ARM64) 타깃 베이스 이미지
# CPU-only 배포가 필요하면 --build-arg BASE_IMAGE=python:3.11-slim 으로 오버라이드 가능
ARG BASE_IMAGE=nvcr.io/nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04
FROM ${BASE_IMAGE}

# ARM64(aarch64) + CUDA 13 환경 빌드 인자
ARG TARGETARCH=linux/arm64
ARG CUDA_INDEX=https://download.pytorch.org/whl/cu126
# GPU 빌드 여부 — CPU-only(예: python:3.11-slim) 빌드 시 --build-arg ENABLE_GPU=0
ARG ENABLE_GPU=1

# Python 3.11 설치 (CUDA 베이스에는 Python이 없음)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-dev \
    python3-pip \
    python3.11-distutils \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    ffmpeg \
    curl \
    && ln -sf /usr/bin/python3.11 /usr/bin/python \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && rm -rf /var/lib/apt/lists/*

# 작업 디렉토리
WORKDIR /app

# requirements 복사 및 설치 (레이어 캐시 효율화)
COPY requirements.txt .

# CUDA 13/cu130 호환 패키지를 먼저 설치 후 나머지 requirements 설치
# DGX Spark ARM64: torch는 cu126 인덱스 사용 (cu130 공식 릴리스 전까지 최근접)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        torch==2.6.0 \
        torchvision==0.21.0 \
        torchaudio==2.6.0 \
        --index-url ${CUDA_INDEX} && \
    pip install --no-cache-dir -r requirements.txt --extra-index-url ${CUDA_INDEX} && \
    # GPU 빌드면 CPU onnxruntime(requirements 기본)를 제거하고 onnxruntime-gpu로 교체.
    # 두 패키지 공존 금지 → uninstall 후 설치. GPU 휠 부재 시 CPU로 폴백(빌드 중단 방지).
    if [ "${ENABLE_GPU}" = "1" ]; then \
        pip uninstall -y onnxruntime && \
        (pip install --no-cache-dir onnxruntime-gpu || pip install --no-cache-dir onnxruntime==1.20.1); \
    fi

# 소스 코드 복사 (.dockerignore로 .venv/.git/.env/logs/data/models 제외됨)
COPY . .

# 비root 사용자 생성 (least-privilege — systemd의 User=ksi-cctv 와 대칭)
# UID 1000은 compose 볼륨 호스트 소유권(chown 1000:1000)과 맞춰야 함
RUN useradd -m -u 1000 -s /bin/bash appuser && \
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
