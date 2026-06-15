"""
대시보드 스모크 테스트 (tests/test_dashboard_smoke.py)

streamlit 비의존 순수함수 단위 테스트:
  - _hash_password 일관성
  - _is_rate_limited / _record_login_failure (전역 카운터 기반 lockout)
  - _GLOBAL_LOGIN_STATE 전역 상태 동작
  - build_camera_policy_profile 우선순위
  - get_events_for_domain / get_available_domains
  - get_alert_level_icon / get_vlm_policy_label
  - _load_alert_preferences round-trip
  - _write_audit_log / _load_audit_log round-trip
  - _rotate_audit_log_if_needed (크기 기반 회전)
  - is_coco_only_active 로직
  - render_coco_warning callable
  - 무인증 경고 배너
  - load_config smoke
  - word-break CSS 포함 확인
"""
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

# ──────────────────────────────────────────────
# 1. sys.path를 가장 먼저 설정 — 이후 stub이 실제 패키지를 가리지 않도록
# ──────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ──────────────────────────────────────────────
# 2. streamlit stub 주입 (app.py 최상위에서 st.* 호출하므로 import 전에 준비)
# ──────────────────────────────────────────────

def _build_st_stub() -> types.ModuleType:
    """streamlit 최소 stub 모듈 생성."""
    st = types.ModuleType("streamlit")

    class _SessionState(dict):
        def __getattr__(self, key):
            try:
                return self[key]
            except KeyError:
                return None

        def __setattr__(self, key, value):
            self[key] = value

    st.session_state = _SessionState()

    for name in (
        "set_page_config", "markdown", "title", "subheader", "write",
        "info", "success", "warning", "error", "caption", "divider",
        "button", "text_input", "rerun", "expander",
        "selectbox", "multiselect", "slider",
        "checkbox", "metric", "image", "progress", "bar_chart",
        "line_chart", "dataframe", "json", "download_button",
        "file_uploader", "number_input",
        "form_submit_button", "select_slider", "date_input", "code",
        "stop",
    ):
        setattr(st, name, MagicMock(return_value=MagicMock()))

    # sidebar — context manager
    sidebar_cm = MagicMock()
    sidebar_cm.__enter__ = MagicMock(return_value=sidebar_cm)
    sidebar_cm.__exit__ = MagicMock(return_value=False)
    st.sidebar = sidebar_cm

    def _columns(n, *a, **kw):
        count = n if isinstance(n, int) else len(n)
        cms = []
        for _ in range(count):
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cms.append(cm)
        return cms

    st.columns = _columns

    def _tabs(labels):
        cms = []
        for _ in labels:
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cms.append(cm)
        return cms

    st.tabs = _tabs

    def _cm_factory(*a, **kw):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    st.container = _cm_factory
    st.expander = _cm_factory
    st.form = _cm_factory
    st.spinner = _cm_factory
    st.columns = _columns

    return st


_st_stub = _build_st_stub()
sys.modules["streamlit"] = _st_stub

# streamlit 서브모듈 stub
for _smod in ("streamlit.testing", "streamlit.testing.v1"):
    sys.modules.setdefault(_smod, types.ModuleType(_smod))

# ──────────────────────────────────────────────
# 3. 외부 의존성 stub (cv2, onnxruntime)
#    — src.* 실제 패키지는 건드리지 않는다
# ──────────────────────────────────────────────
if "cv2" not in sys.modules:
    _cv2 = types.ModuleType("cv2")
    _cv2.cvtColor = MagicMock(return_value=None)
    _cv2.COLOR_BGR2RGB = 4
    sys.modules["cv2"] = _cv2

if "onnxruntime" not in sys.modules:
    _ort = types.ModuleType("onnxruntime")
    _ort.get_available_providers = MagicMock(return_value=[])
    sys.modules["onnxruntime"] = _ort

# ──────────────────────────────────────────────
# 4. src.* 실제 패키지가 import되다가 실패하는 무거운 의존성(모델 로딩 등)만
#    선택적으로 stub 처리. src.dashboard.app 자체는 실제 파일을 사용.
# ──────────────────────────────────────────────

def _stub_if_missing(mod_name: str, attrs: dict | None = None):
    """sys.modules에 없을 때만 stub 등록 (실제 패키지 우선)."""
    if mod_name not in sys.modules:
        m = types.ModuleType(mod_name)
        if attrs:
            for k, v in attrs.items():
                setattr(m, k, v)
        sys.modules[mod_name] = m


# 실제 패키지가 있는 모듈은 먼저 import 시도; 실패 시에만 stub
def _try_import_or_stub(mod_name: str, attrs: dict | None = None):
    if mod_name in sys.modules:
        return
    try:
        __import__(mod_name)
    except Exception:
        _stub_if_missing(mod_name, attrs)


_try_import_or_stub("src.alerts.alert_manager", {
    "AlertConfig": MagicMock,
    "AlertManager": MagicMock,
    "parse_alert_log_line": MagicMock(return_value=None),
})
_try_import_or_stub("src.pipeline.pipeline", {
    "SafetyEvent": type("SafetyEvent", (), {
        "__init__": lambda self, **kw: [setattr(self, k, v) for k, v in kw.items()],
        "to_dict": lambda self: vars(self),
    }),
    "SafetyPipeline": MagicMock,
})
_try_import_or_stub("src.utils.config")
_try_import_or_stub("src.detection.model_registry", {"MultiModelRegistry": MagicMock})
_try_import_or_stub("src.policy.event_policy_engine", {"EventPolicyEngine": MagicMock})
_try_import_or_stub("src.models.upload_validator")
_try_import_or_stub("src.vlm.ollama_provider", {"OllamaProvider": MagicMock})
_try_import_or_stub("src.utils.system_metrics", {"collect_metrics": MagicMock})
_try_import_or_stub("src.utils.health")

# src.utils.config의 ConfigLoader / AppConfig — stub이 주입된 경우 최소 구현 보장
if "src.utils.config" in sys.modules:
    _cfg_mod = sys.modules["src.utils.config"]
    if not hasattr(_cfg_mod, "ConfigLoader"):
        class _ConfigLoader:
            @staticmethod
            def load_env(): pass
            @staticmethod
            def load(path): return {}
            @staticmethod
            def load_yaml(path): return {}
        _cfg_mod.ConfigLoader = _ConfigLoader
    if not hasattr(_cfg_mod, "AppConfig"):
        class _AppConfig:
            def load_cameras(self): return []
            def get_detection_config(self): return {}
            def get_alert_config(self): return {}
            def get(self, *a, **kw): return {}
        _cfg_mod.AppConfig = _AppConfig

# ──────────────────────────────────────────────
# 5. 실제 app 모듈 import
# ──────────────────────────────────────────────
import src.dashboard.app as app  # noqa: E402

# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────


def _reset_global_login_state():
    """각 테스트 후 전역 rate limit 상태 초기화."""
    app._GLOBAL_LOGIN_STATE["attempts"] = []
    app._GLOBAL_LOGIN_STATE["locked_until"] = None
    _st_stub.session_state.clear()
    _st_stub.session_state["login_attempts"] = []
    _st_stub.session_state["login_locked_until"] = None


# ──────────────────────────────────────────────
# 1. _hash_password 일관성
# ──────────────────────────────────────────────

class TestHashPassword:
    def test_same_input_same_output(self):
        h1 = app._hash_password("secret")
        h2 = app._hash_password("secret")
        assert h1 == h2

    def test_different_input_different_output(self):
        h1 = app._hash_password("secret")
        h2 = app._hash_password("other")
        assert h1 != h2

    def test_returns_hex_string(self):
        h = app._hash_password("any")
        assert isinstance(h, str)
        int(h, 16)  # hex 문자만 포함돼야 함

    def test_custom_salt(self):
        h_default = app._hash_password("pw")
        h_custom = app._hash_password("pw", salt="other-salt")
        assert h_default != h_custom


# ──────────────────────────────────────────────
# 2. timing-safe 비교 (compare_digest) 사용 확인
# ──────────────────────────────────────────────

class TestTimingSafeCompare:
    def test_compare_digest_used(self):
        """require_dashboard_auth 소스에 compare_digest 호출이 포함돼야 함."""
        import inspect
        src_code = inspect.getsource(app.require_dashboard_auth)
        assert "compare_digest" in src_code, "timing-safe compare_digest 미사용"


# ──────────────────────────────────────────────
# 3. 전역 rate limit / lockout
# ──────────────────────────────────────────────

class TestGlobalRateLimit:
    def setup_method(self):
        _reset_global_login_state()

    def test_no_lockout_initially(self):
        assert app._is_rate_limited() is False

    def test_lockout_after_5_failures(self):
        for _ in range(5):
            app._record_login_failure()
        assert app._GLOBAL_LOGIN_STATE["locked_until"] is not None

    def test_global_state_persists_across_session_clear(self):
        """세션 상태를 지워도 전역 lockout은 유지돼야 한다."""
        for _ in range(5):
            app._record_login_failure()
        # 세션 초기화 (쿠키 삭제 시뮬레이션)
        _st_stub.session_state.clear()
        _st_stub.session_state["login_attempts"] = []
        _st_stub.session_state["login_locked_until"] = None
        # 전역 lockout은 여전히 남아 있어야 함
        assert app._GLOBAL_LOGIN_STATE["locked_until"] is not None

    def teardown_method(self):
        _reset_global_login_state()


# ──────────────────────────────────────────────
# 4. build_camera_policy_profile 우선순위
# ──────────────────────────────────────────────

class TestBuildCameraPolicyProfile:
    def setup_method(self):
        _st_stub.session_state.clear()
        _st_stub.session_state["camera_profiles"] = {}
        _st_stub.session_state["enabled_events"] = None

    def test_session_profile_takes_priority(self):
        cam = {"id": "cam1", "detection_type": "warehouse"}
        _st_stub.session_state["camera_profiles"] = {"cam1": {"domain": "session_domain"}}
        profile = app.build_camera_policy_profile(cam, {})
        assert profile["domain"] == "session_domain"

    def test_config_profile_fallback(self):
        cam = {"id": "cam1", "detection_type": "warehouse"}
        profiles_config = {"cameras": {"cam1": {"domain": "config_domain"}}}
        profile = app.build_camera_policy_profile(cam, profiles_config)
        assert profile["domain"] == "config_domain"

    def test_camera_detection_type_fallback(self):
        cam = {"id": "cam1", "detection_type": "warehouse"}
        profile = app.build_camera_policy_profile(cam, {})
        assert profile["domain"] == "warehouse"

    def test_default_construction_domain(self):
        cam = {"id": "cam1"}
        profile = app.build_camera_policy_profile(cam, {})
        assert profile["domain"] == "construction"


# ──────────────────────────────────────────────
# 5. get_available_domains / get_events_for_domain
# ──────────────────────────────────────────────

class TestDomainEventHelpers:
    def _sample_policies(self):
        return {
            "profiles": {
                "construction": {
                    "policies": {
                        "person_detected": {
                            "enabled": True,
                            "alert_level": "medium",
                            "vlm": "on_ambiguous",
                            "description": "사람 감지",
                        },
                        "fire_detected": {
                            "enabled": False,
                            "alert_level": "critical",
                            "vlm": "always",
                            "description": "화재 감지",
                        },
                    }
                },
                "warehouse": {},
            }
        }

    def test_get_available_domains(self):
        policies = self._sample_policies()
        domains = app.get_available_domains(policies)
        assert "construction" in domains
        assert "warehouse" in domains

    def test_get_events_for_domain_returns_list(self):
        policies = self._sample_policies()
        events = app.get_events_for_domain(policies, "construction")
        assert len(events) == 2

    def test_event_has_required_keys(self):
        policies = self._sample_policies()
        events = app.get_events_for_domain(policies, "construction")
        for evt in events:
            assert "name" in evt
            assert "enabled" in evt
            assert "alert_level" in evt
            assert "vlm" in evt

    def test_empty_domain(self):
        assert app.get_events_for_domain({}, "unknown") == []


# ──────────────────────────────────────────────
# 6. get_alert_level_icon / get_vlm_policy_label
# ──────────────────────────────────────────────

class TestLabelHelpers:
    def test_alert_level_icons(self):
        assert app.get_alert_level_icon("low") == "🟢"
        assert app.get_alert_level_icon("medium") == "🟡"
        assert app.get_alert_level_icon("high") == "🔴"
        assert app.get_alert_level_icon("critical") == "⛔"

    def test_unknown_level_returns_default(self):
        assert app.get_alert_level_icon("unknown") == "⚪"

    def test_vlm_policy_labels(self):
        assert app.get_vlm_policy_label("never") == "사용 안 함"
        assert app.get_vlm_policy_label("always") == "항상 분석"
        assert app.get_vlm_policy_label("on_ambiguous") == "애매할 때만"

    def test_unknown_policy_returns_raw(self):
        assert app.get_vlm_policy_label("custom_policy") == "custom_policy"


# ──────────────────────────────────────────────
# 7. _load_alert_preferences 기본값 반환
# ──────────────────────────────────────────────

class TestAlertPreferences:
    def test_default_prefs_on_missing_file(self):
        """파일 없으면 기본값 반환."""
        with patch("src.dashboard.app.Path") as mock_path_cls:
            mp = MagicMock()
            mp.exists.return_value = False
            mock_path_cls.return_value = mp
            prefs = app._load_alert_preferences()
        assert "email_alert_level_threshold" in prefs
        assert "per_event_email_enabled" in prefs

    def test_round_trip_via_mock(self):
        """ConfigLoader.load_yaml 를 mock해 round-trip 검증."""
        test_prefs = {
            "email_alert_level_threshold": "high",
            "per_event_email_enabled": {"person_detected": True},
        }
        with patch("src.dashboard.app.Path") as mock_path_cls:
            mp = MagicMock()
            mp.exists.return_value = True
            mock_path_cls.return_value = mp
            with patch("src.dashboard.app.ConfigLoader") as mock_cl:
                mock_cl.load_yaml.return_value = test_prefs
                loaded = app._load_alert_preferences()
        assert loaded["email_alert_level_threshold"] == "high"
        assert loaded["per_event_email_enabled"]["person_detected"] is True


# ──────────────────────────────────────────────
# 8. _write_audit_log / _rotate_audit_log_if_needed
# ──────────────────────────────────────────────

class TestAuditLog:
    def test_write_creates_valid_jsonl(self, tmp_path):
        """실제 파일 I/O로 JSONL 형식 검증."""
        log_file = tmp_path / "dashboard_audit.jsonl"
        entry = {"timestamp": "2026-01-01T00:00:00", "event": "smoke_test"}

        # Path("./logs/dashboard_audit.jsonl") → tmp 경로로 교체
        real_path = app.Path

        def _patched_path(p, *a):
            if "dashboard_audit" in str(p):
                return log_file
            return real_path(p, *a)

        with patch("src.dashboard.app.Path", side_effect=_patched_path):
            # stat().st_size가 없으므로 rotate skip — log_file을 먼저 생성
            log_file.touch()
            app._write_audit_log(entry)

        lines = [ln for ln in log_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) >= 1
        parsed = json.loads(lines[-1])
        assert parsed["event"] == "smoke_test"


class TestAuditLogRotation:
    def test_rotation_removes_old_lines(self, tmp_path):
        log_file = tmp_path / "dashboard_audit.jsonl"
        lines = [json.dumps({"event": f"e{i}"}) + "\n" for i in range(100)]
        log_file.write_text("".join(lines), encoding="utf-8")

        original_max = app._AUDIT_LOG_MAX_BYTES
        try:
            app._AUDIT_LOG_MAX_BYTES = 10  # 강제 회전 트리거
            app._rotate_audit_log_if_needed(log_file)
        finally:
            app._AUDIT_LOG_MAX_BYTES = original_max

        remaining = [
            line for line in log_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(remaining) == 50  # 50% 유지

    def test_no_rotation_below_threshold(self, tmp_path):
        log_file = tmp_path / "dashboard_audit.jsonl"
        log_file.write_text('{"event":"x"}\n', encoding="utf-8")
        original_max = app._AUDIT_LOG_MAX_BYTES
        try:
            app._AUDIT_LOG_MAX_BYTES = 1024 * 1024  # 1 MB — 파일보다 훨씬 큼
            app._rotate_audit_log_if_needed(log_file)
        finally:
            app._AUDIT_LOG_MAX_BYTES = original_max
        assert '{"event":"x"}' in log_file.read_text(encoding="utf-8")


# ──────────────────────────────────────────────
# 9. is_coco_only_active 로직
# ──────────────────────────────────────────────

class TestIsCocoOnlyActive:
    def test_returns_false_when_no_models(self):
        with patch.object(app, "get_model_status", return_value={"models": {}}):
            assert app.is_coco_only_active() is False

    def test_returns_true_when_all_coco(self):
        with patch.object(app, "get_model_status", return_value={
            "models": {
                "model_a": {"loaded": True, "class_profile": "coco"},
                "model_b": {"loaded": True, "class_profile": "coco"},
            }
        }):
            assert app.is_coco_only_active() is True

    def test_returns_false_when_custom_model_present(self):
        with patch.object(app, "get_model_status", return_value={
            "models": {
                "model_a": {"loaded": True, "class_profile": "coco"},
                "model_b": {"loaded": True, "class_profile": "safety_custom"},
            }
        }):
            assert app.is_coco_only_active() is False

    def test_only_counts_loaded_models(self):
        """로드되지 않은 모델은 집계에서 제외."""
        with patch.object(app, "get_model_status", return_value={
            "models": {
                "active": {"loaded": True, "class_profile": "coco"},
                "inactive": {"loaded": False, "class_profile": "safety_custom"},
            }
        }):
            assert app.is_coco_only_active() is True

    def test_returns_false_on_exception(self):
        with patch.object(app, "get_model_status", side_effect=Exception("fail")):
            assert app.is_coco_only_active() is False


# ──────────────────────────────────────────────
# 10. render_coco_warning callable / 호출 동작
# ──────────────────────────────────────────────

class TestRenderCocoWarning:
    def test_is_callable(self):
        assert callable(app.render_coco_warning)

    def test_calls_st_warning_when_coco_only(self):
        with patch.object(app, "is_coco_only_active", return_value=True):
            _st_stub.warning = MagicMock()
            app.render_coco_warning()
            _st_stub.warning.assert_called_once()
            call_text = str(_st_stub.warning.call_args)
            # 경고 텍스트에 COCO 관련 내용 포함 확인
            assert any(kw in call_text for kw in ("COCO", "coco", "안전모", "safety"))

    def test_no_warning_when_custom_model(self):
        with patch.object(app, "is_coco_only_active", return_value=False):
            _st_stub.warning = MagicMock()
            app.render_coco_warning()
            _st_stub.warning.assert_not_called()


# ──────────────────────────────────────────────
# 11. 무인증 경고 배너
# ──────────────────────────────────────────────

class TestAuthBanner:
    def test_no_password_shows_warning_and_returns_true(self):
        """비밀번호 미설정 시 True 반환 + 경고 배너."""
        _st_stub.session_state.clear()
        _st_stub.warning = MagicMock()
        with (
            patch.dict("os.environ", {"KSI_CCTV_DASHBOARD_PASSWORD": ""}, clear=False),
            patch("src.dashboard.app.ConfigLoader.load_env"),
        ):
            result = app.require_dashboard_auth()
        assert result is True
        _st_stub.warning.assert_called()

    def test_change_me_password_shows_warning(self):
        """'change-me' 비밀번호이면 경고 배너 표시."""
        _st_stub.session_state.clear()
        _st_stub.warning = MagicMock()
        with (
            patch.dict("os.environ", {"KSI_CCTV_DASHBOARD_PASSWORD": "change-me"}, clear=False),
            patch("src.dashboard.app.ConfigLoader.load_env"),
        ):
            result = app.require_dashboard_auth()
        assert result is True
        _st_stub.warning.assert_called()


# ──────────────────────────────────────────────
# 12. load_config smoke
# ──────────────────────────────────────────────

class TestLoadConfigSmoke:
    def test_returns_none_on_failure(self):
        with patch("src.dashboard.app.AppConfig", side_effect=Exception("no config")):
            result = app.load_config()
        assert result is None

    def test_returns_appconfig_instance(self):
        mock_cfg = MagicMock()
        with patch("src.dashboard.app.AppConfig", return_value=mock_cfg):
            result = app.load_config()
        assert result is mock_cfg


# ──────────────────────────────────────────────
# 13. word-break: keep-all CSS 포함 확인
# ──────────────────────────────────────────────

class TestCssWordBreak:
    def test_word_break_keep_all_in_source(self):
        """CSS에 word-break: keep-all이 포함돼야 한다 (CLAUDE.md 한글 UI 기본값)."""
        src_file = _PROJECT_ROOT / "src" / "dashboard" / "app.py"
        content = src_file.read_text(encoding="utf-8")
        assert "word-break: keep-all" in content, "CSS에 word-break: keep-all 누락"
