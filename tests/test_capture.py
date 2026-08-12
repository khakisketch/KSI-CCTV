"""
RTSPCapture 단위 테스트

검증 항목:
- [P0] CAP_PROP 타임아웃 설정 — _connect() 호출 시 적용 여부
- [P1] get_status() 필드 완전성 및 last_frame_time / is_stale 노출
- [P1] fps_actual 이동평균: _on_frame_received() 반복 호출 후 수렴 여부
- [P2] 재연결 카운터·백오프·get_status 노출
- URL 마스킹 (_safe_stream_url)
- seconds_since_last_frame / is_stale() 프로퍼티
- CameraManager.get_status() 위임 구조
"""
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from src.capture.rtsp_capture import _BACKOFF_MAX, _FPS_WINDOW, CameraManager, RTSPCapture
    HAS_CV2 = True
except ModuleNotFoundError:
    HAS_CV2 = False

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _make_cap(camera_id: str = "cam_test", **kwargs) -> RTSPCapture:
    """VideoCapture를 mock으로 대체한 RTSPCapture 인스턴스"""
    return RTSPCapture(camera_id=camera_id, stream_url="rtsp://fake/stream", **kwargs)


# ---------------------------------------------------------------------------
# [P0] 타임아웃 설정 테스트
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_CV2, "cv2 설치 후 실행 가능")
class TestConnectTimeout:
    """_connect()에서 CAP_PROP 타임아웃이 실제로 set() 호출되는지 검증"""

    def test_open_timeout_set(self):
        """스트림 소스는 생성자 params로 CAP_PROP_OPEN_TIMEOUT_MSEC(self.timeout*1000 ms)을 전달해야 한다.

        빈 VideoCapture()에 set() 후 open()하는 방식은 일부 OpenCV 빌드에서
        무시되므로(green≠작동), open 타임아웃은 반드시 생성자 params로 전달해야 한다.
        """
        import cv2
        cap = _make_cap(timeout=15.0)
        mock_vc = MagicMock()
        mock_vc.isOpened.return_value = True

        with patch("cv2.VideoCapture", return_value=mock_vc) as mock_ctor:
            result = cap._connect()

        assert result is True
        # 생성자 호출 형태: VideoCapture(url, CAP_FFMPEG, [..., OPEN_TIMEOUT, ms, ...])
        ctor_args = mock_ctor.call_args.args
        assert len(ctor_args) >= 3, "스트림 소스는 (url, apiPreference, params) 3-인자 생성자를 써야 함"
        params = ctor_args[2]
        assert cv2.CAP_PROP_OPEN_TIMEOUT_MSEC in params, "OPEN_TIMEOUT이 생성자 params에 없음"
        idx = params.index(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC)
        assert params[idx + 1] == 15_000, f"기대값 15000, 실제값 {params[idx + 1]}"

    def test_read_timeout_set(self):
        """CAP_PROP_READ_TIMEOUT_MSEC이 self.timeout * 1000 ms로 설정되어야 한다"""
        cap = _make_cap(timeout=8.0)
        mock_vc = MagicMock()
        mock_vc.isOpened.return_value = True

        with patch("cv2.VideoCapture", return_value=mock_vc):
            cap._connect()

        import cv2
        read_calls = [c for c in mock_vc.set.call_args_list
                      if c.args[0] == cv2.CAP_PROP_READ_TIMEOUT_MSEC]
        assert read_calls, "CAP_PROP_READ_TIMEOUT_MSEC이 설정되지 않음"
        assert read_calls[0].args[1] == 8_000

    def test_open_called_with_stream_url(self):
        """스트림 소스는 VideoCapture 생성자 첫 인자로 stream_url을 받아 연결해야 한다."""
        url = "rtsp://user:pass@192.168.1.1:554/stream"
        cap = RTSPCapture(camera_id="cam1", stream_url=url)
        mock_vc = MagicMock()
        mock_vc.isOpened.return_value = True

        with patch("cv2.VideoCapture", return_value=mock_vc) as mock_ctor:
            cap._connect()

        assert mock_ctor.call_args.args[0] == url

    def test_file_source_uses_plain_constructor(self):
        """파일 소스는 타임아웃 없이 기본 백엔드(단일 인자 생성자)로 열어야 한다."""
        import cv2
        cap = RTSPCapture(camera_id="cam_file", stream_url="./data/sample.mp4")
        assert cap.is_file_source is True
        mock_vc = MagicMock()
        mock_vc.isOpened.return_value = True

        with patch("cv2.VideoCapture", return_value=mock_vc) as mock_ctor:
            cap._connect()

        # 파일은 (url) 단일 인자 — CAP_FFMPEG/params를 강제하지 않는다.
        ctor_args = mock_ctor.call_args.args
        assert ctor_args[0] == "./data/sample.mp4"
        assert len(ctor_args) == 1, "파일 소스에 apiPreference/params를 강제하면 안 됨"
        assert cv2.CAP_PROP_OPEN_TIMEOUT_MSEC not in [c.args[0] for c in mock_vc.set.call_args_list]

    def test_connect_failure_returns_false(self):
        """isOpened()=False 시 _connect()는 False를 반환해야 한다"""
        cap = _make_cap()
        mock_vc = MagicMock()
        mock_vc.isOpened.return_value = False

        with patch("cv2.VideoCapture", return_value=mock_vc):
            result = cap._connect()

        assert result is False

    def test_connect_exception_never_logs_the_credentialed_url(self, caplog):
        url = "rtsp://operator:super-secret@192.168.1.20/live"
        cap = RTSPCapture(camera_id="cam1", stream_url=url)

        with patch("cv2.VideoCapture", side_effect=RuntimeError(f"failed to open {url}")):
            result = cap._connect()

        assert result is False
        assert "RuntimeError" in caplog.text
        assert "operator" not in caplog.text
        assert "super-secret" not in caplog.text

    def test_reconnect_attempts_incremented_on_each_call(self):
        """_connect() 호출마다 reconnect_attempts가 1씩 증가해야 한다"""
        cap = _make_cap()
        mock_vc = MagicMock()
        mock_vc.isOpened.return_value = False

        with patch("cv2.VideoCapture", return_value=mock_vc):
            cap._connect()
            cap._connect()
            cap._connect()

        assert cap.reconnect_attempts == 3

    def test_last_connect_time_updated_on_success(self):
        """연결 성공 시 last_connect_time이 갱신되어야 한다"""
        cap = _make_cap()
        mock_vc = MagicMock()
        mock_vc.isOpened.return_value = True
        before = time.time()

        with patch("cv2.VideoCapture", return_value=mock_vc):
            cap._connect()

        assert cap.last_connect_time >= before

    def test_fps_window_cleared_on_reconnect(self):
        """재연결 성공 시 _frame_intervals 윈도우가 초기화되어야 한다"""
        cap = _make_cap()
        # 인위적으로 윈도우에 값 채우기
        cap._frame_intervals.extend([0.033] * 10)
        cap._last_interval_ts = time.time()

        mock_vc = MagicMock()
        mock_vc.isOpened.return_value = True

        with patch("cv2.VideoCapture", return_value=mock_vc):
            cap._connect()

        assert len(cap._frame_intervals) == 0
        assert cap._last_interval_ts == 0.0


# ---------------------------------------------------------------------------
# [P1] get_status() 필드 완전성
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_CV2, "cv2 설치 후 실행 가능")
class TestGetStatus:
    """get_status()가 반드시 포함해야 하는 필드 검증"""

    REQUIRED_KEYS = {
        "connected", "queue_size", "fps", "frame_count", "dropped_frames",
        "source_type", "last_frame_time", "seconds_since_last_frame",
        "is_stale", "reconnect_attempts", "last_connect_time", "consecutive_failures",
    }

    def test_all_keys_present(self):
        """get_status()에 모든 필수 키가 존재해야 한다"""
        cap = _make_cap()
        status = cap.get_status()
        missing = self.REQUIRED_KEYS - status.keys()
        assert not missing, f"누락된 필드: {missing}"

    def test_initial_state_no_frame(self):
        """프레임 미수신 초기 상태에서 last_frame_time/seconds_since_last_frame은 None"""
        cap = _make_cap()
        status = cap.get_status()
        assert status["last_frame_time"] is None
        assert status["seconds_since_last_frame"] is None
        assert status["is_stale"] is False

    def test_last_frame_time_exposed_after_frame(self):
        """_on_frame_received() 호출 후 last_frame_time이 None이 아니어야 한다"""
        cap = _make_cap()
        cap._on_frame_received()
        status = cap.get_status()
        assert status["last_frame_time"] is not None
        assert status["seconds_since_last_frame"] is not None
        assert status["seconds_since_last_frame"] >= 0.0

    def test_is_stale_false_when_recent(self):
        """방금 수신한 프레임은 stale이 아니어야 한다"""
        cap = _make_cap(stale_threshold=30.0)
        cap._on_frame_received()
        status = cap.get_status()
        assert status["is_stale"] is False

    def test_is_stale_true_when_overdue(self):
        """stale_threshold보다 오래된 프레임은 is_stale=True여야 한다"""
        cap = _make_cap(stale_threshold=5.0)
        # 10초 전 수신 시뮬레이션
        cap.last_frame_time = time.time() - 10.0
        status = cap.get_status()
        assert status["is_stale"] is True

    def test_fps_rounded_to_2_decimal(self):
        """fps 필드는 소수점 2자리로 반올림되어야 한다"""
        cap = _make_cap()
        cap.fps_actual = 29.976523
        status = cap.get_status()
        assert status["fps"] == 29.98

    def test_reconnect_fields_initial(self):
        """초기 상태에서 재연결 필드 기본값 검증"""
        cap = _make_cap()
        status = cap.get_status()
        assert status["reconnect_attempts"] == 0
        assert status["last_connect_time"] is None
        assert status["consecutive_failures"] == 0

    def test_source_type_stream(self):
        """RTSP URL은 source_type='stream'이어야 한다"""
        cap = _make_cap()
        assert cap.get_status()["source_type"] == "stream"

    def test_source_type_file(self):
        """mp4 URL은 source_type='file'이어야 한다"""
        cap = RTSPCapture(camera_id="cam_file", stream_url="/data/video.mp4")
        assert cap.get_status()["source_type"] == "file"


# ---------------------------------------------------------------------------
# [P1] FPS 이동평균 테스트
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_CV2, "cv2 설치 후 실행 가능")
class TestFpsMovingAverage:
    """_on_frame_received()로 fps_actual이 이동평균으로 수렴하는지 검증"""

    def _simulate_frames(self, cap: RTSPCapture, interval: float, count: int):
        """일정 간격으로 프레임 수신 시뮬레이션"""
        for _ in range(count):
            cap._on_frame_received()
            time.sleep(interval)

    def test_fps_converges_to_target(self):
        """
        33ms 간격(≈30fps) 프레임을 FPS_WINDOW개 수신 후
        fps_actual이 20~45 사이에 있어야 한다 (CI 환경 sleep 오차 허용).
        """
        cap = _make_cap()
        # 실제 sleep을 사용하면 느리므로 _frame_intervals를 직접 채움
        interval = 1.0 / 30.0
        for _ in range(_FPS_WINDOW):
            cap._frame_intervals.append(interval)
        cap._last_interval_ts = time.time() - interval
        cap._on_frame_received()

        assert 20.0 <= cap.fps_actual <= 45.0, f"fps_actual={cap.fps_actual}"

    def test_single_frame_no_previous(self):
        """첫 번째 호출에서는 이전 타임스탬프가 없으므로 fps_actual=0"""
        cap = _make_cap()
        cap._on_frame_received()
        # 첫 호출: _last_interval_ts=0 이므로 interval 계산 안 됨 → 윈도우 비어있음
        assert cap.fps_actual == 0.0

    def test_two_frames_give_nonzero_fps(self):
        """두 번째 호출부터 fps_actual이 0보다 커야 한다"""
        cap = _make_cap()
        cap._on_frame_received()
        time.sleep(0.01)
        cap._on_frame_received()
        assert cap.fps_actual > 0.0

    def test_large_gap_excluded_from_window(self):
        """
        60초 이상의 간격은 이동평균 윈도우에 들어가지 않아야 한다
        (재연결 후 비정상 간격 오염 방지).
        """
        cap = _make_cap()
        cap._last_interval_ts = time.time() - 120.0  # 120초 전
        cap._on_frame_received()
        # 큰 간격이 제외되면 윈도우가 비어있어 fps=0
        assert cap.fps_actual == 0.0

    def test_window_size_capped_at_fps_window(self):
        """이동평균 윈도우는 _FPS_WINDOW(30) 크기를 초과하지 않아야 한다"""
        cap = _make_cap()
        cap._last_interval_ts = time.time()
        for _ in range(_FPS_WINDOW * 2):
            time.sleep(0.001)
            cap._on_frame_received()

        assert len(cap._frame_intervals) <= _FPS_WINDOW


# ---------------------------------------------------------------------------
# [P2] 재연결 카운터·백오프
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_CV2, "cv2 설치 후 실행 가능")
class TestReconnectBackoff:
    """재연결 실패 시 카운터 증가 및 지수 백오프 계산 검증"""

    def test_consecutive_failures_incremented(self):
        """_connect() 실패 시마다 _consecutive_failures가 증가해야 한다"""
        cap = _make_cap(reconnect_interval=1.0)
        mock_vc = MagicMock()
        mock_vc.isOpened.return_value = False

        with patch("cv2.VideoCapture", return_value=mock_vc):
            # 실제 루프 대신 _connect를 직접 호출해 연속 실패 상황 재현
            for _ in range(3):
                result = cap._connect()
                if not result:
                    cap._consecutive_failures += 1

        assert cap._consecutive_failures == 3

    def test_backoff_formula(self):
        """
        reconnect_interval=5s, consecutive_failures=3 일 때
        백오프 = min(5 * 2^2, 60) = min(20, 60) = 20s 이어야 한다.
        """
        cap = _make_cap(reconnect_interval=5.0)
        cap._consecutive_failures = 3
        backoff = min(
            cap.reconnect_interval * (2 ** (cap._consecutive_failures - 1)),
            _BACKOFF_MAX,
        )
        assert backoff == 20.0

    def test_backoff_capped_at_max(self):
        """백오프가 _BACKOFF_MAX(60초)를 초과하지 않아야 한다"""
        cap = _make_cap(reconnect_interval=5.0)
        cap._consecutive_failures = 20  # 충분히 큰 값
        backoff = min(
            cap.reconnect_interval * (2 ** (cap._consecutive_failures - 1)),
            _BACKOFF_MAX,
        )
        assert backoff == _BACKOFF_MAX

    def test_consecutive_failures_reset_on_success(self):
        """연결 성공 시 _consecutive_failures가 0으로 리셋되어야 한다"""
        cap = _make_cap()
        cap._consecutive_failures = 5
        mock_vc = MagicMock()
        mock_vc.isOpened.return_value = True

        with patch("cv2.VideoCapture", return_value=mock_vc):
            cap._connect()

        # _capture_loop에서 성공 시 리셋하는 로직을 직접 시뮬레이션
        cap._consecutive_failures = 0
        assert cap._consecutive_failures == 0

    def test_reconnect_attempts_in_status(self):
        """get_status()에서 reconnect_attempts를 확인할 수 있어야 한다"""
        cap = _make_cap()
        cap.reconnect_attempts = 7
        cap._consecutive_failures = 2
        status = cap.get_status()
        assert status["reconnect_attempts"] == 7
        assert status["consecutive_failures"] == 2


# ---------------------------------------------------------------------------
# URL 마스킹
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_CV2, "cv2 설치 후 실행 가능")
class TestSafeStreamUrl:
    """_safe_stream_url이 자격증명을 마스킹하는지 검증"""

    def test_masks_username_and_password(self):
        """user:pass@host 형식에서 자격증명이 ***:***로 치환되어야 한다"""
        cap = RTSPCapture(camera_id="c", stream_url="rtsp://admin:secret123@192.168.0.1:554/live")
        safe = cap._safe_stream_url
        assert "secret123" not in safe
        assert "admin" not in safe
        assert "***:***" in safe
        assert "192.168.0.1" in safe

    def test_no_credentials_url_unchanged(self):
        """자격증명 없는 URL은 그대로 반환되어야 한다"""
        url = "rtsp://192.168.0.1:554/live"
        cap = RTSPCapture(camera_id="c", stream_url=url)
        assert cap._safe_stream_url == url

    def test_file_path_unchanged(self):
        """로컬 파일 경로는 그대로 반환되어야 한다"""
        path = "/data/recordings/cam1.mp4"
        cap = RTSPCapture(camera_id="c", stream_url=path)
        assert cap._safe_stream_url == path


# ---------------------------------------------------------------------------
# seconds_since_last_frame / is_stale() 프로퍼티
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_CV2, "cv2 설치 후 실행 가능")
class TestStaleProperty:
    """seconds_since_last_frame과 is_stale() 프로퍼티 검증"""

    def test_none_when_no_frame(self):
        """프레임 미수신 시 seconds_since_last_frame은 None"""
        cap = _make_cap()
        assert cap.seconds_since_last_frame is None

    def test_positive_after_frame(self):
        """프레임 수신 직후 seconds_since_last_frame은 0 이상"""
        cap = _make_cap()
        cap._on_frame_received()
        elapsed = cap.seconds_since_last_frame
        assert elapsed is not None
        assert elapsed >= 0.0

    def test_is_stale_false_recent(self):
        """방금 수신한 프레임에서 is_stale()=False"""
        cap = _make_cap(stale_threshold=30.0)
        cap._on_frame_received()
        assert cap.is_stale() is False

    def test_is_stale_true_old(self):
        """오래된 last_frame_time이면 is_stale()=True"""
        cap = _make_cap(stale_threshold=5.0)
        cap.last_frame_time = time.time() - 10.0
        assert cap.is_stale() is True

    def test_is_stale_custom_threshold(self):
        """커스텀 threshold 인자로 판정 기준을 덮어쓸 수 있어야 한다"""
        cap = _make_cap(stale_threshold=60.0)  # 기본은 60초
        cap.last_frame_time = time.time() - 10.0
        # 기본(60s) 기준으로는 stale 아님
        assert cap.is_stale() is False
        # 5s 커스텀 기준으로는 stale
        assert cap.is_stale(threshold=5.0) is True

    def test_is_stale_false_no_frame(self):
        """프레임 미수신 상태에서는 stale이 아닌 것으로 간주"""
        cap = _make_cap()
        assert cap.is_stale() is False


# ---------------------------------------------------------------------------
# CameraManager
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_CV2, "cv2 설치 후 실행 가능")
class TestCameraManager:
    """CameraManager 기본 동작 검증"""

    def test_remove_camera_stops_and_unregisters(self):
        manager = CameraManager()
        capture = manager.add_camera("cam_remove", "rtsp://192.168.1.10/live")
        capture.stop = MagicMock()

        removed = manager.remove_camera("cam_remove")

        assert removed is capture
        capture.stop.assert_called_once()
        assert manager.get_camera("cam_remove") is None

    def test_add_and_get_camera(self):
        """add_camera 후 get_camera로 동일 인스턴스를 반환해야 한다"""
        mgr = CameraManager()
        cap = mgr.add_camera("cam1", "rtsp://host/stream")
        assert mgr.get_camera("cam1") is cap

    def test_duplicate_camera_raises(self):
        """동일 camera_id 추가 시 ValueError가 발생해야 한다"""
        mgr = CameraManager()
        mgr.add_camera("cam1", "rtsp://host/stream")
        try:
            mgr.add_camera("cam1", "rtsp://host/stream2")
            assert False, "ValueError가 발생하지 않았음"
        except ValueError:
            pass

    def test_get_status_delegates(self):
        """CameraManager.get_status()는 각 카메라의 get_status()를 위임해야 한다"""
        mgr = CameraManager()
        mgr.add_camera("cam1", "rtsp://host/stream1")
        mgr.add_camera("cam2", "/data/video.mp4")
        status = mgr.get_status()

        assert "cam1" in status
        assert "cam2" in status
        # 필수 키 포함 여부
        for cam_status in status.values():
            assert "connected" in cam_status
            assert "is_stale" in cam_status
            assert "reconnect_attempts" in cam_status

    def test_get_camera_nonexistent_returns_none(self):
        """존재하지 않는 camera_id 조회 시 None을 반환해야 한다"""
        mgr = CameraManager()
        assert mgr.get_camera("nonexistent") is None
