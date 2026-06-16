"""대시보드 실 렌더 게이트 (streamlit AppTest) — 'green ≠ 작동' UI판.

기존 test_dashboard_smoke.py는 streamlit을 stub으로 대체해 순수 로직만 검증하므로(실제
렌더 미수행) 빈 데이터에서 탭이 깨지는 등의 렌더 결함을 못 잡는다. 이 테스트는 **실제
streamlit AppTest**로 app.py를 인증 세션 + 빈 데이터로 한 번 렌더해 13개 탭이 예외 없이
그려지는지 확인한다.

stub 오염 회피: test_dashboard_smoke가 sys.modules["streamlit"]에 stub을 주입하므로 같은
프로세스에선 AppTest(실 streamlit 필요)와 충돌한다 → **subprocess로 격리** 실행한다.

실 ONNX 모델/streamlit이 없으면(CI 등) 자동 skip. 빠름(~3s).
"""
import subprocess
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_REQUIRED_MODELS = [
    _ROOT / "models" / "yolo26.onnx",
    _ROOT / "models" / "yolo26_ppe.onnx",
    _ROOT / "models" / "yolo26_fire_smoke.onnx",
]

try:
    from streamlit.testing.v1 import AppTest  # noqa: F401
    _HAS_APPTEST = True
except Exception:
    _HAS_APPTEST = False

_HAS_ASSETS = all(m.exists() for m in _REQUIRED_MODELS)

# subprocess에서 실행할 AppTest 렌더 스크립트(클린 인터프리터 = 실 streamlit).
_RENDER_SCRIPT = r"""
import os, queue, time
os.environ["KSI_CCTV_DASHBOARD_PASSWORD"] = "apptest-pw"
from streamlit.testing.v1 import AppTest
at = AppTest.from_file("src/dashboard/app.py", default_timeout=120)
# init_session_state가 덮어쓰지 않도록 initialized=True와 함께 인증 세션을 시드한다.
seed = dict(initialized=True, authenticated=True, pipeline=None, alert_manager=None,
    events=[], is_running=False, current_frame={}, detection_results={}, camera_configs=[],
    event_queue=queue.Queue(), selected_domain="general", enabled_events=None, camera_profiles={},
    auto_refresh_enabled=False, auto_refresh_interval=5, last_activity=time.time(),
    login_attempts=[], login_locked_until=None)
for k, v in seed.items():
    at.session_state[k] = v
at.run()
assert not at.exception, f"AppTest 렌더 예외: {[str(e.value)[:300] for e in at.exception]}"
assert len(at.tabs) >= 10, f"탭이 충분히 렌더되지 않음: {len(at.tabs)}"
assert not at.error, f"화면에 에러 렌더됨: {[e.value[:200] for e in at.error]}"
print("DASHBOARD_RENDER_OK tabs=%d" % len(at.tabs))
"""


@unittest.skipUnless(_HAS_APPTEST and _HAS_ASSETS, "streamlit AppTest + 실 ONNX 모델 필요")
class TestDashboardRender(unittest.TestCase):
    def test_authenticated_dashboard_renders_all_tabs_without_exception(self):
        result = subprocess.run(
            [sys.executable, "-c", _RENDER_SCRIPT],
            cwd=str(_ROOT), capture_output=True, text=True, timeout=180,
        )
        self.assertIn(
            "DASHBOARD_RENDER_OK", result.stdout,
            f"대시보드 실 렌더 실패\n--- stdout ---\n{result.stdout[-2000:]}\n--- stderr ---\n{result.stderr[-2000:]}",
        )
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])


if __name__ == "__main__":
    unittest.main()
