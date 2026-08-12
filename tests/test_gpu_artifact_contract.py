from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_gpu_wheel_recipe_exports_from_a_non_cache_path_and_pins_cuda13_ort() -> None:
    dockerfile = (ROOT / "docker/onnxruntime-wheel.Dockerfile").read_text(encoding="utf-8")

    assert "ONNXRUNTIME_VERSION=1.28.0" in dockerfile
    assert "ONNXRUNTIME_COMMIT=da9b5e364c465de65c49d91e696cd6485270757f" in dockerfile
    assert dockerfile.count("ONNXRUNTIME_VERSION=1.28.0") == 2
    assert dockerfile.count("ONNXRUNTIME_COMMIT=da9b5e364c465de65c49d91e696cd6485270757f") == 2
    assert "ARG ONNXRUNTIME_VERSION" not in dockerfile
    assert "ARG ONNXRUNTIME_COMMIT" not in dockerfile
    assert "--mount=type=cache" not in dockerfile
    assert '"CMAKE_CUDA_ARCHITECTURES=121-real;121-virtual"' in dockerfile
    assert "--disable_contrib_ops" not in dockerfile
    assert "numpy==2.1.1" in dockerfile
    assert "packaging==24.2" in dockerfile
    assert "--use_preinstalled_eigen" not in dockerfile
    assert "cp /opt/onnxruntime/build/Linux/Release/dist/*.whl /opt/artifacts/" in dockerfile
    assert "COPY --from=builder /opt/artifacts/*.whl /wheel/" in dockerfile


def test_engine_gpu_build_requires_verified_wheel_and_model_hashes() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ONNXRUNTIME_GPU_WHEEL_SHA256" in dockerfile
    assert 'wheel_name="$(basename' in dockerfile
    assert 'ONNXRUNTIME_GPU_WHEEL_URL must end in .whl' in dockerfile
    assert "from urllib.parse import urlsplit" in dockerfile
    assert "u.scheme == 'https'" in dockerfile
    assert "not u.username and not u.password and not u.query and not u.fragment" in dockerfile
    assert "--proto '=https' --proto-redir '=https'" in dockerfile
    assert "CUDA_INDEX must be HTTPS without credentials" in dockerfile
    assert '--index-url "${CUDA_INDEX}"' in dockerfile
    assert "not c.isspace()" in dockerfile
    assert "re.fullmatch(r'[0-9a-fA-F]{64}'" in dockerfile
    assert "hashlib.file_digest" in dockerfile
    assert "sha256sum --check" not in dockerfile
    assert "COPY . ." not in dockerfile
    assert "COPY src/ ./src/" in dockerfile
    assert "RUN printf '[]\\n' > /app/config/cameras.json" in dockerfile
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert dockerignore[0] == "**"
    assert "!config/*.yaml" not in dockerignore
    assert "!config/cameras.json" not in dockerignore
    assert "!scripts/qualify_onnxruntime_gpu.py" in dockerignore
    assert "**/.env.*" in dockerignore

    gpu_compose = (ROOT / "docker-compose.gpu.yml").read_text(encoding="utf-8")
    assert "ONNXRUNTIME_GPU_WHEEL_URL:" in gpu_compose
    assert "ONNXRUNTIME_GPU_WHEEL_SHA256:" in gpu_compose

    frontend_dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY . ." not in frontend_dockerfile
    assert "COPY src/ ./src/" in frontend_dockerfile
    assert "ARG NODE_BASE" not in frontend_dockerfile
    assert frontend_dockerfile.count("FROM node:22-alpine@sha256:") == 3
    frontend_dockerignore = (ROOT / "frontend" / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert frontend_dockerignore[0] == "**"
    assert "!e2e/" not in frontend_dockerignore
    assert "**/.env.*" in frontend_dockerignore
    assert "GENERAL_MODEL_SHA256" in dockerfile
    assert "PPE_MODEL_SHA256" in dockerfile
    assert "FIRE_SMOKE_MODEL_SHA256" in dockerfile
    assert "ARG BASE_IMAGE" not in dockerfile
    assert "ARG GENERAL_MODEL_SHA256" not in dockerfile
    assert "ARG PPE_MODEL_SHA256" not in dockerfile
    assert "ARG FIRE_SMOKE_MODEL_SHA256" not in dockerfile
    assert "PPE_MODEL_SHA256=1ba914b176fa3072a2a2b019404535a2995abe6fe473041975484b543f1078f1" in dockerfile
    assert "FIRE_SMOKE_MODEL_SHA256=a639beb65e3a1f2a5e476852d2aba278070ab88422b33fa79eeed6149f816ab2" in dockerfile


def test_gpu_qualification_requires_three_cuda_only_model_sessions() -> None:
    script = (ROOT / "scripts/build_onnxruntime_wheel.sh").read_text(encoding="utf-8")
    qualification = (ROOT / "scripts/qualify_onnxruntime_gpu.py").read_text(encoding="utf-8")

    assert "yolo26.onnx yolo26_ppe.onnx yolo26_fire_smoke.onnx" in script
    assert "--platform linux/arm64" in script
    assert "options.enable_profiling = True" in qualification
    assert 'node_providers != {"CUDAExecutionProvider"}' in qualification
    assert "for _ in range(2)" in qualification
