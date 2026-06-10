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
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

logger = logging.getLogger(__name__)


ALLOWED_EXTENSIONS = {".onnx"}
MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024
UPLOAD_BASE_DIR = Path("./models/uploaded")
MODEL_PROFILES_PATH = Path("./config/model_profiles.yaml")
INFERENCE_TIMEOUT_SEC = 30.0


@dataclass
class UploadResult:
    """Result returned by the upload workflow."""

    success: bool
    model_id: str = ""
    profile_name: str = ""
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


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


def _validate_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
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

    return {
        "model_name": model_name,
        "model_type": "yolo_onnx",
        "input_size": input_size,
        "classes": classes,
        "postprocess_mode": str(meta.get("postprocess_mode") or "auto"),
        "class_profile": str(meta.get("class_profile") or "custom"),
        "conf_threshold": float(meta.get("conf_threshold", 0.25)),
        "iou_threshold": float(meta.get("iou_threshold", 0.45)),
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


def validate_onnx_inference(file_path: Path, timeout_sec: float = INFERENCE_TIMEOUT_SEC) -> tuple[bool, str]:
    """Run a bounded synthetic inference smoke test."""

    def _run() -> tuple[bool, str]:
        try:
            import numpy as np
            import onnxruntime as ort

            session = ort.InferenceSession(str(file_path), providers=["CPUExecutionProvider"])
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

    return _run_with_timeout(_run, timeout_sec)


def _load_yaml(path: Path) -> Dict[str, Any]:
    import yaml

    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: Dict[str, Any]) -> None:
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
    metadata: Dict[str, Any],
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
    metadata: Optional[Dict[str, Any]] = None,
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


def get_uploaded_models(upload_base_dir: Path = UPLOAD_BASE_DIR) -> list[Dict[str, Any]]:
    """Return uploaded model metadata list."""

    if not upload_base_dir.exists():
        return []
    models: list[Dict[str, Any]] = []
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
