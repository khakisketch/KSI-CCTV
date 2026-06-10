"""
메인 스트림릿 대시보드

실시간 CCTV 스트림, 탐지 결과, 알람 로그 대시보드
"""
import sys
import os
import hashlib
import json
import uuid
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

import logging
import time
from datetime import datetime
from queue import Queue, Empty
from typing import Any, Dict, List

import streamlit as st
import cv2
import numpy as np
from PIL import Image

from src.detection.yolo_detector import YOLODetector
from src.pipeline.pipeline import SafetyPipeline, SafetyEvent
from src.alerts.alert_manager import AlertManager, AlertConfig
from src.utils.config import AppConfig, ConfigLoader

try:
    from src.detection.model_registry import MultiModelRegistry  # noqa: F401
    HAS_MODEL_REGISTRY = True
except ImportError:
    HAS_MODEL_REGISTRY = False

# Phase 6: Model upload validation
try:
    from src.models.upload_validator import (  # noqa: F401
        activate_uploaded_model,
        deactivate_uploaded_model,
        upload_model,
        get_uploaded_models,
        delete_uploaded_model,
    )
    HAS_UPLOAD_VALIDATOR = True
except ImportError:
    HAS_UPLOAD_VALIDATOR = False

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

# 스트림릿 페이지 설정
st.set_page_config(
    page_title="KSI-CCTV 안전 관리 대시보드",
    page_icon="🎥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS 스타일
st.markdown("""
<style>
.main-header { font-size: 2.5rem; font-weight: bold; color: #1f77b4; }
.sub-header { font-size: 1.5rem; font-weight: bold; color: #2ca02c; }
.status-online { color: #2ca02c; font-weight: bold; }
.status-offline { color: #d62728; font-weight: bold; }
.alert-card {
    padding: 15px;
    border-radius: 10px;
    margin: 10px 0;
    border-left: 5px solid #d62728;
    background-color: #fff3f3;
}
.metric-card {
    padding: 20px;
    border-radius: 10px;
    background-color: #f0f2f6;
    text-align: center;
}
</style>
""", unsafe_allow_html=True)

def load_config():
    """설정 로드"""
    try:
        config = AppConfig()
        return config
    except Exception as e:
        logger.error(f"설정 로드 실패: {e}")
        return None

def init_session_state():
    """세션 상태 초기화"""
    if 'initialized' not in st.session_state:
        st.session_state.initialized = True
        st.session_state.pipeline = None
        st.session_state.alert_manager = None
        st.session_state.events = []
        st.session_state.is_running = False
        st.session_state.current_frame = {}
        st.session_state.detection_results = {}
        st.session_state.camera_configs = []
        st.session_state.event_queue = Queue()
        st.session_state.authenticated = False

        # Phase 5: Camera Settings state
        st.session_state.selected_domain = "construction"
        st.session_state.enabled_events = None  # None = use all enabled events from policy
        st.session_state.camera_profiles = {}  # camera_id -> profile dict

        # Phase 8 M5.1: Auto-refresh + Security
        st.session_state.auto_refresh_enabled = True
        st.session_state.auto_refresh_interval = 5
        st.session_state.last_activity = datetime.utcnow().timestamp()
        st.session_state.login_attempts = []  # List[Dict] for rate limiting
        st.session_state.login_locked_until = None

def _hash_password(password: str, salt: str = "ksi-cctv-dashboard") -> str:
    """비밀번호 해싱 (hashlib.scrypt)"""
    h = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=16384,
        r=8,
        p=1,
        dklen=64,
    )
    return h.hex()

def _load_audit_log() -> List[Dict]:
    """오디트 로그 로드"""
    log_path = Path("./logs/dashboard_audit.jsonl")
    if not log_path.exists():
        return []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except Exception:
        return []

def _write_audit_log(entry: Dict):
    """오디트 로그 쓰기"""
    log_path = Path("./logs/dashboard_audit.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


# ───────── Phase 5: Camera Settings Helpers ─────────

def load_event_policies() -> Dict[str, Any]:
    """event_policies.yaml 로드 (실패 시 빈 dict 반환)"""
    try:
        return ConfigLoader.load("./config/event_policies.yaml")
    except Exception:
        return {}


def get_available_domains(policies: Dict[str, Any]) -> List[str]:
    """사용 가능한 도메인 목록 추출"""
    profiles = policies.get("profiles", {}) if isinstance(policies, dict) else {}
    return list(profiles.keys()) if isinstance(profiles, dict) else []


def get_events_for_domain(policies: Dict[str, Any], domain: str) -> List[Dict]:
    """특정 도메인의 이벤트 목록 추출"""
    profile = policies.get("profiles", {}).get(domain, {}) if isinstance(policies, dict) else {}
    policy_map = profile.get("policies", {}) if isinstance(profile, dict) else {}
    events = []
    for name, policy in policy_map.items():
        if isinstance(policy, dict):
            enabled = policy.get("enabled", True)
            alert_level = policy.get("alert_level", "low")
            vlm_policy = policy.get("vlm", "never")
            description = policy.get("description", name)
            events.append({
                "name": name,
                "enabled": enabled,
                "alert_level": alert_level,
                "vlm": vlm_policy,
                "description": description,
            })
    return events


def get_model_status() -> Dict[str, Any]:
    """MultiModelRegistry 상태 조회 (없으면 config 기반 fallback)"""
    if not HAS_MODEL_REGISTRY:
        # config/model_profiles.yaml 기반 fallback
        try:
            profiles = ConfigLoader.load("./config/model_profiles.yaml")
            models_info = {}
            for name, info in profiles.items():
                if isinstance(info, dict):
                    models_info[name] = {
                        "enabled": info.get("enabled", False),
                        "type": info.get("type", "unknown"),
                        "status": "active" if info.get("enabled") else "disabled",
                    }
            return {"models": models_info, "source": "config"}
        except Exception:
            return {"models": {}, "source": "none"}

    try:
        registry = MultiModelRegistry.from_config("./config/model_profiles.yaml")
        status_dict = registry.to_status_dict()
        return {
            "active_models": list(status_dict.get("active_model_names", [])),
            "failed_models": dict(status_dict.get("failed_models", {})),
            "source": "registry",
        }
    except Exception:
        return {"models": {}, "source": "error"}


def get_alert_level_icon(level: str) -> str:
    """alert_level → 아이콘 매핑"""
    icons = {
        "low": "🟢",
        "medium": "🟡",
        "high": "🔴",
        "critical": "⛔",
    }
    return icons.get(level, "⚪")


def get_vlm_policy_label(policy: str) -> str:
    """VLM 정책 → 읽기 쉬운 라벨"""
    labels = {
        "never": "사용 안 함",
        "always": "항상 분석",
        "on_ambiguous": "애매할 때만",
        "on_high_confidence": "고신뢰 시",
        "on_low_confidence": "저신뢰 시",
    }
    return labels.get(policy, policy)


def _is_rate_limited() -> bool:
    """Rate limit login attempts (5 failures per minute lockout 60s)"""
    now = datetime.utcnow().timestamp()
    # Check if currently locked
    if st.session_state.get("login_locked_until"):
        locked_until = st.session_state.login_locked_until
        if now < locked_until:
            remaining = int(locked_until - now)
            st.warning(f"로그인 시도가 너무 많습니다. {remaining}초 후에 다시 시도하세요.")
            return True
        else:
            st.session_state.login_locked_until = None
            st.session_state.login_attempts = []
    return False

def _record_login_failure() -> None:
    """Record a failed login attempt for rate limiting"""
    now = datetime.utcnow().timestamp()
    window = 60  # 60 seconds window
    attempts = st.session_state.get("login_attempts", [])
    attempts = [t for t in attempts if now - t < window]
    attempts.append(now)
    st.session_state.login_attempts = attempts
    if len(attempts) >= 5:
        st.session_state.login_locked_until = now + 60
        st.warning("로그인 시도 5회 실패. 60초 동안 잠깁니다.")


def require_dashboard_auth() -> bool:
    """선택적 대시보드 비밀번호 보호 (해싱 기반 + rate limiting + session timeout)"""
    ConfigLoader.load_env()
    password = os.environ.get("KSI_CCTV_DASHBOARD_PASSWORD", "").strip()
    if not password or password == "change-me":
        return True

    # Session timeout check (30 min inactivity)
    last_activity = st.session_state.get("last_activity")
    if last_activity and (datetime.utcnow().timestamp() - last_activity) > 1800:
        st.session_state.authenticated = False
        st.warning("세션이 만료되었습니다. 다시 로그인해 주세요.")
        _write_audit_log({
            "timestamp": datetime.utcnow().isoformat(),
            "event": "session_timeout",
        })
        st.rerun()

    if st.session_state.get("authenticated"):
        # Update activity on every call
        st.session_state.last_activity = datetime.utcnow().timestamp()
        return True

    # Rate limiting before showing login form
    if _is_rate_limited():
        return False

    st.title("KSI-CCTV 로그인")
    entered = st.text_input("대시보드 비밀번호", type="password")
    if st.button("로그인"):
        hashed = _hash_password(entered)
        expected = _hash_password(password)
        if hashed == expected:
            st.session_state.authenticated = True
            st.session_state.login_attempts = []
            st.session_state.login_locked_until = None
            st.session_state.last_activity = datetime.utcnow().timestamp()
            _write_audit_log({
                "timestamp": datetime.utcnow().isoformat(),
                "event": "login_success",
                "session_id": str(uuid.uuid4()),
            })
            st.rerun()
        else:
            _record_login_failure()
            _write_audit_log({
                "timestamp": datetime.utcnow().isoformat(),
                "event": "login_failure",
            })
            st.error("비밀번호가 올바르지 않습니다")
    return False


def is_admin_model_management_allowed() -> bool:
    """모델 업로드/활성화/삭제는 명시적 대시보드 비밀번호가 설정된 세션만 허용."""
    ConfigLoader.load_env()
    password = os.environ.get("KSI_CCTV_DASHBOARD_PASSWORD", "").strip()
    return bool(password and password != "change-me" and st.session_state.get("authenticated"))


def audit_model_action(event: str, **extra: Any) -> None:
    """모델 관리 작업 감사 로그 기록."""
    _write_audit_log({
        "timestamp": datetime.utcnow().isoformat(),
        "event": event,
        **extra,
    })

def drain_event_queue():
    """백그라운드 파이프라인 스레드에서 들어온 이벤트를 UI 상태로 반영"""
    event_queue = st.session_state.get('event_queue')
    if event_queue is None:
        return

    while True:
        try:
            event = event_queue.get_nowait()
        except Empty:
            break

        st.session_state.events.append(event)
        if len(st.session_state.events) > 1000:
            st.session_state.events.pop(0)

def get_status_color(status: bool) -> str:
    """연결 상태 색상"""
    return "🟢 연결됨" if status else "🔴 연결 끊김"

def load_enabled_cameras(config: AppConfig) -> List[Dict]:
    """활성화된 카메라 설정 로드"""
    cameras = config.load_cameras()
    return [camera for camera in cameras if camera.get("enabled")]

def render_sidebar():
    """사이드바 렌더링"""
    with st.sidebar:
        st.title("📊 관리 메뉴")
        
        # 연결 제어
        st.subheader("시스템 제어")
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("▶️ 시작", use_container_width=True):
                start_pipeline()
        
        with col2:
            if st.button("⏹️ 중지", use_container_width=True):
                stop_pipeline()
        
        # 상태 표시
        st.divider()
        st.subheader("시스템 상태")
        
        if st.session_state.get('is_running'):
            st.markdown("🔴 <span class='status-online'>실행 중</span>", unsafe_allow_html=True)
        else:
            st.markdown("⚪ <span class='status-offline'>중지됨</span>", unsafe_allow_html=True)

        # VLM 상태
        pipeline = st.session_state.get('pipeline')
        if pipeline and hasattr(pipeline, 'vlm_provider'):
            vlm = pipeline.vlm_provider
            if vlm is not None:
                available = vlm.is_available()
                status_text = "🟢 연결됨" if available else "🔴 오프라인"
                st.caption(f"VLM ({vlm.model}): {status_text}")

        # 이벤트 통계
        if st.session_state.get('events'):
            alert_count = len([
                event for event in st.session_state.events
                if getattr(event, "event_type", "") != "vlm_analysis_candidate"
            ])
            st.metric("총 이벤트 수", len(st.session_state.events))
            st.caption(f"알람 이벤트: {alert_count} / VLM 분석 후보: {len(st.session_state.events) - alert_count}")
        
        # 설정
        st.divider()
        st.subheader("설정")
        
        # 모델 설정
        st.session_state.conf_threshold = st.slider(
            "신뢰도 임계값", 0.0, 1.0, 0.25, 0.05
        )
        st.session_state.iou_threshold = st.slider(
            "IoU 임계값", 0.0, 1.0, 0.45, 0.05
        )
        st.session_state.skip_frames = st.slider(
            "프레임 스킵", 1, 30, 5
        )

        # Phase 8 M5.1: Auto-refresh control
        st.divider()
        st.subheader("새로고침")
        st.session_state.auto_refresh_enabled = st.checkbox(
            "자동 새로고침", value=st.session_state.get("auto_refresh_enabled", True)
        )
        if st.session_state.auto_refresh_enabled:
            interval = st.slider(
                "새로고침 간격 (초)", 1, 30, st.session_state.get("auto_refresh_interval", 5)
            )
            st.session_state.auto_refresh_interval = interval

def start_pipeline():
    """파이프라인 시작"""
    try:
        if st.session_state.get('is_running'):
            st.warning("이미 실행 중입니다")
            return
        
        # 설정
        config = load_config()
        if not config:
            st.error("설정을 로드할 수 없습니다")
            return
        
        # 디텍터 설정
        yolo_config = config.get('models', 'yolo', default={})
        model_path = yolo_config.get('model_path', './models/yolo26.onnx')
        input_size = tuple(yolo_config.get('input_size', [640, 640]))
        class_profile = yolo_config.get('class_profile', 'coco')
        configured_classes = yolo_config.get('classes')
        detector = YOLODetector(
            model_path=model_path,
            input_size=input_size,
            conf_threshold=st.session_state.get('conf_threshold', 0.25),
            iou_threshold=st.session_state.get('iou_threshold', 0.45),
            device=config.get('device', 'primary', default='cuda'),
            fp16=bool(config.get('device', 'fp16', default=True)),
            postprocess_mode=yolo_config.get('postprocess_mode', 'auto'),
            class_profile=class_profile,
            classes=configured_classes or None
        )
        
        # 파이프라인 설정
        detection_config = config.get_detection_config()
        target_classes = detection_config.get('target_classes', 'person,fire,smoke,fall')
        if isinstance(target_classes, str):
            target_classes = [item.strip() for item in target_classes.split(',') if item.strip()]
        vlm_trigger_classes = detection_config.get('vlm_trigger_classes', 'person')
        if isinstance(vlm_trigger_classes, str):
            vlm_trigger_classes = [item.strip() for item in vlm_trigger_classes.split(',') if item.strip()]

        pipeline = SafetyPipeline(
            detector=detector,
            skip_frames=st.session_state.get('skip_frames', 5),
            alert_cooldown=float(detection_config.get('alert_cooldown_sec', 30.0)),
            target_classes=target_classes,
            vlm_trigger_classes=vlm_trigger_classes,
            vlm_cooldown=float(detection_config.get('vlm_cooldown_sec', 60.0))
        )
        
        # 알람 관리자
        alerts_cfg = config.get_alert_config()
        alert_config = AlertConfig(
            enabled=bool(alerts_cfg.get("enabled", True)),
            log_file=alerts_cfg.get("log_file", "./logs/alerts.log"),
            console_output=bool(alerts_cfg.get("console_output", True)),
            save_images=bool(alerts_cfg.get("save_images", True)),
            image_dir=alerts_cfg.get("image_dir", "./data/captures"),
            image_retention_days=int(alerts_cfg.get("image_retention_days", 30)),
            blur_faces=bool(alerts_cfg.get("blur_faces", True)),
            face_blur_strength=int(alerts_cfg.get("face_blur_strength", 35)),
            log_max_bytes=int(alerts_cfg.get("log_max_bytes", 10 * 1024 * 1024)),
            log_backup_count=int(alerts_cfg.get("log_backup_count", 5))
        )
        alert_manager = AlertManager(alert_config)
        
        # 콜백 등록
        event_queue = st.session_state.event_queue

        def on_event(event: SafetyEvent):
            event_queue.put(event)
            alert_manager.handle_event(event)

        def on_analysis_event(event: SafetyEvent):
            event_queue.put(event)
        
        pipeline.add_alert_callback(on_event)
        pipeline.add_analysis_callback(on_analysis_event)

        # VLM 활성화
        vlm_cfg = config.get('models', 'vlm', default={})
        if bool(vlm_cfg.get("enabled", False)):
            from src.vlm.ollama_provider import OllamaProvider
            provider = OllamaProvider(
                base_url=vlm_cfg.get("base_url", "http://localhost:11434"),
                model=vlm_cfg.get("model_name", "qwen3-vl:latest"),
                timeout_sec=float(vlm_cfg.get("timeout_sec", 60)),
                max_retries=int(vlm_cfg.get("max_retries", 2)),
            )
            prompt_path = vlm_cfg.get(
                "prompt_template", "./config/prompts/construction.yaml"
            )
            pipeline.enable_vlm(provider, prompt_path)

        cameras_config = load_enabled_cameras(config)
        if not cameras_config:
            st.error("활성화된 카메라가 없습니다. config/cameras.json에서 enabled=true와 stream_url을 설정하세요.")
            return

        for cam in cameras_config:
            stream_url = cam.get("stream_url", "")
            if not stream_url or "${" in stream_url:
                st.error(f"{cam.get('id', 'unknown')}의 stream_url 환경변수가 설정되지 않았습니다.")
                return
            pipeline.add_camera(
                cam["id"],
                stream_url,
                target_fps=int(cam.get("fps", 30))
            )
        
        # 시작
        pipeline.start()
        
        st.session_state.pipeline = pipeline
        st.session_state.alert_manager = alert_manager
        st.session_state.camera_configs = cameras_config
        st.session_state.is_running = True
        
        st.success("파이프라인 시작 완료!")
        logger.info("파이프라인 시작 완료")
        
    except Exception as e:
        st.error(f"시작 실패: {e}")
        logger.error(f"시작 실패: {e}")

def stop_pipeline():
    """파이프라인 중지"""
    try:
        if st.session_state.get('pipeline'):
            st.session_state.pipeline.stop()
        
        st.session_state.is_running = False
        st.success("파이프라인 중지 완료")
        logger.info("파이프라인 중지 완료")
        
    except Exception as e:
        st.error(f"중지 실패: {e}")
        logger.error(f"중지 실패: {e}")

def render_dashboard():
    """메인 대시보드"""
    drain_event_queue()
    st.markdown("<h1 class='main-header'>🎥 KSI-CCTV 안전 관리 대시보드</h1>", unsafe_allow_html=True)
    pipeline = st.session_state.get('pipeline')
    status = pipeline.get_status() if pipeline else {"running": False, "cameras": {}}
    camera_count = len(status.get("cameras", {})) or len(st.session_state.get('camera_configs', []))
    connected_count = sum(1 for item in status.get("cameras", {}).values() if item.get("connected"))
    running_label = "실행 중" if status.get("running") else "대기"
    
    # KPI 메트릭
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown(f"""
        <div class='metric-card'>
            <h3>📹 카메라</h3>
            <h2>{camera_count}대</h2>
            <p>연결: <span class='status-online'>{connected_count}대</span></p>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        total_events = len(st.session_state.get('events', []))
        st.markdown(f"""
        <div class='metric-card'>
            <h3>🚨 알람</h3>
            <h2>{total_events}건</h2>
            <p>오늘 이벤트 수</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        st.markdown(f"""
        <div class='metric-card'>
            <h3>🤖 탐지 모델</h3>
            <h2>YOLO26</h2>
            <p>ONNX Runtime</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col4:
        st.markdown(f"""
        <div class='metric-card'>
            <h3>⚡ 상태</h3>
            <h2>{running_label}</h2>
            <p>파이프라인 상태</p>
        </div>
        """, unsafe_allow_html=True)
    
    st.divider()
    
    # 메인 컨텐츠
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
        "📹 실시간 스트림",
        "🔔 알람 로그",
        "📝 이벤트 로그",
        "📊 통계",
        "⚙️ 카메라 설정",
        "📦 모델 업로드",
        "🔍 감사 로그",
        "🔧 시스템 상태",
        "🔔 알림 설정",
    ])
    
    with tab1:
        render_stream_view()
    
    with tab2:
        render_alert_log()
    
    with tab3:
        render_event_log()
    
    with tab4:
        render_statistics()
    
    with tab5:
        render_camera_settings()

    with tab6:
        render_model_upload()
    
    with tab7:
        render_audit_log()
    
    with tab8:
        render_system_status()
    
    with tab9:
        render_alert_settings()

def render_stream_view():
    """실시간 스트림 뷰"""
    st.subheader("실시간 CCTV 스트림")
    
    if not st.session_state.get('is_running'):
        st.info("📢 왼쪽 사이드바에서 '시작' 버튼을 눌러 파이프라인을 실행하세요")
        
        # 카메라 설정 안내
        st.markdown("""
        ### 카메라 설정 방법
        
        1. `config/cameras.yaml` 또는 `config/cameras.json` 파일에 카메라 정보를 입력하세요
        2. RTSP URL 형식: `rtsp://[user]:[pw]@[ip]:[port]/[path]`
        3. '시작' 버튼을 눌러 실시간 감지를 시작하세요
        
        **지원 형식:**
        - RTSP (실시간 스트리밍)
        - HTTP/HTTPS 스트림
        - 로컬 비디오 파일 (.mp4, .avi 등)
        
        **참고:** 카메라가 연결되지 않은 경우 샘플 이미지로 대시보드를 테스트할 수 있습니다.
        """)
        return
    
    cameras = st.session_state.get('camera_configs', [])
    pipeline = st.session_state.get('pipeline')
    camera_status = pipeline.get_status().get("cameras", {}) if pipeline else {}

    if not cameras:
        st.warning("표시할 카메라 설정이 없습니다.")
        return
    
    # 2x2 또는 3열 그리드
    cols = st.columns(min(len(cameras), 3))
    
    for idx, cam in enumerate(cameras):
        with cols[idx % 3]:
            st.markdown(f"**{cam['name']}** | {cam['location']}")
            
            # 플레이스홀더 이미지 (실제 스트림 연결 시 대체)
            placeholder = np.zeros((240, 320, 3), dtype=np.uint8)
            placeholder[:] = [40, 40, 40]  # Dark gray
            
            # 상태 표시
            status = get_status_color(camera_status.get(cam['id'], {}).get("connected", False))
            st.caption(f"상태: {status}")

            frame = None
            if pipeline:
                capture = pipeline.camera_manager.get_camera(cam['id'])
                if capture:
                    frame = capture.get_latest_frame()

            if frame is not None:
                display_frame = frame.image
                detections = pipeline.get_latest_detections(cam['id']) if pipeline else []
                if detections and pipeline:
                    display_frame = pipeline.detector.draw_detections(display_frame, detections)
                image = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
                st.image(image, use_container_width=True)
                st.caption(f"최근 프레임: #{frame.frame_number} / {frame.timestamp:%H:%M:%S}")
                if detections:
                    st.caption("탐지: " + ", ".join(f"{d.class_name}({d.confidence:.2f})" for d in detections[:5]))
            else:
                st.image(placeholder, use_container_width=True)
            
            # 탐지 결과 요약
            if st.session_state.get('detection_results'):
                results = st.session_state.detection_results.get(cam['id'])
                if results:
                    st.json(results)

def render_alert_log():
    """알람 로그"""
    st.subheader("🔔 이벤트 / VLM 분석 로그")
    
    events = st.session_state.get('events', [])
    
    if not events:
        st.info("📭 아직 이벤트가 없습니다")
        st.markdown("""
        알람 이벤트 또는 VLM 분석 후보가 발생하면 이곳에 자동으로 기록됩니다.
        
        **감지 가능한 이벤트:**
        - 사람 감지 (person_detected)
        - 안전장비 미착용 (safety_gear_violation)
        - 화재 감지 (fire_detected)
        - 연기 감지 (smoke_detected)
        - 쓰러짐 감지 (fall_detected)
        - VLM 분석 후보 (vlm_analysis_candidate)
        """)
        return
    
    # 이벤트 필터
    col1, col2 = st.columns(2)
    with col1:
        event_types = list(set(e.event_type for e in events))
        selected_types = st.multiselect("이벤트 유형 필터", event_types, default=event_types)
    with col2:
        alert_levels = sorted(set(getattr(e, "alert_level", "low") for e in events))
        selected_levels = st.multiselect("alert_level 필터", alert_levels, default=alert_levels)
    
    # 시간 범위 필터
    if len(events) > 1:
        timestamps = [e.timestamp for e in events if hasattr(e, "timestamp")]
        if timestamps:
            min_ts = min(timestamps)
            max_ts = max(timestamps)
            from datetime import timedelta
            time_options = ["전체", "최근 1시간", "최근 24시간", "최근 7일"]
            selected_time = st.selectbox("시간 범위", time_options, index=0)
            
            if selected_time != "전체":
                now = datetime.now()
                if selected_time == "최근 1시간":
                    cutoff = now - timedelta(hours=1)
                elif selected_time == "최근 24시간":
                    cutoff = now - timedelta(days=1)
                elif selected_time == "최근 7일":
                    cutoff = now - timedelta(days=7)
                else:
                    cutoff = None
            else:
                cutoff = None
        else:
            cutoff = None
    else:
        cutoff = None
    
    # 이벤트 표시 (필터 적용)
    filtered_events = [e for e in events 
                       if e.event_type in selected_types 
                       and getattr(e, "alert_level", "low") in selected_levels]
    
    if cutoff:
        filtered_events = [e for e in filtered_events 
                           if hasattr(e, "timestamp") and e.timestamp >= cutoff]
    
    # 이벤트 내보내기
    if filtered_events:
        col_exp1, col_exp2, col_exp3 = st.columns(3)
        with col_exp1:
            if st.button("📥 CSV 내보내기", key="export_csv"):
                import csv
                import io
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(["timestamp", "camera_id", "event_type", "confidence", "alert_level", "description"])
                for event in filtered_events:
                    writer.writerow([
                        event.timestamp.strftime('%Y-%m-%d %H:%M:%S') if hasattr(event, "timestamp") else "",
                        event.camera_id,
                        event.event_type,
                        f"{event.confidence:.2f}",
                        getattr(event, "alert_level", "low"),
                        getattr(event, "description", "")
                    ])
                st.download_button(
                    label="CSV 다운로드",
                    data=output.getvalue(),
                    file_name=f"events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    key="download_csv"
                )
        with col_exp2:
            if st.button("📥 JSON 내보내기", key="export_json"):
                import json
                events_data = []
                for event in filtered_events:
                    events_data.append({
                        "timestamp": event.timestamp.isoformat() if hasattr(event, "timestamp") else "",
                        "camera_id": event.camera_id,
                        "event_type": event.event_type,
                        "confidence": event.confidence,
                        "alert_level": getattr(event, "alert_level", "low"),
                        "description": getattr(event, "description", ""),
                        "rule_name": getattr(event, "rule_name", ""),
                        "source_models": getattr(event, "source_models", []),
                    })
                json_str = json.dumps(events_data, ensure_ascii=False, indent=2)
                st.download_button(
                    label="JSON 다운로드",
                    data=json_str,
                    file_name=f"events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json",
                    key="download_json"
                )
        with col_exp3:
            st.caption(f"총 {len(filtered_events)}개 이벤트")
    
    st.divider()
    
    for event in filtered_events[-50:]:  # 최신 50개
        with st.container():
            is_vlm_candidate = event.event_type == "vlm_analysis_candidate"
            icon = "🤖" if is_vlm_candidate else "🚨"
            vlm_desc = getattr(event, "description", "") or ""
            vlm_risk = getattr(event, "vlm_risk_level", None)
            vlm_conf = getattr(event, "vlm_confidence", None)
            
            # Phase 5: alert_level 표시
            alert_level = getattr(event, "alert_level", "low")
            alert_icon = get_alert_level_icon(alert_level)
            alert_badge = f" | {alert_icon} **{alert_level.upper()}**" if not is_vlm_candidate else ""

            risk_badge = ""
            if vlm_risk:
                badge_map = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "⛔"}
                emoji = badge_map.get(vlm_risk, "⚪")
                risk_badge = f" | {emoji} VLM 위험도: **{vlm_risk.upper()}**"

            st.markdown(f"""
            <div class='alert-card'>
                <h4>{icon} {event.event_type}{alert_badge}{risk_badge}</h4>
                <p><strong>카메라:</strong> {event.camera_id}</p>
                <p><strong>시각:</strong> {event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p><strong>YOLO 신뢰도:</strong> {event.confidence:.2f} | **alert_level:** {alert_level}</p>
            </div>
            """, unsafe_allow_html=True)

            # Phase 5: policy metadata 표시
            source_models = getattr(event, "source_models", [])
            rule_name = getattr(event, "rule_name", "")
            vlm_needed = getattr(event, "vlm_needed", False)
            if source_models or rule_name or vlm_needed:
                meta_parts = []
                if source_models:
                    meta_parts.append(f"모델: {', '.join(source_models)}")
                if rule_name:
                    meta_parts.append(f"rule: {rule_name}")
                if vlm_needed:
                    meta_parts.append("VLM 필요")
                st.caption("📋 " + " | ".join(meta_parts))

            if vlm_desc:
                st.markdown(f"🤖 **VLM 설명:** {vlm_desc}")
                if vlm_conf is not None:
                    st.caption(f"VLM 신뢰도: {vlm_conf:.2f}")

            # 이미지 표시 (있는 경우)
            if event.image is not None:
                st.image(event.image, caption=f"{event.camera_id} - {event.timestamp}", width=400)

def render_event_log():
    """이벤트 로그 뷰어 — alerts.log 파일 기반 히스토리"""
    st.subheader("📝 이벤트 로그")
    
    log_path = Path("./logs/alerts.log")
    
    if not log_path.exists():
        st.info("📭 이벤트 로그 파일이 없습니다")
        st.markdown("""
        **알람 로그 위치:**
        - `logs/alerts.log` — JSONL 형식의 이벤트 기록
        - 카메라 이미지는 `data/captures/`에 저장
        """)
        return
    
    # 로그 파싱
    events = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    # Format: "timestamp | level | EVENT | {json}"
                    parts = line.split(" | ")
                    if len(parts) >= 3 and "EVENT" in parts[2]:
                        json_str = parts[2].split("EVENT | ", 1)[1] if "EVENT | " in parts[2] else parts[2]
                        event = json.loads(json_str)
                        events.append(event)
                except Exception:
                    continue
    except Exception as exc:
        st.error(f"로그 파일 읽기 실패: {exc}")
        return
    
    if not events:
        st.info("📭 파싱된 이벤트가 없습니다")
        return
    
    st.success(f"총 {len(events)}개 이벤트 로드")
    
    # 필터
    col1, col2 = st.columns(2)
    with col1:
        event_types = sorted(set(e.get("event_type", "unknown") for e in events))
        selected_types = st.multiselect("이벤트 유형", event_types, default=event_types)
    with col2:
        cameras = sorted(set(e.get("camera_id", "unknown") for e in events))
        selected_cameras = st.multiselect("카메라", cameras, default=cameras)
    
    # 시간 범위
    try:
        timestamps = [datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")) for e in events if "timestamp" in e]
        if timestamps:
            min_ts = min(timestamps)
            max_ts = max(timestamps)
            date_range = st.date_input("날짜 범위", value=(min_ts.date(), max_ts.date()))
            if isinstance(date_range, tuple) and len(date_range) == 2:
                start_date, end_date = date_range
                filtered = [e for e in events
                           if e.get("event_type") in selected_types
                           and e.get("camera_id") in selected_cameras
                           and "timestamp" in e
                           and start_date <= datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).date() <= end_date]
            else:
                filtered = [e for e in events if e.get("event_type") in selected_types and e.get("camera_id") in selected_cameras]
        else:
            filtered = [e for e in events if e.get("event_type") in selected_types and e.get("camera_id") in selected_cameras]
    except Exception:
        filtered = [e for e in events if e.get("event_type") in selected_types and e.get("camera_id") in selected_cameras]
    
    st.caption(f"필터링 결과: {len(filtered)}개 이벤트")
    
    # 표시
    for event in reversed(filtered[-100:]):  # 최신 100개
        with st.expander(f"🚨 {event.get('event_type', 'unknown')} | {event.get('camera_id', 'unknown')} | {event.get('timestamp', 'N/A')[:19]}"):
            st.json(event)


def render_statistics():
    """통계 화면 — 카메라별 상세 통계 (Phase 8 M5.3 D3)"""
    st.subheader("📊 탐지 통계")

    events = st.session_state.get('events', [])

    if not events:
        st.info("통계를 생성할 데이터가 없습니다")
        return

    from collections import Counter
    from datetime import timedelta

    # 전체 요약 메트릭
    total_events = len(events)
    cameras_seen = set(e.camera_id for e in events)
    alert_level_counts = Counter(getattr(e, "alert_level", "low") for e in events)
    critical_high = alert_level_counts.get("critical", 0) + alert_level_counts.get("high", 0)

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    with col_m1:
        st.metric("총 이벤트", total_events)
    with col_m2:
        st.metric("활성 카메라", len(cameras_seen))
    with col_m3:
        st.metric("High+Critical", critical_high)
    with col_m4:
        if events:
            last_event_time = max(e.timestamp for e in events)
            st.metric("최근 이벤트", last_event_time.strftime("%H:%M:%S"))

    st.divider()

    # 카메라 선택 필터
    selected_camera = st.selectbox(
        "📷 카메라 선택",
        ["전체"] + sorted(cameras_seen),
        key="stats_camera_filter"
    )

    if selected_camera == "전체":
        filtered = events
    else:
        filtered = [e for e in events if e.camera_id == selected_camera]

    if not filtered:
        st.info(f"카메라 '{selected_camera}'의 이벤트가 없습니다.")
        return

    # 카메라별 상세 통계
    st.markdown(f"### 📷 {selected_camera} 상세 통계 ({len(filtered)}건)")

    # 1. 이벤트 유형별 분포
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**이벤트 유형별 분포**")
        event_types = Counter(e.event_type for e in filtered)
        for event_type, count in event_types.most_common():
            st.progress(count / max(event_types.values()), text=f"{event_type}: {count}건")

    with col2:
        st.markdown("**alert_level 분포**")
        level_counts = Counter(getattr(e, "alert_level", "low") for e in filtered)
        level_icons = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "⛔"}
        for level in ["critical", "high", "medium", "low"]:
            count = level_counts.get(level, 0)
            if count > 0:
                st.progress(count / max(level_counts.values()),
                            text=f"{level_icons.get(level, '⚪')} {level}: {count}건")

    # 2. 시간대별 알람 (카메라 선택 적용)
    st.markdown("**시간대별 알람**")
    hours = [e.timestamp.hour for e in filtered]
    hour_counts = Counter(hours)
    st.bar_chart({f"{h:02d}시": hour_counts.get(h, 0) for h in range(24)})

    # 3. 최근 24시간 시간별 추이 (히트맵 대신 라인 차트)
    st.markdown("**최근 24시간 이벤트 추이**")
    now = max(e.timestamp for e in filtered) if filtered else None
    if now:
        last_24h = [e for e in filtered if (now - e.timestamp) <= timedelta(hours=24)]
        if last_24h:
            hour_24 = Counter(e.timestamp.replace(minute=0, second=0, microsecond=0) for e in last_24h)
            sorted_keys = sorted(hour_24.keys())
            st.line_chart({k.strftime("%m-%d %H시"): hour_24[k] for k in sorted_keys})

    # 4. 카메라별 비교 (전체 선택 시에만)
    if selected_camera == "전체" and len(cameras_seen) > 1:
        st.divider()
        st.markdown("**📊 카메라별 비교**")
        cam_counts = Counter(e.camera_id for e in events)
        cam_data = {
            "이벤트 수": {cam: cam_counts.get(cam, 0) for cam in cameras_seen}
        }
        for level in ["critical", "high", "medium", "low"]:
            level_cam = Counter(
                e.camera_id for e in events
                if getattr(e, "alert_level", "low") == level
            )
            if any(level_cam.values()):
                cam_data[f"{level_icons.get(level, '⚪')} {level}"] = {
                    cam: level_cam.get(cam, 0) for cam in cameras_seen
                }
        try:
            import pandas as pd
            df = pd.DataFrame(cam_data)
            st.bar_chart(df)
        except ImportError:
            st.info("💡 카메라별 비교 차트는 pandas 설치 시 표시됩니다.")
            for col_name, col_data in cam_data.items():
                st.markdown(f"**{col_name}**")
                for cam, cnt in col_data.items():
                    st.caption(f"  {cam}: {cnt}건")

    # 5. 카메라별 요약 테이블
    st.divider()
    st.markdown("**📋 카메라별 요약**")
    summary_rows = []
    for cam in sorted(cameras_seen):
        cam_events = [e for e in events if e.camera_id == cam]
        cam_levels = Counter(getattr(e, "alert_level", "low") for e in cam_events)
        last_time = max(e.timestamp for e in cam_events)
        summary_rows.append({
            "카메라": cam,
            "총 이벤트": len(cam_events),
            "Critical": cam_levels.get("critical", 0),
            "High": cam_levels.get("high", 0),
            "Medium": cam_levels.get("medium", 0),
            "Low": cam_levels.get("low", 0),
            "최근 이벤트": last_time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    try:
        import pandas as pd
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)
    except ImportError:
        for row in summary_rows:
            st.caption(
                f"**{row['카메라']}** — 총: {row['총 이벤트']}, "
                f"C/H/M/L: {row['Critical']}/{row['High']}/{row['Medium']}/{row['Low']}, "
                f"최근: {row['최근 이벤트']}"
            )


# ───────── Phase 5: Camera Settings Tab ─────────

def render_camera_settings():
    """카메라 설정 UI — 도메인/이벤트/모델 선택"""
    st.subheader("⚙️ 카메라 설정")

    # 정책 로드
    policies = load_event_policies()
    domains = get_available_domains(policies)
    model_status = get_model_status()

    if not domains:
        st.info("📭 event_policies.yaml에 도메인 프로필이 없습니다. `config/event_policies.yaml`을 확인하세요.")
        return

    # 도메인 선택
    selected_domain = st.selectbox(
        "도메인 템플릿",
        domains,
        index=domains.index(st.session_state.selected_domain) if st.session_state.selected_domain in domains else 0,
        help="선택한 도메인의 이벤트/모델 설정이 기본값으로 적용됩니다.",
    )
    st.session_state.selected_domain = selected_domain

    # 도메인별 이벤트 목록 표시
    events = get_events_for_domain(policies, selected_domain)
    if not events:
        st.warning(f"도메인 '{selected_domain}'에 활성화된 이벤트가 없습니다.")
        return

    st.markdown(f"**{selected_domain} 도메인 — 이벤트 설정**")

    # 체크박스 목록
    enabled_events = []
    for evt in events:
        checked = st.checkbox(
            f"{get_alert_level_icon(evt['alert_level'])} **{evt['name']}** "
            f"({evt['description']}) | alert={evt['alert_level']} | VLM={get_vlm_policy_label(evt['vlm'])}",
            value=evt["enabled"],
            key=f"event_{selected_domain}_{evt['name']}",
        )
        if checked:
            enabled_events.append(evt["name"])

    st.session_state.enabled_events = enabled_events if enabled_events else None

    # 모델 상태 표시
    st.divider()
    st.markdown("**📦 모델 상태**")

    source = model_status.get("source", "unknown")
    if source == "registry":
        active = model_status.get("active_models", [])
        failed = model_status.get("failed_models", {})
        col1, col2 = st.columns(2)
        with col1:
            st.success(f"활성 모델: {', '.join(active) if active else '없음'}")
        with col2:
            if failed:
                for name, err in failed.items():
                    st.error(f"{name}: {err}")
            else:
                st.caption("실패한 모델 없음")
    elif source == "config":
        models = model_status.get("models", {})
        active_count = sum(1 for m in models.values() if m.get("status") == "active")
        disabled_count = len(models) - active_count
        st.info(f"config 기반: 활성 {active_count} / 비활성 {disabled_count}")
    else:
        st.warning("모델 상태를 확인할 수 없습니다.")

    # 카메라별 프로필 적용 안내
    st.divider()
    pipeline = st.session_state.get('pipeline')
    if pipeline and hasattr(pipeline, 'set_camera_profile'):
        for cam in st.session_state.get('camera_configs', []):
            cam_id = cam.get("id", "unknown")
            profile = {
                "domain": selected_domain,
                "events": enabled_events if enabled_events else None,
            }
            pipeline.set_camera_profile(cam_id, profile)

        st.success(f"✅ 카메라 프로필 적용 완료: 도메인='{selected_domain}', 이벤트={len(enabled_events)}개")
    elif not st.session_state.get('is_running'):
        st.info("💡 파이프라인을 시작하면 선택한 설정이 각 카메라에 자동으로 적용됩니다.")


# ───────── Phase 6: User Model Upload Tab ─────────

def render_model_upload():
    """관리자 전용 사용자 ONNX 모델 업로드/관리 UI."""
    st.subheader("📦 사용자 ONNX 모델 업로드")

    if not HAS_UPLOAD_VALIDATOR:
        st.error("모델 업로드 검증 모듈을 로드할 수 없습니다.")
        return

    if not is_admin_model_management_allowed():
        st.warning(
            "모델 업로드/활성화/삭제는 관리자 비밀번호가 설정된 인증 세션에서만 가능합니다. "
            "`KSI_CCTV_DASHBOARD_PASSWORD`를 `change-me`가 아닌 값으로 설정한 뒤 로그인하세요."
        )
        return

    st.markdown(
        "**보안 정책:** ONNX(`.onnx`)만 허용, 업로드 모델은 격리 저장 후 smoke test를 통과해야 하며 "
        "`enabled: false` 상태로만 등록됩니다."
    )

    with st.form("upload_model_form", clear_on_submit=True):
        uploaded = st.file_uploader("ONNX 모델 파일", type=["onnx"])
        model_name = st.text_input("모델 표시 이름", value="uploaded safety model")
        classes_text = st.text_input("클래스 목록 (쉼표 구분)", value="")
        col1, col2 = st.columns(2)
        with col1:
            input_h = st.number_input("입력 높이", min_value=32, max_value=4096, value=640, step=32)
        with col2:
            input_w = st.number_input("입력 너비", min_value=32, max_value=4096, value=640, step=32)
        submitted = st.form_submit_button("검증 후 disabled 등록")

    if submitted:
        if uploaded is None:
            st.error("업로드할 ONNX 파일을 선택하세요.")
        else:
            classes = [item.strip() for item in classes_text.split(",") if item.strip()]
            result = upload_model(
                uploaded.getvalue(),
                uploaded.name,
                metadata={
                    "model_name": model_name,
                    "classes": classes,
                    "input_size": [int(input_h), int(input_w)],
                },
            )
            if result.success:
                audit_model_action(
                    "model_upload_success",
                    model_id=result.model_id,
                    profile_name=result.profile_name,
                    original_filename=uploaded.name,
                )
                st.success(f"업로드/검증 완료: {result.profile_name} (disabled)")
            else:
                audit_model_action("model_upload_failure", original_filename=uploaded.name, error=result.error)
                st.error(result.error)

    st.divider()
    st.markdown("**업로드된 모델**")
    models = get_uploaded_models()
    if not models:
        st.info("아직 업로드된 모델이 없습니다.")
        return

    for item in models:
        model_id = item.get("model_id", "")
        profile_name = item.get("profile_name", f"uploaded_{model_id}")
        enabled = bool(item.get("enabled", False))
        with st.container():
            st.markdown(f"- **{profile_name}** / `{item.get('original_filename', '')}` / enabled={enabled}")
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                if st.button("활성화", key=f"activate_{model_id}", disabled=enabled):
                    ok, err = activate_uploaded_model(model_id)
                    audit_model_action("model_activate" if ok else "model_activate_failure", model_id=model_id, error=err)
                    if ok:
                        st.success("활성화했습니다. 파이프라인 재시작 후 반영됩니다.")
                        st.rerun()
                    else:
                        st.error(err)
            with col_b:
                if st.button("비활성화", key=f"deactivate_{model_id}", disabled=not enabled):
                    ok, err = deactivate_uploaded_model(model_id)
                    audit_model_action("model_deactivate" if ok else "model_deactivate_failure", model_id=model_id, error=err)
                    if ok:
                        st.success("비활성화했습니다. 파이프라인 재시작 후 반영됩니다.")
                        st.rerun()
                    else:
                        st.error(err)
            with col_c:
                confirm_delete = st.checkbox("삭제 확인", key=f"confirm_delete_{model_id}")
                if st.button("삭제", key=f"delete_{model_id}", disabled=not confirm_delete):
                    ok, err = delete_uploaded_model(model_id)
                    audit_model_action("model_delete" if ok else "model_delete_failure", model_id=model_id, error=err)
                    if ok:
                        st.success("삭제했습니다.")
                        st.rerun()
                    else:
                        st.error(err)

# ───────── Audit Log Viewer Tab ─────────

def render_audit_log():
    """감사 로그 뷰어 — 로그인/모델 관리/시스템 이벤트 조회"""
    st.subheader("🔍 감사 로그")
    
    logs = _load_audit_log()
    
    if not logs:
        st.info("📭 감사 로그가 없습니다")
        st.markdown("""
        **기록되는 이벤트:**
        - 로그인 성공/실패
        - 모델 업로드/활성화/비활성화/삭제
        - 설정 변경
        """)
        return
    
    # 필터
    event_types = sorted(set(log.get("event", "unknown") for log in logs))
    selected_types = st.multiselect("이벤트 유형 필터", event_types, default=event_types)
    
    filtered = [log for log in logs if log.get("event", "") in selected_types]
    
    st.caption(f"총 {len(filtered)}개 이벤트 (전체 {len(logs)}개)")
    
    # 테이블 형태로 표시
    import pandas as pd
    try:
        df = pd.DataFrame(filtered)
        if not df.empty and "timestamp" in df.columns:
            df = df.sort_values("timestamp", ascending=False)
        st.dataframe(df, use_container_width=True)
    except Exception:
        # pandas 없으면 수동 표시
        for log in reversed(filtered[-100:]):  # 최신 100개
            ts = log.get("timestamp", "N/A")
            event = log.get("event", "unknown")
            
            # 이벤트별 아이콘
            icons = {
                "login_success": "✅",
                "login_failure": "❌",
                "model_upload_success": "📤",
                "model_upload_failure": "⚠️",
                "model_activate": "▶️",
                "model_deactivate": "⏸️",
                "model_delete": "🗑️",
            }
            icon = icons.get(event, "📝")
            
            with st.expander(f"{icon} {event} — {ts}"):
                st.json(log)


# ───────── System Status Tab ─────────

def render_system_status():
    """시스템 상태 위젯 — CPU/메모리/디스크, ONNX Runtime 정보"""
    st.subheader("🔧 시스템 상태")
    
    # ONNX Runtime 정보
    st.markdown("**🧠 ONNX Runtime**")
    try:
        import onnxruntime as ort
        providers = ort.get_available_providers()
        st.success(f"사용 가능한 Provider: {', '.join(providers)}")
        
        # 현재 로드된 모델의 provider
        pipeline = st.session_state.get('pipeline')
        if pipeline and hasattr(pipeline, 'detector') and pipeline.detector:
            used_providers = pipeline.detector.session.get_providers() if pipeline.detector.session else []
            st.info(f"현재 사용 중인 Provider: {', '.join(used_providers)}")
    except Exception as exc:
        st.error(f"ONNX Runtime 정보 조회 실패: {exc}")
    
    st.divider()
    
    # 시스템 리소스 (psutil 사용)
    st.markdown("**💻 시스템 리소스**")
    try:
        import psutil
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            cpu_percent = psutil.cpu_percent(interval=1)
            st.metric("CPU 사용량", f"{cpu_percent:.1f}%")
            st.progress(min(cpu_percent / 100, 1.0))
        
        with col2:
            memory = psutil.virtual_memory()
            st.metric("메모리 사용량", f"{memory.percent:.1f}%")
            st.progress(memory.percent / 100)
            st.caption(f"{memory.used / (1024**3):.1f} GB / {memory.total / (1024**3):.1f} GB")
        
        with col3:
            disk = psutil.disk_usage("/")
            disk_percent = disk.percent
            st.metric("디스크 사용량", f"{disk_percent:.1f}%")
            st.progress(disk_percent / 100)
            st.caption(f"{disk.used / (1024**3):.1f} GB / {disk.total / (1024**3):.1f} GB")
        
        # 추가 정보
        st.divider()
        col4, col5 = st.columns(2)
        with col4:
            st.markdown("**프로세스 정보**")
            st.caption(f"PID: {os.getpid()}")
            st.caption(f"작업 디렉토리: {os.getcwd()}")
        with col5:
            st.markdown("**네트워크**")
            net_io = psutil.net_io_counters()
            st.caption(f"수신: {net_io.bytes_recv / (1024**2):.1f} MB")
            st.caption(f"송신: {net_io.bytes_sent / (1024**2):.1f} MB")
            
    except ImportError:
        st.info("💡 상세 시스템 정보를 보려면 `pip install psutil`을 실행하세요.")
        st.markdown("""
        **기본 정보:**
        - PID: {}
        - 작업 디렉토리: {}
        """.format(os.getpid(), os.getcwd()))
    except Exception as exc:
        st.error(f"시스템 정보 조회 실패: {exc}")
    
    st.divider()
    
    # VLM 상태 (Phase 9 M1)
    st.markdown("**🧠 VLM (Ollama) 상태**")
    try:
        pipeline = st.session_state.get('pipeline')
        vlm_provider = pipeline.vlm_provider if pipeline and hasattr(pipeline, "vlm_provider") else None
        if vlm_provider is None:
            st.caption("VLM이 비활성화되어 있습니다.")
        else:
            available = vlm_provider.is_available()
            if available:
                st.success(f"🟢 VLM 연결됨: {vlm_provider.model}")
            else:
                st.warning(f"🔴 VLM 오프라인: {vlm_provider.model}")

            # 메트릭
            if hasattr(vlm_provider, "get_metrics"):
                m = vlm_provider.get_metrics()
                col_v1, col_v2, col_v3, col_v4 = st.columns(4)
                with col_v1:
                    st.metric("총 호출", m["total_calls"])
                with col_v2:
                    st.metric("성공/실패", f"{m['success_count']} / {m['failure_count']}")
                with col_v3:
                    st.metric("평균 레이턴시", f"{m['avg_latency_ms']:.0f}ms")
                with col_v4:
                    st.metric("최대 레이턴시", f"{m['max_latency_ms']:.0f}ms")
                st.caption(f"성공률: {m['success_rate']*100:.1f}% | 마지막: {m['last_risk_level'] or 'N/A'}")
                if m["last_error"]:
                    st.caption(f"마지막 에러: {m['last_error']}")

            # Cache stats
            if pipeline and hasattr(pipeline, "get_vlm_cache_stats"):
                cs = pipeline.get_vlm_cache_stats()
                total = cs["hits"] + cs["misses"]
                if total > 0:
                    hit_rate = cs["hits"] / total * 100
                    st.caption(
                        f"VLM cache: hits={cs['hits']}, misses={cs['misses']}, "
                        f"bypasses={cs['bypasses']}, hit_rate={hit_rate:.1f}%"
                    )
                if st.button("VLM 메트릭 / cache 리셋", key="reset_vlm_metrics"):
                    if hasattr(vlm_provider, "reset_metrics"):
                        vlm_provider.reset_metrics()
                    if pipeline and hasattr(pipeline, "reset_vlm_cache"):
                        pipeline.reset_vlm_cache()
                    st.rerun()
    except Exception as exc:
        st.error(f"VLM 상태 조회 실패: {exc}")

    st.divider()
    
    # SMTP 상태 및 테스트
    st.markdown("**📧 SMTP 알림**")
    try:
        smtp_host = os.environ.get("KSI_CCTV_SMTP_HOST", "").strip()
        if smtp_host:
            st.success(f"SMTP 설정됨: {smtp_host}")
            smtp_user = os.environ.get("KSI_CCTV_SMTP_USER", "")
            recipients = os.environ.get("KSI_CCTV_SMTP_RECIPIENTS", "")
            st.caption(f"사용자: {smtp_user}")
            st.caption(f"수신자: {recipients}")
            
            if st.button("📧 SMTP 테스트 발송", key="smtp_test"):
                with st.spinner("SMTP 테스트 중..."):
                    try:
                        import smtplib
                        from email.mime.text import MIMEText
                        
                        msg = MIMEText("KSI-CCTV SMTP 연결 테스트입니다.", "plain", "utf-8")
                        msg["Subject"] = "[KSI-CCTV] SMTP 테스트"
                        msg["From"] = smtp_user
                        msg["To"] = recipients
                        
                        with smtplib.SMTP(smtp_host, int(os.environ.get("KSI_CCTV_SMTP_PORT", "587"))) as server:
                            server.starttls()
                            password = os.environ.get("KSI_CCTV_SMTP_PASSWORD", "")
                            if smtp_user and password:
                                server.login(smtp_user, password)
                            server.send_message(msg)
                        st.success("✅ SMTP 테스트 이메일 발송 성공")
                    except Exception as exc:
                        st.error(f"❌ SMTP 테스트 실패: {exc}")
        else:
            st.info("SMTP 미설정 — `.env`에 `KSI_CCTV_SMTP_HOST`를 설정하세요")
    except Exception as exc:
        st.error(f"SMTP 설정 확인 실패: {exc}")
    
    st.divider()
    
    # 모델 상세 상태
    st.markdown("**📦 모델 상세 상태**")
    try:
        if HAS_MODEL_REGISTRY:
            registry = MultiModelRegistry.from_config("./config/model_profiles.yaml")
            status = registry.to_status_dict()
            
            for name, info in status.items():
                col1, col2 = st.columns([1, 3])
                with col1:
                    if info.get("loaded"):
                        st.success(f"✅ {name}")
                    elif info.get("enabled"):
                        st.error(f"❌ {name}")
                    else:
                        st.caption(f"⏸️ {name} (disabled)")
                with col2:
                    st.caption(f"Type: {info.get('type', 'N/A')}")
                    st.caption(f"Path: {info.get('model_path', 'N/A')}")
                    if info.get("class_map"):
                        st.caption(f"Class Map: {info['class_map']}")
                    if info.get("error"):
                        st.error(f"오류: {info['error']}")
        else:
            st.info("Model registry not available")
    except Exception as exc:
        st.error(f"모델 상태 조회 실패: {exc}")


def _load_alert_preferences() -> Dict[str, Any]:
    """`config/alert_preferences.yaml` 로드 — 실패 시 기본값 반환"""
    prefs_path = Path("./config/alert_preferences.yaml")
    if not prefs_path.exists():
        return {"email_alert_level_threshold": "medium", "per_event_email_enabled": {}}
    try:
        return ConfigLoader.load_yaml(str(prefs_path))
    except Exception:
        return {"email_alert_level_threshold": "medium", "per_event_email_enabled": {}}


def _save_alert_preferences(prefs: Dict[str, Any]) -> None:
    """`config/alert_preferences.yaml` 저장"""
    prefs_path = Path("./config/alert_preferences.yaml")
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml as _yaml
        with open(prefs_path, "w", encoding="utf-8") as f:
            _yaml.safe_dump(prefs, f, allow_unicode=True, default_flow_style=False)
    except Exception as exc:
        logger.error(f"알림 설정 저장 실패: {exc}")
        raise


def render_alert_settings():
    """알림 설정 UI — 이벤트 타입별 on/off, alert_level 임계값"""
    st.subheader("🔔 알림 설정")

    # Load current alert config
    policies = load_event_policies()
    domains = get_available_domains(policies)

    # Load persistent preferences
    prefs = _load_alert_preferences()
    if "email_alert_level_threshold" not in st.session_state:
        st.session_state.email_alert_level_threshold = prefs.get(
            "email_alert_level_threshold", "medium"
        )
    if "per_event_email_enabled" not in st.session_state:
        st.session_state.per_event_email_enabled = prefs.get("per_event_email_enabled", {})

    # Email threshold
    st.markdown("**📧 이메일 발송 설정**")
    current_threshold = st.session_state.get("email_alert_level_threshold", "medium")
    threshold = st.select_slider(
        "이메일 발송 alert_level 임계값",
        options=["low", "medium", "high", "critical"],
        value=current_threshold,
    )
    st.session_state.email_alert_level_threshold = threshold
    st.caption(f"`{threshold}` 이상의 이벤트만 이메일로 발송됩니다.")

    # Per-event-type email toggle
    st.divider()
    st.markdown("**📋 이벤트 타입별 이메일 발송**")

    selected_domain = st.selectbox(
        "도메인 선택",
        domains if domains else ["construction"],
        key="alert_settings_domain"
    )
    events = get_events_for_domain(policies, selected_domain)

    if not events:
        st.info("설정된 이벤트가 없습니다.")
        return

    # Show toggles in a compact layout
    cols = st.columns(2)
    for idx, evt in enumerate(events):
        with cols[idx % 2]:
            event_name = evt["name"]
            default_val = st.session_state.per_event_email_enabled.get(event_name, True)
            enabled = st.checkbox(
                f"{get_alert_level_icon(evt['alert_level'])} {event_name}",
                value=default_val,
                key=f"email_toggle_{event_name}"
            )
            st.session_state.per_event_email_enabled[event_name] = enabled
            st.caption(f"설명: {evt['description']} | VLM: {get_vlm_policy_label(evt['vlm'])}")

    # Summary
    st.divider()
    enabled_count = sum(1 for v in st.session_state.per_event_email_enabled.values() if v)
    total_count = len(st.session_state.per_event_email_enabled)
    st.info(f"이메일 발송 이벤트: {enabled_count} / {total_count}")

    # Save button
    if st.button("💾 설정 저장", key="save_alert_settings"):
        try:
            _save_alert_preferences({
                "email_alert_level_threshold": st.session_state.email_alert_level_threshold,
                "per_event_email_enabled": st.session_state.per_event_email_enabled,
            })
            _write_audit_log({
                "timestamp": datetime.utcnow().isoformat(),
                "event": "alert_preferences_saved",
                "threshold": st.session_state.email_alert_level_threshold,
                "enabled_count": enabled_count,
            })
            st.success("✅ 설정이 저장되었습니다. 파이프라인 재시작 시 적용됩니다.")
        except Exception as exc:
            st.error(f"저장 실패: {exc}")

    if st.button("🔄 기본값으로 되돌리기", key="reset_alert_settings"):
        st.session_state.email_alert_level_threshold = "medium"
        st.session_state.per_event_email_enabled = {}
        st.info("세션 설정이 기본값으로 초기화되었습니다. '설정 저장'으로 영구 저장하세요.")


# 메인
def main():
    init_session_state()
    if not require_dashboard_auth():
        return
    render_sidebar()
    render_dashboard()
    
    # Phase 8 M5.1: Auto-refresh at end of main loop
    if st.session_state.get("auto_refresh_enabled", True):
        interval = st.session_state.get("auto_refresh_interval", 5)
        time.sleep(interval)
        st.rerun()

if __name__ == "__main__":
    main()
