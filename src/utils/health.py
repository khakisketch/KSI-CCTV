"""
헬스체크 모듈

시스템 상태 점검 및 HTTP 엔드포인트 제공.

신규(Phase 4):
- check_cameras: CameraManager.get_status() 기반 카메라별 staleness/연결 상태 점검
- check_vlm: OllamaProvider.get_metrics() 기반 VLM 실패율·지연 점검
- check_model_registry: MultiModelRegistry.get_model_metrics() 기반 모델 연속 실패 점검
- check_gpu: GPU VRAM·온도 임계값 점검
- check_log_dirs: 로그/캡처 디렉터리 크기 임계값 점검
- register_pipeline_health_checks: SafetyPipeline에서 위 체크를 일괄 등록하는 헬퍼
- CLI 진입점: python -m src.utils.health [--once | --serve]
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from src.utils.system_metrics import SystemMetrics, collect_metrics, get_dir_size, get_gpu_info

if TYPE_CHECKING:
    pass  # 런타임 순환 방지 — 타입 힌트는 문자열 forward-ref 사용

logger = logging.getLogger(__name__)


@dataclass
class ComponentHealth:
    """컴포넌트 헬스 상태"""
    name: str
    healthy: bool
    message: str = ""
    last_check: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class HealthReport:
    """전체 헬스체크 리포트"""
    overall_healthy: bool
    components: list[ComponentHealth]
    system_metrics: SystemMetrics
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "status": "healthy" if self.overall_healthy else "unhealthy",
            "timestamp": self.timestamp,
            "system": self.system_metrics.to_dict(),
            "components": [
                {
                    "name": c.name,
                    "healthy": c.healthy,
                    "message": c.message,
                    "last_check": c.last_check,
                    "metadata": c.metadata,
                }
                for c in self.components
            ],
        }


class HealthChecker:
    """헬스체크 관리자"""

    def __init__(self):
        self._checks: dict[str, Callable[[], Any]] = {}
        self._lock = threading.Lock()

    def register(self, name: str, check_fn: Callable[[], Any]):
        """헬스체크 함수 등록"""
        with self._lock:
            self._checks[name] = check_fn

    def unregister(self, name: str):
        """헬스체크 함수 제거"""
        with self._lock:
            self._checks.pop(name, None)

    def run_checks(self) -> HealthReport:
        """등록된 모든 헬스체크 실행"""
        components = []
        overall_healthy = True

        with self._lock:
            checks = dict(self._checks)

        for name, check_fn in checks.items():
            try:
                result = check_fn()
                if isinstance(result, ComponentHealth):
                    components.append(result)
                    if not result.healthy:
                        overall_healthy = False
                elif isinstance(result, tuple) and len(result) == 2:
                    healthy, message = result
                    components.append(ComponentHealth(name, healthy, str(message)))
                    if not healthy:
                        overall_healthy = False
                else:
                    components.append(ComponentHealth(name, bool(result), ""))
                    if not bool(result):
                        overall_healthy = False
            except Exception as e:
                components.append(ComponentHealth(name, False, f"Check failed: {e}"))
                overall_healthy = False

        return HealthReport(
            overall_healthy=overall_healthy,
            components=components,
            system_metrics=collect_metrics(),
        )


# ─── 기본 헬스체크 함수들 ────────────────────────────────────────────────────

def check_disk_space(threshold_percent: float = 90.0) -> ComponentHealth:
    """디스크 공간 점검"""
    metrics = collect_metrics()
    healthy = metrics.disk_percent < threshold_percent
    return ComponentHealth(
        name="disk_space",
        healthy=healthy,
        message=f"디스크 사용률: {metrics.disk_percent:.1f}%",
        metadata={"percent": metrics.disk_percent, "threshold": threshold_percent},
    )


def check_memory_usage(threshold_percent: float = 90.0) -> ComponentHealth:
    """메모리 사용량 점검"""
    metrics = collect_metrics()
    healthy = metrics.memory_percent < threshold_percent
    return ComponentHealth(
        name="memory_usage",
        healthy=healthy,
        message=f"메모리 사용률: {metrics.memory_percent:.1f}%",
        metadata={"percent": metrics.memory_percent, "threshold": threshold_percent},
    )


def check_ollama_available(base_url: str = "http://localhost:11434") -> ComponentHealth:
    """Ollama 서비스 연결 점검 (ping 전용)"""
    try:
        import requests
        resp = requests.get(f"{base_url}/api/tags", timeout=3.0)
        healthy = resp.status_code == 200
        return ComponentHealth(
            name="ollama",
            healthy=healthy,
            message=f"Ollama {base_url}: {'연결됨' if healthy else '연결 실패'}",
            metadata={"url": base_url, "status_code": resp.status_code if healthy else None},
        )
    except Exception as e:
        return ComponentHealth(
            name="ollama",
            healthy=False,
            message=f"Ollama 연결 실패: {e}",
            metadata={"url": base_url},
        )


# ─── 카메라 헬스체크 ─────────────────────────────────────────────────────────

def check_cameras(
    manager_status: dict[str, dict],
    stale_sec: float = 10.0,
    max_consecutive_failures: int = 3,
) -> ComponentHealth:
    """카메라 신선도 및 연결 상태 점검.

    manager_status: CameraManager.get_status() 반환값
      {camera_id: {connected, is_stale, consecutive_failures, seconds_since_last_frame, ...}}

    판정 기준:
    - is_stale=True (마지막 프레임 경과 > stale_sec) 이면 해당 카메라 unhealthy
    - connected=False 이면 해당 카메라 unhealthy
    - consecutive_failures >= max_consecutive_failures 이면 unhealthy
    """
    if not manager_status:
        return ComponentHealth(
            name="cameras",
            healthy=True,
            message="등록된 카메라 없음",
            metadata={"camera_count": 0},
        )

    unhealthy_cameras: list[str] = []
    camera_details: dict[str, dict] = {}

    for cam_id, status in manager_status.items():
        issues: list[str] = []
        elapsed = status.get("seconds_since_last_frame")
        # is_stale(카메라가 자신의 stale_threshold로 계산)를 1차로 신뢰하고, 필드가 없으면(미제공)
        # stale_sec를 fallback 임계로 직접 판정한다(이전엔 stale_sec가 미사용 dead 파라미터였음).
        is_stale = status.get("is_stale")
        if is_stale is None:
            is_stale = elapsed is not None and elapsed > stale_sec
        if is_stale:
            elapsed_str = f"{elapsed:.1f}초" if elapsed is not None else "알 수 없음"
            issues.append(f"프레임 지연 {elapsed_str}")
        elif not status.get("connected", True):
            issues.append("연결 끊김")

        consec = status.get("consecutive_failures", 0)
        if consec >= max_consecutive_failures:
            issues.append(f"연속 연결 실패 {consec}회")

        camera_details[cam_id] = {
            "connected": status.get("connected", False),
            "is_stale": status.get("is_stale", False),
            "consecutive_failures": consec,
            "seconds_since_last_frame": status.get("seconds_since_last_frame"),
            "issues": issues,
        }
        if issues:
            unhealthy_cameras.append(cam_id)

    healthy = len(unhealthy_cameras) == 0
    if healthy:
        message = f"카메라 {len(manager_status)}대 정상"
    else:
        message = f"카메라 이상: {', '.join(unhealthy_cameras)}"

    return ComponentHealth(
        name="cameras",
        healthy=healthy,
        message=message,
        metadata={
            "camera_count": len(manager_status),
            "unhealthy_count": len(unhealthy_cameras),
            "cameras": camera_details,
        },
    )


# ─── VLM 헬스체크 ────────────────────────────────────────────────────────────

def check_vlm(
    provider_metrics: dict[str, Any],
    max_failure_rate: float = 0.5,
    max_latency_ms: float = 30_000.0,
) -> ComponentHealth:
    """VLM 실패율 및 지연 기반 헬스체크.

    provider_metrics: OllamaProvider.get_metrics() 반환값
      {total_calls, success_count, failure_count, avg_latency_ms,
       max_latency_ms, last_latency_ms, success_rate, last_error, ...}

    판정 기준:
    - total_calls >= 5 이고 success_rate < (1 - max_failure_rate) 이면 unhealthy
    - last_latency_ms > max_latency_ms 이면 unhealthy
    """
    total = provider_metrics.get("total_calls", 0)
    failure_count = provider_metrics.get("failure_count", 0)
    success_rate = provider_metrics.get("success_rate", 1.0)
    last_latency = provider_metrics.get("last_latency_ms", 0.0)
    avg_latency = provider_metrics.get("avg_latency_ms", 0.0)
    last_error = provider_metrics.get("last_error", "")

    issues: list[str] = []

    # 충분한 샘플이 쌓인 뒤에만 실패율 평가
    if total >= 5:
        failure_rate = 1.0 - success_rate
        if failure_rate >= max_failure_rate:
            issues.append(
                f"VLM 실패율 {failure_rate*100:.0f}% "
                f"(임계 {max_failure_rate*100:.0f}%, {failure_count}/{total}건)"
            )

    # 마지막 요청 지연 초과
    if last_latency > max_latency_ms:
        issues.append(
            f"마지막 VLM 지연 {last_latency/1000:.1f}초 "
            f"(임계 {max_latency_ms/1000:.0f}초)"
        )

    healthy = len(issues) == 0
    if healthy:
        if total == 0:
            message = "VLM 미사용"
        else:
            message = (
                f"VLM 정상 ({total}건, 성공률 {success_rate*100:.0f}%, "
                f"평균 {avg_latency:.0f}ms)"
            )
    else:
        message = "; ".join(issues)
        if last_error:
            message += f" | 최근 오류: {last_error[:100]}"

    return ComponentHealth(
        name="vlm",
        healthy=healthy,
        message=message,
        metadata={
            "total_calls": total,
            "failure_count": failure_count,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency,
            "last_latency_ms": last_latency,
        },
    )


# ─── 모델 레지스트리 헬스체크 ─────────────────────────────────────────────────

def check_model_registry(
    model_metrics: dict[str, dict[str, Any]],
    max_consecutive_errors: int = 3,
    max_error_rate: float = 0.5,
) -> ComponentHealth:
    """MultiModelRegistry.get_model_metrics() 기반 모델 헬스 점검.

    model_metrics: {model_name: {inference_count, success_count, error_count,
                                  consecutive_errors, avg_latency_ms, ...}}

    판정 기준:
    - consecutive_errors >= max_consecutive_errors 이면 해당 모델 unhealthy
    - inference_count >= 10 이고 error_rate >= max_error_rate 이면 unhealthy
    """
    if not model_metrics:
        return ComponentHealth(
            name="model_registry",
            healthy=True,
            message="등록된 모델 없음",
            metadata={"model_count": 0},
        )

    unhealthy_models: list[str] = []
    model_details: dict[str, dict] = {}

    for model_name, m in model_metrics.items():
        issues: list[str] = []
        consec_err = m.get("consecutive_errors", 0)
        inference_count = m.get("inference_count", 0)
        error_count = m.get("error_count", 0)

        if consec_err >= max_consecutive_errors:
            issues.append(f"연속 실패 {consec_err}회")

        if inference_count >= 10:
            error_rate = error_count / inference_count
            if error_rate >= max_error_rate:
                issues.append(
                    f"오류율 {error_rate*100:.0f}% "
                    f"({error_count}/{inference_count}건)"
                )

        model_details[model_name] = {
            "inference_count": inference_count,
            "error_count": error_count,
            "consecutive_errors": consec_err,
            "avg_latency_ms": m.get("avg_latency_ms", -1),
            "issues": issues,
        }
        if issues:
            unhealthy_models.append(model_name)

    healthy = len(unhealthy_models) == 0
    if healthy:
        message = f"모델 {len(model_metrics)}개 정상"
    else:
        message = f"모델 이상: {', '.join(unhealthy_models)}"

    return ComponentHealth(
        name="model_registry",
        healthy=healthy,
        message=message,
        metadata={
            "model_count": len(model_metrics),
            "unhealthy_count": len(unhealthy_models),
            "models": model_details,
        },
    )


# ─── GPU 헬스체크 ─────────────────────────────────────────────────────────────

def check_gpu(
    vram_threshold_percent: float = 95.0,
    temp_threshold_celsius: float = 85.0,
) -> ComponentHealth:
    """GPU VRAM 사용률 및 온도 점검.

    DGX Spark 통합메모리 환경에서 VRAM 포화는 OOM 직결 → 조기 경보 필요.
    GPU가 없는 환경(pynvml 실패)에서는 healthy=True, 메시지로 안내.
    """
    gpu = get_gpu_info()

    # GPU 없음 — pynvml 실패 시 모두 None
    if gpu["percent"] is None and gpu["memory_percent"] is None and gpu["temperature"] is None:
        return ComponentHealth(
            name="gpu",
            healthy=True,
            message="GPU 정보 없음 (GPU 미탑재 또는 pynvml 초기화 실패)",
            metadata={"available": False},
        )

    issues: list[str] = []
    mem_pct = gpu.get("memory_percent")
    temp = gpu.get("temperature")

    if mem_pct is not None and mem_pct >= vram_threshold_percent:
        issues.append(
            f"VRAM {mem_pct:.1f}% (임계 {vram_threshold_percent:.0f}%)"
        )
    if temp is not None and temp >= temp_threshold_celsius:
        issues.append(
            f"GPU 온도 {temp:.0f}°C (임계 {temp_threshold_celsius:.0f}°C)"
        )

    healthy = len(issues) == 0
    message = (
        "GPU 정상"
        if healthy
        else f"GPU 경보: {'; '.join(issues)}"
    )

    return ComponentHealth(
        name="gpu",
        healthy=healthy,
        message=message,
        metadata={
            "available": True,
            "gpu_percent": gpu.get("percent"),
            "memory_percent": mem_pct,
            "temperature": temp,
            "vram_threshold": vram_threshold_percent,
            "temp_threshold": temp_threshold_celsius,
        },
    )


# ─── 로그/캡처 디렉터리 크기 점검 ──────────────────────────────────────────────

def check_log_dirs(
    log_dir: str = "./logs",
    capture_dir: str = "./data/captures",
    max_log_gb: float = 5.0,
    max_capture_gb: float = 20.0,
) -> ComponentHealth:
    """로그 및 캡처 디렉터리 누적 크기 점검.

    alert_manager가 ./logs(RotatingFileHandler)와 ./data/captures에 파일을 쌓으므로
    retention 미설정 시 디스크 폭주를 조기 감지한다.
    """
    _GB = 1024 ** 3
    log_bytes = get_dir_size(log_dir)
    capture_bytes = get_dir_size(capture_dir)

    log_gb = log_bytes / _GB
    capture_gb = capture_bytes / _GB

    issues: list[str] = []
    if log_gb >= max_log_gb:
        issues.append(
            f"로그 디렉터리 {log_gb:.2f}GB (임계 {max_log_gb:.1f}GB): {log_dir}"
        )
    if capture_gb >= max_capture_gb:
        issues.append(
            f"캡처 디렉터리 {capture_gb:.2f}GB (임계 {max_capture_gb:.1f}GB): {capture_dir}"
        )

    healthy = len(issues) == 0
    message = (
        f"로그 {log_gb*1024:.0f}MB / 캡처 {capture_gb*1024:.0f}MB"
        if healthy
        else "; ".join(issues)
    )

    return ComponentHealth(
        name="log_dirs",
        healthy=healthy,
        message=message,
        metadata={
            "log_dir": log_dir,
            "log_size_gb": round(log_gb, 3),
            "log_threshold_gb": max_log_gb,
            "capture_dir": capture_dir,
            "capture_size_gb": round(capture_gb, 3),
            "capture_threshold_gb": max_capture_gb,
        },
    )


# ─── 파이프라인 헬스체크 일괄 등록 헬퍼 ────────────────────────────────────────

def register_pipeline_health_checks(
    checker: HealthChecker,
    pipeline: Any,
    stale_sec: float = 10.0,
    vlm_max_failure_rate: float = 0.5,
    vlm_max_latency_ms: float = 30_000.0,
    model_max_consecutive_errors: int = 3,
    vram_threshold_percent: float = 95.0,
    temp_threshold_celsius: float = 85.0,
    log_dir: str = "./logs",
    capture_dir: str = "./data/captures",
    max_log_gb: float = 5.0,
    max_capture_gb: float = 20.0,
) -> None:
    """SafetyPipeline에서 헬스체크를 일괄 등록하는 헬퍼.

    pipeline: SafetyPipeline 인스턴스 (get_status() 가 있어야 함).
    checker: HealthChecker 인스턴스.

    등록 항목:
    - cameras   : 카메라 staleness/연결 (CameraManager.get_status 소비)
    - vlm       : VLM 실패율·지연 (OllamaProvider.get_metrics 소비)
    - model_registry : 모델 연속 실패 (MultiModelRegistry.get_model_metrics 소비)
    - gpu       : VRAM/온도 임계
    - log_dirs  : 로그·캡처 디렉터리 크기
    """

    def _check_cameras() -> ComponentHealth:
        status = pipeline.get_status()
        return check_cameras(
            status.get("cameras", {}),
            stale_sec=stale_sec,
        )

    def _check_vlm() -> ComponentHealth:
        status = pipeline.get_status()
        provider_metrics = status.get("vlm_provider", {})
        return check_vlm(
            provider_metrics,
            max_failure_rate=vlm_max_failure_rate,
            max_latency_ms=vlm_max_latency_ms,
        )

    def _check_models() -> ComponentHealth:
        status = pipeline.get_status()
        model_metrics = status.get("models", {})
        return check_model_registry(
            model_metrics,
            max_consecutive_errors=model_max_consecutive_errors,
        )

    def _check_gpu_health() -> ComponentHealth:
        return check_gpu(
            vram_threshold_percent=vram_threshold_percent,
            temp_threshold_celsius=temp_threshold_celsius,
        )

    def _check_log_dirs_health() -> ComponentHealth:
        return check_log_dirs(
            log_dir=log_dir,
            capture_dir=capture_dir,
            max_log_gb=max_log_gb,
            max_capture_gb=max_capture_gb,
        )

    checker.register("cameras", _check_cameras)
    checker.register("vlm", _check_vlm)
    checker.register("model_registry", _check_models)
    checker.register("gpu", _check_gpu_health)
    checker.register("log_dirs", _check_log_dirs_health)

    logger.info("파이프라인 헬스체크 등록 완료 (cameras/vlm/model_registry/gpu/log_dirs)")


# ─── HTTP 서버 ────────────────────────────────────────────────────────────────

class HealthHTTPHandler(BaseHTTPRequestHandler):
    """헬스체크 HTTP 핸들러"""

    health_checker: HealthChecker = None  # type: ignore

    def do_GET(self):
        if self.path == "/health" or self.path == "/healthz":
            self._send_health()
        elif self.path == "/health/live":
            self._send_liveness()
        elif self.path == "/health/ready":
            self._send_readiness()
        else:
            self.send_error(404, "Not Found")

    def _send_health(self):
        report = self.health_checker.run_checks() if self.health_checker else None
        if report is None:
            self._send_json(503, {"status": "no_checker"})
            return
        status_code = 200 if report.overall_healthy else 503
        self._send_json(status_code, report.to_dict())

    def _send_liveness(self):
        """Liveness: 프로세스가 살아있는지"""
        self._send_json(200, {"status": "alive"})

    def _send_readiness(self):
        """Readiness: 의존성이 준비되었는지"""
        report = self.health_checker.run_checks() if self.health_checker else None
        if report is None:
            self._send_json(503, {"status": "no_checker"})
            return
        # 핵심 컴포넌트만 체크
        critical_components = ["disk_space", "memory_usage"]
        all_critical_ok = all(
            c.healthy for c in report.components if c.name in critical_components
        )
        status_code = 200 if all_critical_ok else 503
        self._send_json(status_code, {"status": "ready" if all_critical_ok else "not_ready"})

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """기본 로그 출력 비활성화"""
        pass


def start_health_server(
    host: str = "127.0.0.1",  # 기본 loopback — /health 운영상태 정보의 외부노출 방지. 외부 모니터링은 0.0.0.0 opt-in.
    port: int = 8081,
    checker: HealthChecker | None = None,
) -> HTTPServer:
    """헬스체크 HTTP 서버 시작"""
    if checker is None:
        checker = HealthChecker()
        # 기본 체크 등록
        checker.register("disk_space", check_disk_space)
        checker.register("memory_usage", check_memory_usage)
        checker.register("ollama", check_ollama_available)

    HealthHTTPHandler.health_checker = checker

    server = HTTPServer((host, port), HealthHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"헬스체크 서버 시작: http://{host}:{port}/health")
    return server


# ─── CLI 진입점 ───────────────────────────────────────────────────────────────

def _build_default_checker() -> HealthChecker:
    """기본 체크(disk/memory/ollama/gpu/log_dirs)가 등록된 체커를 반환."""
    checker = HealthChecker()
    checker.register("disk_space", check_disk_space)
    checker.register("memory_usage", check_memory_usage)
    checker.register("ollama", check_ollama_available)
    checker.register("gpu", check_gpu)
    checker.register(
        "log_dirs",
        lambda: check_log_dirs(),
    )
    return checker


def _main():
    """CLI 진입점: python -m src.utils.health [--once | --serve]"""
    parser = argparse.ArgumentParser(
        prog="python -m src.utils.health",
        description="KSI-CCTV 헬스체크 CLI",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--once",
        action="store_true",
        default=True,
        help="헬스체크 1회 실행 후 JSON 출력 (기본)",
    )
    group.add_argument(
        "--serve",
        action="store_true",
        help="HTTP 헬스체크 서버 기동 (포트 8081)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8081,
        help="--serve 모드 포트 (기본 8081)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    checker = _build_default_checker()

    if args.serve:
        server = start_health_server(port=args.port, checker=checker)
        print(f"헬스체크 서버 기동: http://0.0.0.0:{args.port}/health")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
    else:
        # --once (기본)
        report = checker.run_checks()
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
        import sys
        sys.exit(0 if report.overall_healthy else 1)


if __name__ == "__main__":
    _main()
