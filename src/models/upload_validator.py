"""Safe user ONNX model upload validation and registration.

Phase 6 bounded scope:
- accept ONNX files only
- isolate uploads under ``models/uploaded/{model_id}/model.onnx``
- reject path traversal / executable-style extensions by never trusting filenames
- validate ONNX Runtime load + bounded smoke inference
- register uploaded profiles as ``enabled: false`` by default

Activation/deletion are exposed as explicit helper functions so the dashboard can
guard them behind admin authentication and audit logging.
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import shutil
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


ALLOWED_EXTENSIONS = {".onnx"}
MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024
UPLOAD_BASE_DIR = Path("./models/uploaded")
MODEL_PROFILES_PATH = Path("./config/model_profiles.yaml")
INFERENCE_TIMEOUT_SEC = 30.0
# 업로드 추론 검증 시 자식 프로세스 가상주소공간 상한(거대 텐서 할당 → 호스트 OOM 차단).
# 정상 YOLO ONNX(≤500MB 파일)는 충분, BLAS/arena VA 예약을 감안해 넉넉히 잡되 폭주는 막는 균형값.
INFERENCE_MEM_LIMIT_BYTES = 8 * 1024 * 1024 * 1024


@dataclass
class UploadResult:
    """Result returned by the upload workflow."""

    success: bool
    model_id: str = ""
    profile_name: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _safe_original_name(original_filename: str) -> str:
    """Return a metadata-safe basename; uploaded storage never uses it."""

    name = Path(str(original_filename or "")).name.strip()
    return name or "uploaded.onnx"


def validate_extension(filename: str) -> bool:
    """Return True only for .onnx files."""

    return Path(_safe_original_name(filename)).suffix.lower() in ALLOWED_EXTENSIONS


def sanitize_filename(original_name: str) -> str:
    """Return the fixed storage filename for validated ONNX uploads."""

    if not validate_extension(original_name):
        raise ValueError("허용되지 않은 확장자입니다. ONNX(.onnx)만 허용됩니다.")
    return "model.onnx"


def _validate_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize optional model metadata without trusting arbitrary values."""

    meta = dict(metadata or {})
    classes = meta.get("classes", [])
    if classes is None:
        classes = []
    if not isinstance(classes, list) or not all(isinstance(item, str) and item.strip() for item in classes):
        raise ValueError("classes metadata must be a list of non-empty strings")

    input_size = meta.get("input_size", [640, 640])
    if (
        not isinstance(input_size, list)
        or len(input_size) != 2
        or not all(isinstance(item, int) and item > 0 for item in input_size)
    ):
        raise ValueError("input_size metadata must be [height, width] positive integers")

    model_name = str(meta.get("model_name") or "uploaded_onnx_model").strip()[:80]
    if not model_name:
        model_name = "uploaded_onnx_model"

    # conf_threshold / iou_threshold — 비수치 입력에 대해 명시적으로 검증하고
    # 한국어 에러 메시지를 반환한다. float() 무조건 변환으로 인한 영문 raw ValueError
    # 전파를 방지한다.
    conf_raw = meta.get("conf_threshold", 0.25)
    if isinstance(conf_raw, bool) or not isinstance(conf_raw, (int, float, str)):
        raise ValueError("conf_threshold는 0과 1 사이의 숫자여야 합니다.")
    try:
        conf_threshold = float(conf_raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"conf_threshold 값을 숫자로 변환할 수 없습니다: '{conf_raw}'. 0과 1 사이의 숫자를 입력하세요."
        ) from exc
    if not (0.0 <= conf_threshold <= 1.0):
        raise ValueError(
            f"conf_threshold는 0과 1 사이여야 합니다. 입력값: {conf_threshold}"
        )

    iou_raw = meta.get("iou_threshold", 0.45)
    if isinstance(iou_raw, bool) or not isinstance(iou_raw, (int, float, str)):
        raise ValueError("iou_threshold는 0과 1 사이의 숫자여야 합니다.")
    try:
        iou_threshold = float(iou_raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"iou_threshold 값을 숫자로 변환할 수 없습니다: '{iou_raw}'. 0과 1 사이의 숫자를 입력하세요."
        ) from exc
    if not (0.0 <= iou_threshold <= 1.0):
        raise ValueError(
            f"iou_threshold는 0과 1 사이여야 합니다. 입력값: {iou_threshold}"
        )

    return {
        "model_name": model_name,
        "model_type": "yolo_onnx",
        "input_size": input_size,
        "classes": classes,
        "postprocess_mode": str(meta.get("postprocess_mode") or "auto"),
        "class_profile": str(meta.get("class_profile") or "custom"),
        "conf_threshold": conf_threshold,
        "iou_threshold": iou_threshold,
    }


def _run_with_timeout(fn: Callable[[], tuple[bool, str]], timeout_sec: float) -> tuple[bool, str]:
    """Run blocking ONNX calls with a bounded wait."""

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout_sec)
    except concurrent.futures.TimeoutError:
        future.cancel()
        return False, f"ONNX inference timeout after {timeout_sec:.1f}s"
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def validate_file_size_bytes(file_size: int, max_bytes: int = MAX_FILE_SIZE_BYTES) -> bool:
    """Validate in-memory upload size before writing to disk."""

    return 0 < file_size <= max_bytes


def validate_file_size(file_path: Path, max_bytes: int = MAX_FILE_SIZE_BYTES) -> bool:
    """Validate an existing file size."""

    try:
        return validate_file_size_bytes(file_path.stat().st_size, max_bytes)
    except OSError:
        return False


def validate_onnx_no_external_data(file_path: Path) -> tuple[bool, str]:
    """external-data(별도 파일에서 weight 로드)를 참조하는 ONNX를 거부한다.

    악성 모델이 external_data location='../../.env' 같은 경로로 모델 디렉토리 밖 파일을
    weight로 읽어 추론 출력으로 유출하는 arbitrary file read를 차단한다(onnxruntime<1.26은
    path-escape 가드가 없음). 업로드 모델은 self-contained ONNX만 허용한다. InferenceSession을
    만들기 **전에** 그래프만 파싱(load_external_data=False)해 검사하므로 외부 파일을 읽지 않는다.
    """
    try:
        import onnx

        model = onnx.load(str(file_path), load_external_data=False)
    except Exception as exc:
        return False, f"ONNX 파싱 실패: {exc}"

    external = onnx.TensorProto.EXTERNAL
    for init in model.graph.initializer:
        if init.data_location == external:
            return False, (
                "외부 데이터(external data)를 참조하는 ONNX는 보안상 허용되지 않습니다. "
                "self-contained ONNX(가중치 내장)만 업로드하세요."
            )
    return True, ""


def validate_onnx_load(file_path: Path) -> tuple[bool, str]:
    """Check that ONNX Runtime can load the model and sees inputs/outputs."""

    try:
        import onnxruntime as ort

        session = ort.InferenceSession(str(file_path), providers=["CPUExecutionProvider"])
        if not session.get_inputs() or not session.get_outputs():
            return False, "ONNX 모델에 입력/출력 노드가 없습니다."
        return True, ""
    except Exception as exc:  # pragma: no cover - exact provider errors vary
        return False, f"ONNX 로드 실패: {exc}"


def validate_onnx_shape_metadata(file_path: Path, input_size: Iterable[int]) -> tuple[bool, str]:
    """Validate static ONNX input H/W against declared metadata when available."""

    try:
        import onnxruntime as ort

        expected = list(input_size)
        session = ort.InferenceSession(str(file_path), providers=["CPUExecutionProvider"])
        inputs = session.get_inputs()
        if not inputs:
            return False, "ONNX 모델 입력 노드가 없습니다."
        shape = list(inputs[0].shape)
        if len(shape) < 4:
            return False, f"지원하지 않는 ONNX 입력 shape입니다: {shape}"
        static_hw = shape[-2:]
        if all(isinstance(dim, int) and dim > 0 for dim in static_hw) and static_hw != expected:
            return False, f"ONNX 입력 크기 {static_hw}가 metadata input_size {expected}와 다릅니다."
        return True, ""
    except Exception as exc:  # pragma: no cover - exact provider errors vary
        return False, f"ONNX shape 검사 실패: {exc}"


def _resolve_dim(dim: Any, index: int) -> int:
    """Resolve symbolic/dynamic ONNX dimensions to bounded smoke-test values."""

    if isinstance(dim, int) and dim > 0:
        return dim
    if index == 0:
        return 1
    if index in (2, 3):
        return 640
    return 3


def _np_dtype(onnx_type: str):
    import numpy as np

    type_text = str(onnx_type).lower()
    if "float16" in type_text:
        return np.float16
    if "float" in type_text:
        return np.float32
    if "int64" in type_text:
        return np.int64
    if "int" in type_text:
        return np.int32
    return np.float32


def _do_inference_smoke(file_path_str: str) -> tuple[bool, str]:
    """합성 zero 입력으로 1회 추론 smoke test (sandbox/스레드 폴백 공용 코어)."""
    try:
        import numpy as np
        import onnxruntime as ort

        session = ort.InferenceSession(file_path_str, providers=["CPUExecutionProvider"])
        feed = {}
        for inp in session.get_inputs():
            shape = [_resolve_dim(dim, idx) for idx, dim in enumerate(list(inp.shape))]
            if not shape:
                return False, f"ONNX 입력 shape가 비어 있습니다: {inp.name}"
            feed[inp.name] = np.zeros(shape, dtype=_np_dtype(inp.type))
        session.run(None, feed)
        return True, ""
    except Exception as exc:  # pragma: no cover - exact runtime errors vary
        return False, f"ONNX inference 실패: {exc}"


def _sandbox_inference_worker(file_path_str: str, mem_limit_bytes: int, cpu_limit_sec: int, queue) -> None:
    """자식 프로세스: rlimit(메모리/CPU) 설정 후 추론 smoke test → 결과를 queue로 반환.

    악성 ONNX가 거대 텐서 할당(OOM)·무한 연산(CPU)으로 호스트를 마비시키는 것을 막는다.
    스레드 타임아웃과 달리 자식 프로세스는 자원상한·hard-kill이 실제로 강제된다.
    """
    try:
        import resource

        # RLIMIT_AS: 가상주소공간 상한(거대 텐서 차단). spawn 자식이라 baseline VA가 작아 의미 있음.
        resource.setrlimit(resource.RLIMIT_AS, (mem_limit_bytes, mem_limit_bytes))
        # RLIMIT_CPU: CPU 초 상한(무한 루프 차단) — soft 도달 시 SIGXCPU, hard에서 SIGKILL.
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit_sec, cpu_limit_sec))
    except Exception:  # pragma: no cover - 일부 환경은 setrlimit 불허(상위 wall-clock이 최종 안전망)
        pass

    queue.put(_do_inference_smoke(file_path_str))


def _run_inference_sandboxed(
    file_path: Path, timeout_sec: float, mem_limit_bytes: int
) -> tuple[bool, str]:
    """추론 검증을 자원-제한 자식 프로세스에서 실행(POSIX). spawn 미지원이면 스레드로 폴백."""
    import multiprocessing as mp

    # fork는 부모의 거대한 onnxruntime VA를 물려받아 RLIMIT_AS가 즉시 깨질 수 있으므로,
    # baseline이 깨끗한 spawn으로 자식을 띄운다(상한이 실제로 의미를 가지게).
    try:
        ctx = mp.get_context("spawn")
    except ValueError:  # pragma: no cover - spawn 미지원 플랫폼
        return _run_with_timeout(lambda: _do_inference_smoke(str(file_path)), timeout_sec)

    queue = ctx.Queue()
    cpu_limit_sec = max(1, int(timeout_sec) + 5)
    proc = ctx.Process(
        target=_sandbox_inference_worker,
        args=(str(file_path), mem_limit_bytes, cpu_limit_sec, queue),
    )
    proc.start()
    proc.join(timeout_sec)

    if proc.is_alive():
        # wall-clock 초과 → hard-kill(스레드 타임아웃이 못 죽이던 네이티브 연산을 실제로 종료).
        proc.terminate()
        proc.join(5)
        if proc.is_alive():  # pragma: no cover - terminate 무시 시
            proc.kill()
            proc.join()
        return False, f"ONNX inference timeout after {timeout_sec:.1f}s"

    # 결과가 큐에 있으면(정상/추론오류) 그것을 신뢰. 없는데 비정상 종료면 자원상한 위반으로 본다.
    try:
        return queue.get_nowait()
    except Exception:
        if proc.exitcode and proc.exitcode != 0:
            return False, (
                f"ONNX inference가 자원 상한(메모리 {mem_limit_bytes // (1024 * 1024)}MB / "
                f"CPU {cpu_limit_sec}s)을 초과했거나 비정상 종료했습니다(exitcode={proc.exitcode})."
            )
        return False, "ONNX inference 결과를 가져오지 못했습니다."


def validate_onnx_inference(
    file_path: Path,
    timeout_sec: float = INFERENCE_TIMEOUT_SEC,
    mem_limit_bytes: int = INFERENCE_MEM_LIMIT_BYTES,
) -> tuple[bool, str]:
    """자원-제한 sandbox에서 합성 입력 1회 추론 smoke test를 수행한다.

    업로드 모델은 신뢰할 수 없다 — 악성 그래프가 거대 텐서/무한 연산으로 호스트를 DoS할 수 있어
    POSIX에서는 **별도 프로세스 + rlimit(메모리/CPU) + wall-clock 타임아웃**으로 격리한다.
    (스레드 타임아웃은 onnxruntime 네이티브 호출을 실제로 죽이지 못하고 메모리도 못 묶는다.)
    비-POSIX 환경은 자원상한 API가 없어 스레드 타임아웃으로 폴백한다(완전 격리는 아님).
    """
    if os.name == "posix":
        return _run_inference_sandboxed(file_path, timeout_sec, mem_limit_bytes)
    return _run_with_timeout(lambda: _do_inference_smoke(str(file_path)), timeout_sec)


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def _model_file_for(upload_base_dir: Path, model_id: str) -> Path:
    return upload_base_dir / model_id / "model.onnx"


def _metadata_file_for(upload_base_dir: Path, model_id: str) -> Path:
    return upload_base_dir / model_id / "metadata.yaml"


def _register_disabled_profile(
    *,
    model_id: str,
    model_path: Path,
    metadata: dict[str, Any],
    model_profiles_path: Path,
) -> str:
    """Register uploaded model in model_profiles.yaml as disabled-by-default."""

    profile_name = f"uploaded_{model_id}"
    config = _load_yaml(model_profiles_path)
    models = config.setdefault("models", {})
    models[profile_name] = {
        "enabled": False,
        "type": "yolo_onnx",
        "source": "uploaded",
        "model_path": str(model_path),
        "class_profile": metadata["class_profile"],
        "classes": metadata["classes"],
        "input_size": metadata["input_size"],
        "conf_threshold": metadata["conf_threshold"],
        "iou_threshold": metadata["iou_threshold"],
        "postprocess_mode": metadata["postprocess_mode"],
        "device": "cuda",
        "fp16": True,
        "description": f"Uploaded model: {metadata['model_name']} (disabled by default)",
    }
    _write_yaml(model_profiles_path, config)
    return profile_name


def upload_model(
    file_bytes: bytes,
    original_filename: str,
    metadata: dict[str, Any] | None = None,
    *,
    max_file_size: int = MAX_FILE_SIZE_BYTES,
    upload_base_dir: Path = UPLOAD_BASE_DIR,
    model_profiles_path: Path = MODEL_PROFILES_PATH,
    inference_timeout_sec: float = INFERENCE_TIMEOUT_SEC,
) -> UploadResult:
    """Validate, isolate, smoke-test, and register an uploaded ONNX model."""

    safe_original = _safe_original_name(original_filename)
    if not validate_extension(safe_original):
        return UploadResult(success=False, error="허용되지 않은 파일 형식입니다. ONNX(.onnx)만 업로드할 수 있습니다.")
    if not validate_file_size_bytes(len(file_bytes), max_file_size):
        return UploadResult(success=False, error=f"파일 크기가 허용 범위를 벗어났습니다. 최대 {max_file_size // (1024 * 1024)}MB")

    try:
        normalized = _validate_metadata(metadata)
    except ValueError as exc:
        return UploadResult(success=False, error=str(exc))

    model_id = uuid.uuid4().hex
    model_dir = upload_base_dir / model_id
    tmp_dir = upload_base_dir / "tmp"
    tmp_file = tmp_dir / f"{model_id}.upload"
    target_file = _model_file_for(upload_base_dir, model_id)

    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        with open(tmp_file, "wb") as f:
            f.write(file_bytes)

        # 보안: InferenceSession 생성(외부 파일 읽기 발생) 전에 external-data 참조부터 차단한다.
        ext_ok, ext_error = validate_onnx_no_external_data(tmp_file)
        if not ext_ok:
            return UploadResult(success=False, error=ext_error)

        load_ok, load_error = validate_onnx_load(tmp_file)
        if not load_ok:
            return UploadResult(success=False, error=load_error)

        shape_ok, shape_error = validate_onnx_shape_metadata(tmp_file, normalized["input_size"])
        if not shape_ok:
            return UploadResult(success=False, error=shape_error)

        smoke_ok, smoke_error = validate_onnx_inference(tmp_file, timeout_sec=inference_timeout_sec)
        if not smoke_ok:
            return UploadResult(success=False, error=smoke_error)

        model_dir.mkdir(parents=True, exist_ok=False)
        shutil.move(str(tmp_file), str(target_file))

        profile_name = _register_disabled_profile(
            model_id=model_id,
            model_path=target_file,
            metadata=normalized,
            model_profiles_path=model_profiles_path,
        )

        upload_metadata = {
            "model_id": model_id,
            "profile_name": profile_name,
            "original_filename": safe_original,
            "model_path": str(target_file),
            "enabled": False,
            **normalized,
        }
        _write_yaml(_metadata_file_for(upload_base_dir, model_id), upload_metadata)
        logger.info("Uploaded ONNX model registered disabled: %s", profile_name)
        return UploadResult(success=True, model_id=model_id, profile_name=profile_name, metadata=upload_metadata)
    except Exception as exc:
        shutil.rmtree(model_dir, ignore_errors=True)
        logger.error("Model upload failed: %s", exc)
        return UploadResult(success=False, error=f"업로드 실패: {exc}")
    finally:
        if tmp_file.exists():
            tmp_file.unlink(missing_ok=True)


def get_uploaded_models(upload_base_dir: Path = UPLOAD_BASE_DIR) -> list[dict[str, Any]]:
    """Return uploaded model metadata list."""

    if not upload_base_dir.exists():
        return []
    models: list[dict[str, Any]] = []
    for model_dir in sorted(upload_base_dir.iterdir()):
        if not model_dir.is_dir() or model_dir.name == "tmp":
            continue
        metadata = _load_yaml(_metadata_file_for(upload_base_dir, model_dir.name))
        if metadata:
            models.append(metadata)
    return models


def _set_profile_enabled(
    model_id: str,
    enabled: bool,
    model_profiles_path: Path,
    upload_base_dir: Path,
) -> tuple[bool, str]:
    profile_name = f"uploaded_{model_id}"
    config = _load_yaml(model_profiles_path)
    models = config.get("models", {})
    profile = models.get(profile_name)
    if not isinstance(profile, dict) or profile.get("source") != "uploaded":
        return False, f"업로드 모델 프로필을 찾을 수 없습니다: {profile_name}"
    profile["enabled"] = enabled
    _write_yaml(model_profiles_path, config)
    metadata_path = _metadata_file_for(upload_base_dir, model_id)
    metadata = _load_yaml(metadata_path)
    if metadata:
        metadata["enabled"] = enabled
        _write_yaml(metadata_path, metadata)
    return True, ""


def activate_uploaded_model(
    model_id: str,
    model_profiles_path: Path = MODEL_PROFILES_PATH,
    upload_base_dir: Path = UPLOAD_BASE_DIR,
) -> tuple[bool, str]:
    """Enable a previously validated uploaded model profile."""

    return _set_profile_enabled(model_id, True, model_profiles_path, upload_base_dir)


def deactivate_uploaded_model(
    model_id: str,
    model_profiles_path: Path = MODEL_PROFILES_PATH,
    upload_base_dir: Path = UPLOAD_BASE_DIR,
) -> tuple[bool, str]:
    """Disable an uploaded model profile."""

    return _set_profile_enabled(model_id, False, model_profiles_path, upload_base_dir)


def delete_uploaded_model(
    model_id: str,
    *,
    upload_base_dir: Path = UPLOAD_BASE_DIR,
    model_profiles_path: Path = MODEL_PROFILES_PATH,
) -> tuple[bool, str]:
    """Delete uploaded model files and remove its uploaded profile."""

    model_dir = upload_base_dir / model_id
    profile_name = f"uploaded_{model_id}"
    try:
        config = _load_yaml(model_profiles_path)
        models = config.get("models", {})
        profile = models.get(profile_name)
        if isinstance(profile, dict) and profile.get("source") == "uploaded":
            models.pop(profile_name, None)
            _write_yaml(model_profiles_path, config)
        if model_dir.exists():
            shutil.rmtree(model_dir)
        return True, ""
    except Exception as exc:
        return False, f"모델 삭제 실패: {exc}"
