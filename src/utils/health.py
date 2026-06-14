"""
헬스체크 모듈

시스템 상태 점검 및 HTTP 엔드포인트 제공.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional

from src.utils.system_metrics import collect_metrics, SystemMetrics

logger = logging.getLogger(__name__)


@dataclass
class ComponentHealth:
    """컴포넌트 헬스 상태"""
    name: str
    healthy: bool
    message: str = ""
    last_check: float = field(default_factory=time.time)
    metadata: Dict = field(default_factory=dict)


@dataclass
class HealthReport:
    """전체 헬스체크 리포트"""
    overall_healthy: bool
    components: List[ComponentHealth]
    system_metrics: SystemMetrics
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
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
        self._checks: Dict[str, callable] = {}
        self._lock = threading.Lock()

    def register(self, name: str, check_fn: callable):
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


# 기본 헬스체크 함수들
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
    """Ollama 서비스 연결 점검"""
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

    def _send_json(self, code: int, data: Dict):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """기본 로그 출력 비활성화"""
        pass


def start_health_server(
    host: str = "0.0.0.0",
    port: int = 8081,
    checker: Optional[HealthChecker] = None,
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
