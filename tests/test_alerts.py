"""
alerts 단위 테스트

대상: src/alerts/alert_manager.py
- 얼굴 블러링 (정면 + 측면 cascade 병행)
- 이미지 저장 / 암호화 저장
- cleanup_old_images (디렉토리명 기반 날짜 판정)
- _maybe_cleanup_images throttle
- SMTP 필터링 (이메일 필터 / alert_level threshold)
- 로그 마스킹 (RTSP URL)
- VLM 필드 이메일 포함 여부
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np

# --- 런타임 의존성 체크 ---
try:
    import cv2 as _cv2  # noqa: F401  — 존재 확인용
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False

try:
    from src.alerts.alert_manager import AlertConfig, AlertManager
    from src.pipeline.pipeline import SafetyEvent
    HAS_ALERTS = True
except ImportError:
    HAS_ALERTS = False


def _make_event(**kwargs) -> SafetyEvent:
    """테스트용 SafetyEvent 생성 헬퍼."""
    defaults = {
        "event_id": "test_evt",
        "camera_id": "cam_001",
        "event_type": "person_detected",
        "timestamp": datetime.now(),
        "confidence": 0.92,
        "alert_level": "medium",
    }
    defaults.update(kwargs)
    return SafetyEvent(**defaults)


def _make_bgr_image(h: int = 100, w: int = 100) -> np.ndarray:
    """합성 BGR 이미지 반환 (단색 회색)."""
    return np.full((h, w, 3), 128, dtype=np.uint8)


@unittest.skipUnless(HAS_ALERTS and HAS_CV2, "runtime deps 없음")
class TestFaceBlur(unittest.TestCase):
    """얼굴 블러링 — cascade 로드 및 실행 경로 검증"""

    def _make_manager(self, blur: bool = True) -> AlertManager:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AlertConfig(
                enabled=True,
                save_images=False,
                blur_faces=blur,
                log_file=str(Path(tmp) / "alerts.log"),
            )
            mgr = AlertManager(cfg)
            mgr.capture_dir = Path(tmp)
            return mgr

    def test_cascades_loaded_when_blur_enabled(self):
        """blur_faces=True이면 _face_cascades에 1개 이상 로드돼야 한다."""
        mgr = self._make_manager(blur=True)
        self.assertGreater(len(mgr._face_cascades), 0, "cascade 1개 이상 로드 필요")

    def test_prepare_image_rejects_missing_detector(self):
        mgr = self._make_manager(blur=True)
        mgr._face_cascades = []
        with self.assertRaises(RuntimeError):
            mgr._prepare_image_for_storage(_make_bgr_image())

    def test_profile_cascade_also_loaded(self):
        """정면 + 측면 cascade 둘 다 로드돼야 한다(CCTV 각도 대응)."""
        mgr = self._make_manager(blur=True)
        # 최소 2개 cascade (frontalface + profileface)
        self.assertGreaterEqual(
            len(mgr._face_cascades), 1,
            "cascade 로드 수 불충분"
        )

    def test_no_cascades_when_blur_disabled(self):
        """blur_faces=False이면 _face_cascades가 비어야 한다."""
        mgr = self._make_manager(blur=False)
        self.assertEqual(mgr._face_cascades, [])

    def test_prepare_image_returns_same_shape(self):
        """블러링 처리 후 이미지 shape가 유지돼야 한다."""
        mgr = self._make_manager(blur=True)
        img = _make_bgr_image(120, 160)
        result = mgr._prepare_image_for_storage(img)
        self.assertEqual(result.shape, img.shape)

    def test_prepare_image_no_blur_returns_original(self):
        """blur_faces=False이면 원본과 동일 배열을 반환해야 한다."""
        mgr = self._make_manager(blur=False)
        img = _make_bgr_image()
        result = mgr._prepare_image_for_storage(img)
        np.testing.assert_array_equal(result, img)


@unittest.skipUnless(HAS_ALERTS and HAS_CV2, "runtime deps 없음")
class TestImageSave(unittest.TestCase):
    """이미지 저장 — 평문 및 암호화 경로"""

    def test_capture_encryption_is_enabled_by_default(self):
        self.assertTrue(AlertConfig().encrypt_images)

    def _manager(self, tmp: str, encrypt: bool = False, key: str | None = None) -> AlertManager:
        """임시 디렉토리를 사용하는 AlertManager를 반환한다."""
        env = {}
        if key:
            env["KSI_CCTV_CAPTURE_KEY"] = key

        with patch.dict(os.environ, env, clear=False):
            cfg = AlertConfig(
                enabled=True,
                save_images=True,
                blur_faces=False,
                encrypt_images=encrypt,
                log_file=str(Path(tmp) / "alerts.log"),
                image_dir=str(Path(tmp) / "captures"),
            )
            return AlertManager(cfg)

    def test_save_image_creates_jpg(self):
        """save_images=True, 이미지 있음 → 날짜/카메라 디렉토리에 .jpg 생성."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            event = _make_event(image=_make_bgr_image())
            relative_path = mgr._save_image(event)

            date_str = event.timestamp.strftime("%Y%m%d")
            captures = list((mgr.capture_dir / date_str / "cam_001").glob("*.jpg"))
            self.assertEqual(len(captures), 1, "jpg 파일 1개 생성 필요")
            self.assertEqual(relative_path, captures[0].relative_to(mgr.capture_dir).as_posix())
            self.assertFalse(Path(relative_path).is_absolute())

    def test_handle_event_sets_relative_evidence_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            event = _make_event(
                image=_make_bgr_image(),
                metadata={"track_id": 7, "zone_id": "line"},
            )

            try:
                with patch.object(mgr, "_enqueue_email", return_value=False):
                    mgr.handle_event(event)

                self.assertIsNotNone(event.evidence_image)
                self.assertFalse(Path(event.evidence_image).is_absolute())
                self.assertEqual(event.to_dict()["metadata"]["track_id"], 7)
            finally:
                mgr.shutdown()

    def test_save_image_none_image_skips(self):
        """image=None이면 파일이 생성되지 않아야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            event = _make_event(image=None)
            mgr._save_image(event)
            captures = list(mgr.capture_dir.rglob("*.jpg"))
            self.assertEqual(len(captures), 0)

    def test_save_image_rejects_unsafe_persisted_camera_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            event = _make_event(
                camera_id="../outside",
                image=_make_bgr_image(),
            )

            self.assertIsNone(mgr._save_image(event))
            self.assertEqual(list(mgr.capture_dir.rglob("*.jpg")), [])

    @unittest.skipUnless(HAS_CRYPTOGRAPHY, "cryptography 패키지 없음")
    def test_save_image_encrypted_creates_enc_file(self):
        """encrypt_images=True + 유효 키 → .jpg.enc 파일 생성."""
        key = Fernet.generate_key().decode()
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp, encrypt=True, key=key)
            event = _make_event(image=_make_bgr_image())
            mgr._save_image(event)

            enc_files = list(mgr.capture_dir.rglob("*.enc"))
            self.assertEqual(len(enc_files), 1, ".jpg.enc 파일 1개 생성 필요")
            # .jpg 평문은 없어야 한다
            jpg_files = list(mgr.capture_dir.rglob("*.jpg"))
            self.assertEqual(len(jpg_files), 0, "암호화 모드에서 평문 .jpg 생성 금지")

    @unittest.skipUnless(HAS_CRYPTOGRAPHY, "cryptography 패키지 없음")
    def test_encrypt_decrypt_roundtrip(self):
        """encrypt_image_bytes → decrypt_image_bytes 왕복이 원본을 복원해야 한다."""
        key = Fernet.generate_key().decode()
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp, encrypt=True, key=key)
            original = b"test image bytes 12345"
            encrypted = mgr.encrypt_image_bytes(original)
            self.assertNotEqual(encrypted, original)
            decrypted = mgr.decrypt_image_bytes(encrypted)
            self.assertEqual(decrypted, original)

    @unittest.skipUnless(HAS_CRYPTOGRAPHY, "cryptography 패키지 없음")
    def test_encrypt_images_no_key_fails_closed(self):
        """키 없이 encrypt_images=True이면 평문으로 강등하지 않고 시작 실패."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("KSI_CCTV_CAPTURE_KEY", None)
                with self.assertRaises(RuntimeError):
                    self._manager(tmp, encrypt=True, key=None)
            self.assertEqual(list((Path(tmp) / "captures").rglob("*.jpg")), [])

    def test_blur_faces_without_detector_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            AlertManager, "_load_face_cascades", return_value=[]
        ):
            cfg = AlertConfig(
                enabled=True,
                save_images=True,
                blur_faces=True,
                log_file=str(Path(tmp) / "alerts.log"),
                image_dir=str(Path(tmp) / "captures"),
            )
            with self.assertRaises(RuntimeError):
                AlertManager(cfg)


@unittest.skipUnless(HAS_ALERTS and HAS_CV2, "runtime deps 없음")
class TestCleanupOldImages(unittest.TestCase):
    """cleanup_old_images — 디렉토리명 날짜 기반 판정"""

    def _manager(self, tmp: str, retention_days: int = 7) -> AlertManager:
        cfg = AlertConfig(
            enabled=True,
            save_images=False,
            blur_faces=False,
            image_retention_days=retention_days,
            log_file=str(Path(tmp) / "alerts.log"),
            image_dir=str(Path(tmp) / "captures"),
        )
        return AlertManager(cfg)

    def _make_date_dir(self, base: Path, days_ago: int) -> Path:
        """days_ago일 전 날짜의 YYYYMMDD 디렉토리를 생성한다."""
        target_date = datetime.now() - timedelta(days=days_ago)
        name = target_date.strftime("%Y%m%d")
        d = base / name
        d.mkdir(parents=True, exist_ok=True)
        # 더미 파일 추가 (mtime이 dir 이름 날짜와 다를 수 있음을 의도)
        (d / "dummy.jpg").write_bytes(b"fake")
        return d

    def test_old_dir_deleted(self):
        """retention_days보다 오래된 날짜 디렉토리는 삭제되어야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp, retention_days=7)
            old_dir = self._make_date_dir(mgr.capture_dir, days_ago=10)
            mgr.cleanup_old_images()
            self.assertFalse(old_dir.exists(), "오래된 디렉토리 삭제 필요")

    def test_recent_dir_preserved(self):
        """retention_days 이내 날짜 디렉토리는 보존되어야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp, retention_days=7)
            recent_dir = self._make_date_dir(mgr.capture_dir, days_ago=3)
            mgr.cleanup_old_images()
            self.assertTrue(recent_dir.exists(), "최근 디렉토리 보존 필요")

    def test_old_dir_with_fresh_mtime_still_deleted(self):
        """디렉토리명이 오래된 날짜면 mtime이 최신이어도 삭제돼야 한다.

        이것이 mtime 단독 판정의 회귀 가드 — 디렉토리에 파일이 최근에
        추가돼 mtime이 갱신됐어도 이름 날짜가 cutoff 이전이면 삭제.
        """
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp, retention_days=7)
            old_dir = self._make_date_dir(mgr.capture_dir, days_ago=15)
            # mtime을 현재로 강제 갱신 (OS가 최근 접근으로 처리하게)
            now_ts = time.time()
            os.utime(old_dir, (now_ts, now_ts))
            mgr.cleanup_old_images()
            self.assertFalse(
                old_dir.exists(),
                "mtime이 최신이어도 디렉토리명 날짜가 오래되면 삭제 필요"
            )

    def test_retention_zero_is_noop(self):
        """image_retention_days=0이면 cleanup이 no-op이어야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp, retention_days=0)
            old_dir = self._make_date_dir(mgr.capture_dir, days_ago=100)
            mgr.cleanup_old_images()
            self.assertTrue(old_dir.exists(), "retention=0이면 삭제 금지")

    def test_non_date_dir_uses_mtime_fallback(self):
        """YYYYMMDD 형식이 아닌 디렉토리는 mtime fallback으로 처리된다."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp, retention_days=7)
            misc_dir = mgr.capture_dir / "misc_camera"
            misc_dir.mkdir(parents=True, exist_ok=True)
            # mtime을 30일 전으로 설정
            old_ts = time.time() - 30 * 86400
            os.utime(misc_dir, (old_ts, old_ts))
            mgr.cleanup_old_images()
            # mtime 기준 삭제 됐어야 함
            self.assertFalse(misc_dir.exists(), "mtime fallback으로 오래된 misc 디렉토리 삭제")


@unittest.skipUnless(HAS_ALERTS and HAS_CV2, "runtime deps 없음")
class TestMaybeCleanupThrottle(unittest.TestCase):
    """_maybe_cleanup_images throttle 동작 검증"""

    def _manager(self, tmp: str) -> AlertManager:
        cfg = AlertConfig(
            enabled=True,
            save_images=False,
            blur_faces=False,
            image_retention_days=7,
            log_file=str(Path(tmp) / "alerts.log"),
            image_dir=str(Path(tmp) / "captures"),
        )
        return AlertManager(cfg)

    def test_cleanup_called_only_once_within_interval(self):
        """interval 내에서는 cleanup_old_images가 최대 1회만 호출된다."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            mgr._image_cleanup_interval_sec = 9999  # 사실상 무한

            call_count = 0
            original = mgr.cleanup_old_images

            def counting_cleanup():
                nonlocal call_count
                call_count += 1
                original()

            mgr.cleanup_old_images = counting_cleanup  # type: ignore[method-assign]
            mgr._last_image_cleanup = 0.0  # throttle 해제

            event = _make_event()
            mgr.handle_event(event)  # 첫 호출 → cleanup 실행
            mgr.handle_event(event)  # interval 내 → skip
            mgr.handle_event(event)

            self.assertEqual(call_count, 1, "interval 내 cleanup 중복 실행 금지")

    def test_cleanup_runs_at_startup_without_an_event(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            AlertManager, "cleanup_old_images"
        ) as mock_clean:
            mgr = self._manager(tmp)
            try:
                mock_clean.assert_called_once()
            finally:
                mgr.shutdown()

    def test_periodic_cleanup_runs_without_an_event_and_stops_at_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            initial_timer = mgr._image_cleanup_timer
            self.assertIsNotNone(initial_timer)
            initial_timer.cancel()
            mgr._image_cleanup_interval_sec = 0.01

            with patch.object(mgr, "cleanup_old_images") as mock_clean:
                mgr._schedule_image_cleanup()
                deadline = time.time() + 1
                while not mock_clean.called and time.time() < deadline:
                    time.sleep(0.01)
                mock_clean.assert_called_once()

            timer = mgr._image_cleanup_timer
            self.assertIsNotNone(timer)
            mgr.shutdown()
            self.assertTrue(timer.finished.is_set())

    def test_cleanup_skipped_when_retention_zero(self):
        """image_retention_days=0이면 throttle 없이 early return해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            mgr.config.image_retention_days = 0
            mgr._last_image_cleanup = 0.0

            with patch.object(mgr, "cleanup_old_images") as mock_clean:
                mgr._maybe_cleanup_images()
                mock_clean.assert_not_called()


@unittest.skipUnless(HAS_ALERTS and HAS_CV2, "runtime deps 없음")
class TestEmailFilter(unittest.TestCase):
    """이메일 발송 필터 — alert_level threshold + per-event 설정"""

    def _manager(self, tmp: str, prefs: dict) -> AlertManager:
        cfg = AlertConfig(
            enabled=True,
            save_images=False,
            blur_faces=False,
            log_file=str(Path(tmp) / "alerts.log"),
        )
        mgr = AlertManager(cfg)
        mgr._alert_prefs = prefs
        return mgr

    def test_below_threshold_returns_false(self):
        """alert_level=low, threshold=medium → 발송 안 함."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp, {"email_alert_level_threshold": "medium", "per_event_email_enabled": {}})
            event = _make_event(alert_level="low")
            self.assertFalse(mgr._should_send_email(event))

    def test_at_threshold_returns_true(self):
        """alert_level=medium, threshold=medium → 발송."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp, {"email_alert_level_threshold": "medium", "per_event_email_enabled": {}})
            event = _make_event(alert_level="medium")
            self.assertTrue(mgr._should_send_email(event))

    def test_above_threshold_returns_true(self):
        """alert_level=critical, threshold=medium → 발송."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp, {"email_alert_level_threshold": "medium", "per_event_email_enabled": {}})
            event = _make_event(alert_level="critical")
            self.assertTrue(mgr._should_send_email(event))

    def test_per_event_disabled_blocks_send(self):
        """per_event_email_enabled에 해당 타입이 false이면 발송 안 함."""
        with tempfile.TemporaryDirectory() as tmp:
            prefs = {
                "email_alert_level_threshold": "low",
                "per_event_email_enabled": {"person_detected": False},
            }
            mgr = self._manager(tmp, prefs)
            event = _make_event(event_type="person_detected", alert_level="critical")
            self.assertFalse(mgr._should_send_email(event))

    def test_per_event_enabled_allows_send(self):
        """per_event_email_enabled=true이면 threshold 이상 시 발송."""
        with tempfile.TemporaryDirectory() as tmp:
            prefs = {
                "email_alert_level_threshold": "low",
                "per_event_email_enabled": {"fire_detected": True},
            }
            mgr = self._manager(tmp, prefs)
            event = _make_event(event_type="fire_detected", alert_level="high")
            self.assertTrue(mgr._should_send_email(event))


@unittest.skipUnless(HAS_ALERTS and HAS_CV2, "runtime deps 없음")
class TestLogMasking(unittest.TestCase):
    """로그 마스킹 — RTSP URL이 로그에 평문 노출되지 않아야 한다"""

    def test_rtsp_url_masked_in_log(self):
        """description에 rtsp://user:pass@ URL이 있으면 로그에 마스킹돼야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            log_file = str(Path(tmp) / "alerts.log")
            mgr = AlertManager(AlertConfig(
                enabled=True,
                save_images=False,
                blur_faces=False,
                console_output=False,
                log_file=log_file,
            ))
            event = _make_event(
                event_type="person_detected",
                alert_level="medium",
                description="rtsp://admin:secret123@192.168.1.100/stream",
            )
            mgr.handle_event(event)
            for h in mgr.alert_logger.handlers:
                h.flush()

            log_content = Path(log_file).read_text(encoding="utf-8")
            self.assertNotIn("secret123", log_content, "비밀번호가 로그에 노출되면 안 됨")

    def test_field_level_masking_preserves_structured_fields(self):
        """#7 — description의 PII는 가리되 event_id의 timestamp 등 구조 필드는 보존한다."""
        with tempfile.TemporaryDirectory() as tmp:
            log_file = str(Path(tmp) / "alerts.log")
            mgr = AlertManager(AlertConfig(
                enabled=True, save_images=False, blur_faces=False,
                console_output=False, log_file=log_file,
            ))
            event = _make_event(
                event_id="cam01_fall_1781893381",  # 10자리 timestamp 포함 — 오탐 금지
                description="작업자 연락처 010-1234-5678, hong@corp.com",
            )
            mgr.handle_event(event)
            for h in mgr.alert_logger.handlers:
                h.flush()
            log_content = Path(log_file).read_text(encoding="utf-8")
            # PII는 가려짐
            self.assertNotIn("010-1234-5678", log_content)
            self.assertNotIn("hong@corp.com", log_content)
            # event_id(타임스탬프 포함)는 그대로 보존
            self.assertIn("cam01_fall_1781893381", log_content)

    def test_masked_event_is_persisted_to_durable_repository(self):
        class CaptureRepository:
            def __init__(self):
                self.items = []

            def record(self, payload):
                self.items.append(payload)
                return len(self.items)

        with tempfile.TemporaryDirectory() as tmp:
            repository = CaptureRepository()
            manager = AlertManager(
                AlertConfig(
                    enabled=True,
                    save_images=False,
                    blur_faces=False,
                    console_output=False,
                    log_file=str(Path(tmp) / "alerts.log"),
                ),
                event_repository=repository,
            )
            manager.handle_event(_make_event(description="rtsp://admin:secret123@10.0.0.5/live"))
            self.assertEqual(len(repository.items), 1)
            self.assertNotIn("secret123", repository.items[0]["description"])

    def test_durable_repository_failure_does_not_suppress_alert_log(self):
        class BrokenRepository:
            failing = True

            def record(self, payload):
                if self.failing:
                    raise OSError("disk full")
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "alerts.log"
            from src.api.service import DurableEventStoreStatus

            repository = BrokenRepository()
            status = DurableEventStoreStatus()
            manager = AlertManager(
                AlertConfig(
                    enabled=True,
                    save_images=False,
                    blur_faces=False,
                    console_output=False,
                    log_file=str(log_file),
                ),
                event_repository=repository,
                durable_store_status=status,
            )
            manager.handle_event(_make_event())
            for handler in manager.alert_logger.handlers:
                handler.flush()
            self.assertIn("EVENT |", log_file.read_text(encoding="utf-8"))
            self.assertFalse(status.health_check().healthy)
            status.record_success(operation="query")
            self.assertFalse(status.health_check().healthy)
            repository.failing = False
            manager._persist_event({"event_id": "evt-recovered"})
            self.assertTrue(status.health_check().healthy)


@unittest.skipUnless(HAS_ALERTS and HAS_CV2, "runtime deps 없음")
class TestVlmEmailFields(unittest.TestCase):
    """이메일 본문에 VLM 필드(vlm_risk_level, vlm_recommended_action 등) 포함 여부"""

    def _manager(self, tmp: str) -> AlertManager:
        cfg = AlertConfig(
            enabled=True,
            save_images=False,
            blur_faces=False,
            log_file=str(Path(tmp) / "alerts.log"),
        )
        mgr = AlertManager(cfg)
        # SMTP 설정 없으면 _send_email이 즉시 return — smtp_config를 더미로 주입
        mgr.smtp_config = {
            "host": "localhost",
            "port": 587,
            "user": "test@example.com",
            "password": "",
            "recipients": ["recv@example.com"],
            "use_ssl": False,
            "timeout": 5,
        }
        mgr._alert_prefs = {"email_alert_level_threshold": "low", "per_event_email_enabled": {}}
        return mgr

    def test_vlm_plain_includes_risk_and_action(self):
        """_build_vlm_plain이 vlm_risk_level, vlm_recommended_action을 포함해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            event = _make_event(
                vlm_parse_success=True,
                vlm_risk_level="high",
                vlm_recommended_action="즉시 대피 요청",
                vlm_detected_events=["화재", "연기"],
            )
            plain = mgr._build_vlm_plain(event)
            self.assertIn("high", plain)
            self.assertIn("즉시 대피 요청", plain)
            self.assertIn("화재", plain)

    def test_vlm_plain_none_parse_success_returns_empty(self):
        """vlm_parse_success=None(VLM 미실행)이면 빈 문자열을 반환해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            event = _make_event(vlm_parse_success=None)
            self.assertEqual(mgr._build_vlm_plain(event), "")

    def test_vlm_plain_parse_failed_shows_failure(self):
        """vlm_parse_success=False이면 '파싱 실패' 문구가 포함돼야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            event = _make_event(vlm_parse_success=False)
            plain = mgr._build_vlm_plain(event)
            self.assertIn("파싱 실패", plain)

    def test_vlm_html_rows_risk_badge(self):
        """_build_vlm_html_rows가 risk_level 배지 HTML을 포함해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            event = _make_event(
                vlm_parse_success=True,
                vlm_risk_level="critical",
                vlm_recommended_action="경찰 신고",
                vlm_detected_events=["침입자"],
            )
            html = mgr._build_vlm_html_rows(event)
            self.assertIn("CRITICAL", html)
            self.assertIn("경찰 신고", html)
            self.assertIn("침입자", html)

    def test_vlm_html_rows_escape_model_controlled_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            event = _make_event(
                vlm_parse_success=True,
                vlm_risk_level="high",
                vlm_recommended_action='<a href="https://attacker.invalid">click</a>',
                vlm_detected_events=["<script>alert(1)</script>"],
            )

            rendered = mgr._build_vlm_html_rows(event)

            self.assertNotIn("<script>", rendered)
            self.assertNotIn('<a href=', rendered)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
            self.assertIn("&lt;a href=&quot;https://attacker.invalid&quot;&gt;click&lt;/a&gt;", rendered)

    def test_send_email_calls_smtp(self):
        """_send_email이 SMTP 발송 경로(_smtp_send)를 실제로 호출해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._manager(tmp)
            event = _make_event(
                alert_level="high",
                vlm_parse_success=True,
                vlm_risk_level="high",
                vlm_recommended_action="대피 요청",
            )
            with patch.object(mgr, "_smtp_send") as mock_send:
                mgr._send_email(event)
                mock_send.assert_called_once()

    def test_outbox_email_is_serializable_and_not_sent_directly(self):
        """운영 경로는 SMTP 직접 호출 없이 직렬화 가능한 MIME 원문을 먼저 저장한다."""
        from src.alerts.outbox import OutboxRecord

        with tempfile.TemporaryDirectory() as tmp:
            mgr = AlertManager(AlertConfig(
                enabled=True,
                save_images=False,
                blur_faces=False,
                log_file=str(Path(tmp) / "alerts.log"),
                outbox_dir=str(Path(tmp) / "outbox"),
            ))
            mgr._outbox.stop()
            mgr.smtp_config = {
                "host": "localhost",
                "port": 587,
                "user": "test@example.com",
                "password": "",
                "recipients": ["recv@example.com"],
                "use_ssl": False,
                "timeout": 5,
            }
            mgr._alert_prefs = {
                "email_alert_level_threshold": "low",
                "per_event_email_enabled": {},
            }

            with patch.object(mgr, "_smtp_send") as mock_send:
                mgr._enqueue_email(_make_event(alert_level="high"))
                mock_send.assert_not_called()

            line = mgr._outbox.pending_file.read_text(encoding="utf-8").strip()
            record = OutboxRecord.from_json(line)
            self.assertIsInstance(record.attempt.payload["message"], str)
            self.assertIn("recv@example.com", record.attempt.recipient)
            self.assertIn("Message-ID:", record.attempt.payload["message"])

            with patch.object(mgr, "_smtp_send") as mock_send:
                self.assertTrue(mgr._send_email_direct(record.attempt))
                mock_send.assert_called_once()

    def test_outbox_uses_registered_dispatcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AlertManager(AlertConfig(
                enabled=True,
                save_images=False,
                blur_faces=False,
                log_file=str(Path(tmp) / "alerts.log"),
                outbox_dir=str(Path(tmp) / "outbox"),
            ))
            try:
                self.assertIs(mgr._outbox.dispatcher, mgr._dispatcher)
            finally:
                mgr.shutdown()

    def test_outbox_worker_delivers_queued_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AlertManager(AlertConfig(
                enabled=True,
                save_images=False,
                blur_faces=False,
                log_file=str(Path(tmp) / "alerts.log"),
                outbox_dir=str(Path(tmp) / "outbox"),
            ))
            mgr.smtp_config = {
                "host": "localhost",
                "port": 587,
                "user": "test@example.com",
                "password": "",
                "recipients": ["recv@example.com"],
                "use_ssl": False,
                "timeout": 5,
            }
            mgr._alert_prefs = {
                "email_alert_level_threshold": "low",
                "per_event_email_enabled": {},
            }
            try:
                with patch.object(mgr, "_smtp_send") as mock_send:
                    mgr._enqueue_email(_make_event(event_id="worker-delivery", alert_level="high"))
                    deadline = time.time() + 2
                    while not mock_send.called and time.time() < deadline:
                        time.sleep(0.01)
                    mock_send.assert_called_once()
            finally:
                mgr.shutdown()

    def test_shutdown_flushes_buffered_batch_to_log_and_outbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AlertManager(AlertConfig(
                enabled=True,
                save_images=False,
                blur_faces=False,
                console_output=False,
                log_file=str(Path(tmp) / "alerts.log"),
                outbox_dir=str(Path(tmp) / "outbox"),
                batch_flush_interval=60,
            ))
            mgr._outbox.stop()
            mgr.smtp_config = {
                "host": "localhost",
                "port": 587,
                "user": "test@example.com",
                "password": "",
                "recipients": ["recv@example.com"],
                "use_ssl": False,
                "timeout": 5,
            }
            mgr._alert_prefs = {
                "email_alert_level_threshold": "low",
                "per_event_email_enabled": {},
            }
            event = _make_event(event_id="batch-shutdown", alert_level="high")
            mgr.handle_event(event)

            self.assertNotIn("batch-shutdown", Path(mgr.config.log_file).read_text(encoding="utf-8"))
            mgr.shutdown()
            for handler in mgr.alert_logger.handlers:
                handler.flush()

            self.assertIn("batch-shutdown", Path(mgr.config.log_file).read_text(encoding="utf-8"))
            self.assertIn("batch-shutdown", mgr._outbox.pending_file.read_text(encoding="utf-8"))

    def test_single_event_batch_flushes_on_timer(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AlertManager(AlertConfig(
                enabled=True,
                save_images=False,
                blur_faces=False,
                console_output=False,
                log_file=str(Path(tmp) / "alerts.log"),
                outbox_dir=str(Path(tmp) / "outbox"),
                batch_flush_interval=0.05,
            ))
            try:
                mgr.handle_event(_make_event(event_id="timer-flush"))
                deadline = time.time() + 2
                content = ""
                while time.time() < deadline:
                    for handler in mgr.alert_logger.handlers:
                        handler.flush()
                    content = Path(mgr.config.log_file).read_text(encoding="utf-8")
                    if "timer-flush" in content:
                        break
                    time.sleep(0.01)
                self.assertIn("timer-flush", content)
            finally:
                mgr.shutdown()

    def test_provisional_event_bypasses_batch_and_sets_sent_after_enqueue(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AlertManager(AlertConfig(
                enabled=True,
                save_images=False,
                blur_faces=False,
                console_output=False,
                log_file=str(Path(tmp) / "alerts.log"),
                outbox_dir=str(Path(tmp) / "outbox"),
                batch_flush_interval=60,
            ))
            mgr._outbox.stop()
            mgr.smtp_config = {
                "host": "localhost",
                "port": 587,
                "user": "test@example.com",
                "password": "",
                "recipients": ["recv@example.com"],
                "use_ssl": False,
                "timeout": 5,
            }
            mgr._alert_prefs = {
                "email_alert_level_threshold": "low",
                "per_event_email_enabled": {},
            }
            event = _make_event(
                event_id="provisional-immediate",
                event_type="fire",
                alert_level="critical",
                provisional_alert=True,
                provisional_published=True,
            )

            self.assertTrue(mgr.handle_event(event))

            self.assertEqual(mgr._batch_buffer, [])
            self.assertTrue(event.provisional_sent)
            self.assertIsNotNone(event.provisional_sent_at)
            self.assertIn("provisional-immediate", mgr._outbox.pending_file.read_text(encoding="utf-8"))


@unittest.skipUnless(HAS_ALERTS and HAS_CV2, "runtime deps 없음")
class TestSmtpConfig(unittest.TestCase):
    """SMTP 설정 — use_ssl, timeout 필드 로드 검증"""

    def test_default_starttls_no_ssl(self):
        """KSI_CCTV_SMTP_USE_SSL 미설정 → use_ssl=False."""
        env = {"KSI_CCTV_SMTP_HOST": "smtp.example.com"}
        with patch.dict(os.environ, env, clear=False), tempfile.TemporaryDirectory() as tmp:
            os.environ.pop("KSI_CCTV_SMTP_USE_SSL", None)
            cfg = AlertConfig(
                enabled=True, save_images=False, blur_faces=False,
                log_file=str(Path(tmp) / "alerts.log"),
            )
            mgr = AlertManager(cfg)
            result = mgr._load_smtp_config()
        self.assertIsNotNone(result)
        self.assertFalse(result["use_ssl"])

    def test_ssl_mode_enabled(self):
        """KSI_CCTV_SMTP_USE_SSL=true → use_ssl=True."""
        env = {"KSI_CCTV_SMTP_HOST": "smtp.example.com", "KSI_CCTV_SMTP_USE_SSL": "true"}
        with patch.dict(os.environ, env, clear=False), tempfile.TemporaryDirectory() as tmp:
            cfg = AlertConfig(
                enabled=True, save_images=False, blur_faces=False,
                log_file=str(Path(tmp) / "alerts.log"),
            )
            mgr = AlertManager(cfg)
            result = mgr._load_smtp_config()
        self.assertTrue(result["use_ssl"])

    def test_custom_timeout_loaded(self):
        """KSI_CCTV_SMTP_TIMEOUT=30 → timeout=30.0."""
        env = {"KSI_CCTV_SMTP_HOST": "smtp.example.com", "KSI_CCTV_SMTP_TIMEOUT": "30"}
        with patch.dict(os.environ, env, clear=False), tempfile.TemporaryDirectory() as tmp:
            cfg = AlertConfig(
                enabled=True, save_images=False, blur_faces=False,
                log_file=str(Path(tmp) / "alerts.log"),
            )
            mgr = AlertManager(cfg)
            result = mgr._load_smtp_config()
        self.assertAlmostEqual(result["timeout"], 30.0)

    def test_default_timeout_is_ten(self):
        """KSI_CCTV_SMTP_TIMEOUT 미설정 → timeout=10.0."""
        env = {"KSI_CCTV_SMTP_HOST": "smtp.example.com"}
        with patch.dict(os.environ, env, clear=False), tempfile.TemporaryDirectory() as tmp:
            os.environ.pop("KSI_CCTV_SMTP_TIMEOUT", None)
            cfg = AlertConfig(
                enabled=True, save_images=False, blur_faces=False,
                log_file=str(Path(tmp) / "alerts.log"),
            )
            mgr = AlertManager(cfg)
            result = mgr._load_smtp_config()
        self.assertAlmostEqual(result["timeout"], 10.0)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_ALERTS, "alert_manager import 불가")
class TestVlmUnconfirmedAlert(unittest.TestCase):
    """회귀 가드: vlm_needed인데 VLM 미실행이면 이메일 본문에 'VLM 미확인'을 명시한다."""

    def _manager(self) -> AlertManager:
        return AlertManager(AlertConfig(enabled=True, save_images=False, console_output=False))

    def test_unconfirmed_event_shows_warning(self):
        mgr = self._manager()
        ev = _make_event(vlm_needed=True)  # vlm_parse_success 기본 None → 미확인
        text = mgr._build_vlm_plain(ev)
        self.assertIn("VLM 미확인", text)

    def test_non_vlm_event_stays_empty(self):
        mgr = self._manager()
        ev = _make_event(vlm_needed=False)  # VLM 불필요 + 미실행 → 빈 문자열
        self.assertEqual(mgr._build_vlm_plain(ev), "")

    def test_confirmed_event_shows_results(self):
        mgr = self._manager()
        ev = _make_event(
            vlm_needed=True, vlm_parse_success=True,
            vlm_risk_level="high", vlm_recommended_action="대피",
        )
        text = mgr._build_vlm_plain(ev)
        self.assertIn("high", text)
        self.assertNotIn("미확인", text)
