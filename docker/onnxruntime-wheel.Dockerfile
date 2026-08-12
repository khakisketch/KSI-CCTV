# syntax=docker/dockerfile:1
# Reproducible ARM64 CUDA provider wheel for the DGX Spark Appliance. Build it with
# scripts/build_onnxruntime_wheel.sh; do not publish a wheel until that script's GPU
# session verification has passed.
FROM nvidia/cuda@sha256:c2621d98e7de80c2aec5eb8403b19c67454c8f5b0c929e8588fd3563c9b6558d AS builder

ARG BUILD_JOBS=8
ENV ONNXRUNTIME_VERSION=1.28.0 \
    ONNXRUNTIME_COMMIT=da9b5e364c465de65c49d91e696cd6485270757f

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    git \
    libcurl4-openssl-dev \
    libssl-dev \
    ninja-build \
    patchelf \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN git init /opt/onnxruntime && \
    git -C /opt/onnxruntime remote add origin https://github.com/microsoft/onnxruntime.git && \
    git -C /opt/onnxruntime fetch --depth 1 origin "${ONNXRUNTIME_COMMIT}" && \
    git -C /opt/onnxruntime checkout --detach FETCH_HEAD && \
    test "$(git -C /opt/onnxruntime rev-parse HEAD)" = "${ONNXRUNTIME_COMMIT}" && \
    git -C /opt/onnxruntime submodule update --init --recursive

WORKDIR /opt/onnxruntime

RUN python3 -m venv /opt/ort-build-venv && \
    /opt/ort-build-venv/bin/pip install --no-cache-dir pip==25.1.1 setuptools==80.9.0 wheel==0.45.1 numpy==2.1.1 packaging==24.2 && \
    # Keep contrib ops enabled: the CUDA provider's ONNX Attention implementation and LLM
    # objects depend on them. Pair native sm_121 with PTX so the generic LLM object library links.
    PATH="/opt/ort-build-venv/bin:${PATH}" ./build.sh --config Release --update --build --build_wheel --skip_tests \
        --parallel "${BUILD_JOBS}" --cmake_generator Ninja --use_cuda \
        --cuda_home /usr/local/cuda --cudnn_home /usr \
        --cmake_extra_defines onnxruntime_BUILD_UNIT_TESTS=OFF \
        "CMAKE_CUDA_ARCHITECTURES=121-real;121-virtual" \
        --allow_running_as_root && \
    mkdir -p /opt/artifacts && \
    cp /opt/onnxruntime/build/Linux/Release/dist/*.whl /opt/artifacts/ && \
    test "$(find /opt/artifacts -maxdepth 1 -name '*.whl' -type f | wc -l)" -eq 1

FROM nvidia/cuda@sha256:f2c12914cf4751e61073843724275aa35d4817e01dbdc87eac03905971628c6e AS wheel

ENV ONNXRUNTIME_VERSION=1.28.0 \
    ONNXRUNTIME_COMMIT=da9b5e364c465de65c49d91e696cd6485270757f
LABEL org.ksi-cctv.cuda-base="nvidia/cuda@sha256:c2621d98e7de80c2aec5eb8403b19c67454c8f5b0c929e8588fd3563c9b6558d" \
      org.ksi-cctv.onnxruntime.version="${ONNXRUNTIME_VERSION}" \
      org.ksi-cctv.onnxruntime.commit="${ONNXRUNTIME_COMMIT}" \
      org.ksi-cctv.cuda-architectures="121"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/artifacts/*.whl /wheel/
COPY scripts/qualify_onnxruntime_gpu.py /usr/local/bin/qualify_onnxruntime_gpu.py

RUN python3 -m pip install --no-cache-dir --break-system-packages numpy==2.1.1 /wheel/*.whl && \
    python3 -c "import onnxruntime as ort; assert 'CUDAExecutionProvider' in ort.get_available_providers(), ort.get_available_providers()"
