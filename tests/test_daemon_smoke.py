"""헤드리스 데몬(src/run.py) 실 종단 기동 smoke — 'green ≠ 작동' 게이트.

기존 test_run.py는 build_pipeline/_register_cameras를 Mock으로만 검증한다(데몬 main loop
미통과). 이 테스트는 실제로 run.py를 subprocess로 띄워 모델 로드→/health 응답→SIGTERM
graceful 종료(exit 0)까지 한 번 통과시킨다.

느리고(실 모델 CPU 로드 수~십초) 환경 의존적이라 명시 opt-in일 때만 실행:
  KSI_CCTV_TEST_DAEMON=1 ./.venv/bin/python -m pytest tests/test_daemon_smoke.py -v
실 ONNX 모델/sample.mp4가 없으면(CI 등) 자동 skip.
"""
import os
import signal
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_SAMPLE = _ROOT / "data" / "sample.mp4"
_REQUIRED_MODELS = [
    _ROOT / "models" / "yolo26.onnx",
    _ROOT / "models" / "yolo26_ppe.onnx",
    _ROOT / "models" / "yolo26_fire_smoke.onnx",
]

try:
    import onnxruntime  # noqa: F401
    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

_HAS_ASSETS = _HAS_DEPS and _SAMPLE.exists() and all(m.exists() for m in _REQUIRED_MODELS)
_OPT_IN = os.environ.get("KSI_CCTV_TEST_DAEMON", "").strip() == "1"


@unittest.skipUnless(
    _HAS_ASSETS and _OPT_IN,
    "KSI_CCTV_TEST_DAEMON=1 + 실 ONNX/sample.mp4 필요(데몬 종단 smoke)",
)
class TestDaemonSmoke(unittest.TestCase):
    def test_daemon_boots_serves_health_and_stops_clean(self):
        port = 18099
        env = dict(os.environ)
        env["CCTV_CAM_001_URL"] = str(_SAMPLE)          # sample.mp4를 카메라 소스로
        env["KSI_CCTV_HEALTH_PORT"] = str(port)
        env["KSI_CCTV_HEALTH_HOST"] = "127.0.0.1"
        env["KSI_CCTV_LOG_LEVEL"] = "WARNING"
        env["KSI_CCTV_API_KEY"] = "smoke-api-key"  # 통합 API(/api/*) 검증용

        proc = subprocess.Popen(
            [sys.executable, "src/run.py"],
            cwd=str(_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            # /health/live가 200을 줄 때까지 폴링(모델 CPU 로드 후 헬스서버 기동 — 넉넉히 60s).
            live = False
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    out = proc.stdout.read() if proc.stdout else ""
                    self.fail(f"데몬이 조기 종료(exit={proc.returncode})\n{out[-2000:]}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=2) as r:
                        if r.status == 200:
                            live = True
                            break
                except Exception:
                    time.sleep(1)
            self.assertTrue(live, "/health/live가 60초 내 200을 반환하지 않음")

            # /health(종합)도 응답하는지(JSON, 200 또는 503 degraded 모두 정상 응답으로 간주)
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3)
                health_ok = True
            except urllib.error.HTTPError:
                health_ok = True  # 503(degraded)도 '응답함'이므로 OK
            except Exception:
                health_ok = False
            self.assertTrue(health_ok, "/health 엔드포인트가 응답하지 않음")

            # P1c-2: 통합 엔진 API가 데몬 안에서 라이브로 서빙되는지 — /api/control/status(인증).
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/control/status",
                headers={"X-API-Key": "smoke-api-key"},
            )
            import json as _json
            with urllib.request.urlopen(req, timeout=3) as r:
                self.assertEqual(r.status, 200)
                body = _json.loads(r.read().decode())
            self.assertTrue(body.get("connected"), "데몬 내 파이프라인이 API에 연결되지 않음")

            # SIGTERM → graceful shutdown → exit 0(run.py os._exit(_code))
            proc.send_signal(signal.SIGTERM)
            try:
                rc = proc.wait(timeout=35)
            except subprocess.TimeoutExpired:
                self.fail("SIGTERM 후 35초 내 종료하지 않음(graceful shutdown 실패)")
            self.assertEqual(rc, 0, f"graceful 종료 코드가 0이 아님: {rc}")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
