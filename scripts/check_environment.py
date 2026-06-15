"""
KSI-CCTV 환경 점검 스크립트 (Phase 4)

설치 및 실행 전에 시스템 환경을 점검합니다.
"""
import shutil
import sys
from pathlib import Path  # noqa: I001


# 색상 정의
class Color:
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    RED = "\033[0;31m"
    NC = "\033[0m"


def ok(msg):
    print(f"{Color.GREEN}[OK]{Color.NC} {msg}")


def warn(msg):
    print(f"{Color.YELLOW}[WARN]{Color.NC} {msg}")


def err(msg):
    print(f"{Color.RED}[ERROR]{Color.NC} {msg}")


def check_python():
    """Python 버전 확인"""
    ver = sys.version_info
    if ver >= (3, 10):
        ok(f"Python {ver.major}.{ver.minor}.{ver.micro}")
    else:
        err(f"Python 3.10+ 필요 (현재: {ver.major}.{ver.minor}.{ver.micro})")
        return False
    return True


def check_venv():
    """가상환경 확인"""
    venv = Path(".venv")
    if venv.exists() and (venv / "bin" / "python").exists():
        ok("가상환경 (.venv) 존재")
        return True
    else:
        warn("가상환경 (.venv) 없음")
        return False


def check_dependencies():
    """필수 의존성 확인"""
    required = [
        "numpy", "cv2", "onnxruntime", "yaml", "PIL",
        "streamlit", "requests", "ultralytics"
    ]
    missing = []
    for module in required:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        err(f"누락된 의존성: {', '.join(missing)}")
        return False
    ok("모든 핵심 의존성 설치됨")
    return True


def check_models():
    """모델 파일 확인"""
    models_dir = Path("models")
    if not models_dir.exists():
        err("models/ 디렉토리 없음")
        return False
    onnx_files = list(models_dir.glob("*.onnx"))
    if not onnx_files:
        warn("ONNX 모델 파일 없음")
        return False
    ok(f"ONNX 모델 {len(onnx_files)}개 발견: {[f.name for f in onnx_files]}")
    return True


def check_config():
    """설정 파일 확인"""
    required_files = [
        "config/default.yaml",
        "config/cameras.json",
        "config/model_profiles.yaml",
        "config/event_policies.yaml",
    ]
    for f in required_files:
        if not Path(f).exists():
            err(f"설정 파일 없음: {f}")
            return False
    ok("모든 설정 파일 존재")
    return True


def check_env():
    """환경변수 확인"""
    env_file = Path(".env")
    if not env_file.exists():
        warn(".env 파일 없음 (.env.example을 복사하여 생성하세요)")
        return False
    ok(".env 파일 존재")
    return True


def check_directories():
    """필수 디렉토리 확인/생성"""
    required_dirs = ["logs", "data", "data/captures", "models/uploaded"]
    for d in required_dirs:
        p = Path(d)
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            warn(f"디렉토리 생성: {d}")
        else:
            ok(f"디렉토리 존재: {d}")
    return True


def check_disk_space():
    """디스크 공간 확인"""
    total, used, free = shutil.disk_usage("/")
    free_gb = free / (1024 ** 3)
    if free_gb < 5:
        err(f"디스크 여유 공간 부족: {free_gb:.1f}GB (5GB 이상 필요)")
        return False
    ok(f"디스크 여유 공간: {free_gb:.1f}GB")
    return True


def check_ollama():
    """Ollama 연결 확인 (선택사항)"""
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            ok(f"Ollama 연결됨 (모델: {len(models)}개)")
            return True
        else:
            warn("Ollama 연결 실패 (VLM 기능 비활성화됨)")
            return True  # VLM은 선택사항
    except Exception:
        warn("Ollama 미연결 (VLM 기능 비활성화됨)")
        return True  # VLM은 선택사항


def check_gpu():
    """GPU 확인 및 ONNX Runtime CUDA provider 점검 (DGX Spark cu130/ARM64 대응)

    pynvml로 GPU 존재만 확인하는 것을 넘어,
    onnxruntime의 실제 CUDAExecutionProvider 가용 여부를 함께 점검한다.
    CLAUDE.md 운영 주의: cu130 휠·ARM64 전용 설치가 필요하며 batch=-1 금지.
    """
    # 1. pynvml로 GPU 하드웨어 존재 확인
    gpu_name = None
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode()
        gpu_name = name
        ok(f"GPU 감지됨: {name}")
    except Exception:
        warn("GPU 미감지 (CPU 모드로 실행됨)")

    # 2. onnxruntime CUDAExecutionProvider 실제 가용 여부 확인
    # DGX Spark(ARM64/sm_121/cu130): GPU가 있어도 잘못된 휠이면 CUDAProvider가 없음
    try:
        import onnxruntime as ort
        available_providers = ort.get_available_providers()
        has_cuda = "CUDAExecutionProvider" in available_providers
        if has_cuda:
            ok(f"ONNX Runtime CUDAExecutionProvider 사용 가능 (providers: {available_providers})")
        else:
            if gpu_name:
                # GPU 하드웨어는 있지만 ORT CUDA provider가 없음 = cu130/ARM64 휠 불일치 가능성
                warn(
                    f"GPU({gpu_name})가 있지만 onnxruntime CUDAExecutionProvider를 찾을 수 없습니다. "
                    "DGX Spark ARM64에서는 cu130/aarch64 전용 onnxruntime-gpu 휠이 필요합니다. "
                    "CPU fallback으로 실행됩니다."
                )
            else:
                warn(
                    "onnxruntime CUDAExecutionProvider 미사용 — CPU fallback으로 실행됩니다. "
                    f"(가용 providers: {available_providers})"
                )
    except ImportError:
        # onnxruntime 미설치는 check_dependencies()에서 이미 오류로 잡힌다.
        # GPU 점검 자체는 선택사항이므로 warn으로 처리하고 계속 진행.
        warn("onnxruntime 미설치 — GPU provider 점검 불가. check_dependencies 결과를 확인하세요.")
    except Exception as e:
        warn(f"onnxruntime provider 점검 실패: {e}")

    # 3. DGX Spark 운영 제약 안내
    # batch=-1 금지: OOM 유발. check_environment 점검 범위에서 안내 제공.
    warn(
        "[DGX Spark 운영 주의] batch=-1 절대 금지 (통합메모리 OOM 유발). "
        "학습/추론 시 고정 batch 값을 명시하세요. "
        "PyTorch는 cu130 휠(--index-url https://download.pytorch.org/whl/cu126 이상)로 설치."
    )

    return True  # GPU/CUDA는 선택사항 (CPU fallback 지원)


def main():
    print("=" * 60)
    print("KSI-CCTV 환경 점검")
    print("=" * 60)

    checks = [
        ("Python 버전", check_python),
        ("가상환경", check_venv),
        ("의존성", check_dependencies),
        ("모델 파일", check_models),
        ("설정 파일", check_config),
        ("환경변수", check_env),
        ("디렉토리", check_directories),
        ("디스크 공간", check_disk_space),
        ("Ollama (VLM)", check_ollama),
        ("GPU / ONNX Runtime provider", check_gpu),
    ]

    all_ok = True
    for name, check_fn in checks:
        print(f"\n[{name}]")
        if not check_fn():
            all_ok = False

    print("\n" + "=" * 60)
    if all_ok:
        print(f"{Color.GREEN}모든 점검 통과! KSI-CCTV를 실행할 수 있습니다.{Color.NC}")
        print("\n실행: ./scripts/run_dashboard.sh")
        return 0
    else:
        print(f"{Color.YELLOW}일부 점검 실패. 위의 경고/오류를 확인하세요.{Color.NC}")
        print("\n설치: ./scripts/install.sh")
        return 1


if __name__ == "__main__":
    sys.exit(main())
