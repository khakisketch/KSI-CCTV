"""
RTSP 스트림 수신 모듈

멀티스레딩 기반 RTSP 스트림 수신 및 프레임 큐 관리.
주요 개선:
- cv2 open/read 타임아웃 적용(FFMPEG 백엔드) — 죽은 RTSP 무한 블록 방지
- last_frame_time / seconds_since_last_frame / is_stale 노출
- fps_actual 이동평균(윈도우 N프레임) 기반 실 throughput 계산
- 재연결 시도 카운터 + 지수 백오프 + get_status 노출
- URL 마스킹(_safe_stream_url) — 로그·상태 반환에 자격증명 미노출
"""
import contextlib
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Queue
from urllib.parse import urlsplit, urlunsplit

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 이동평균 계산에 사용할 프레임 윈도우 크기
_FPS_WINDOW = 30

# 지수 백오프 상한 (초)
_BACKOFF_MAX = 60.0


@dataclass
class Frame:
    """프레임 데이터 클래스"""

    camera_id: str
    timestamp: datetime
    image: np.ndarray
    frame_number: int


class RTSPCapture:
    """
    RTSP 스트림 수신 및 프레임 관리

    Attributes:
        camera_id (str): 카메라 고유 ID
        stream_url (str): RTSP 스트림 URL
        queue_size (int): 프레임 버퍼 큐 크기
        target_fps (int): 목표 FPS
        timeout (float): 연결·읽기 타임아웃(초). FFMPEG 백엔드에서 CAP_PROP 설정.
        stale_threshold (float): 마지막 프레임 이후 이 시간(초) 초과 시 stale 판정
    """

    def __init__(
        self,
        camera_id: str,
        stream_url: str,
        queue_size: int = 30,
        target_fps: int = 30,
        reconnect_interval: float = 5.0,
        timeout: float = 10.0,
        resize: tuple[int, int] | None = None,
        loop_file: bool = True,
        stale_threshold: float = 30.0,
    ):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.queue_size = queue_size
        self.target_fps = target_fps
        self.reconnect_interval = reconnect_interval
        self.timeout = timeout
        self.resize = resize
        self.loop_file = loop_file
        self.stale_threshold = stale_threshold
        self.is_file_source = self._is_file_source(stream_url)

        self.frame_queue: Queue = Queue(maxsize=queue_size)
        self.is_running = False
        self.thread: threading.Thread | None = None
        self._frame_lock = threading.Lock()
        self._latest_frame: Frame | None = None
        self.cap: cv2.VideoCapture | None = None
        self.frame_count = 0
        self.dropped_frames = 0

        # 마지막 프레임 수신 시각 (wall-clock)
        self.last_frame_time: float = 0.0

        # FPS 이동평균: 최근 _FPS_WINDOW 개 프레임 간격 저장
        self._frame_intervals: deque[float] = deque(maxlen=_FPS_WINDOW)
        self._last_interval_ts: float = 0.0
        self.fps_actual: float = 0.0

        # 재연결 텔레메트리
        self.reconnect_attempts: int = 0
        self.last_connect_time: float = 0.0
        self._consecutive_failures: int = 0

        logger.info(f"[{self.camera_id}] RTSPCapture 초기화 완료")

    # ------------------------------------------------------------------
    # 공개 인터페이스
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """스트림 수신 시작"""
        if self.is_running:
            logger.warning(f"[{self.camera_id}] 이미 실행 중입니다")
            return True

        self.is_running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        logger.info(f"[{self.camera_id}] 캡처 스레드 시작")
        return True

    def stop(self):
        """스트림 수신 중지"""
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=3.0)
        if self.cap:
            self.cap.release()
        logger.info(f"[{self.camera_id}] 캡처 중지")

    def get_frame(self, timeout: float = 1.0) -> Frame | None:
        """프레임 큐에서 프레임 획득"""
        try:
            return self.frame_queue.get(timeout=timeout)
        except Exception:
            return None

    def get_latest_frame(self) -> Frame | None:
        """가장 최신 프레임 조회 (처리 큐를 소비하지 않음)"""
        with self._frame_lock:
            return self._latest_frame

    def get_status(self) -> dict:
        """
        카메라 현재 상태 딕셔너리 반환.

        반환 필드:
            connected (bool): VideoCapture.isOpened() 결과
            queue_size (int): 현재 프레임 큐 깊이
            fps (float): 이동평균 기반 실 FPS
            frame_count (int): 누적 수신 프레임 수
            dropped_frames (int): 누적 폐기 프레임 수
            source_type (str): "file" | "stream"
            last_frame_time (float | None): 마지막 프레임 수신 wall-clock (time.time())
            seconds_since_last_frame (float | None): 수신 후 경과초 (미수신 시 None)
            is_stale (bool): 경과초 > stale_threshold 이면 True
            reconnect_attempts (int): 누적 재연결 시도 횟수
            last_connect_time (float | None): 마지막 연결 성공 wall-clock
            consecutive_failures (int): 연속 연결 실패 횟수 (성공 시 0 리셋)
        """
        now = time.time()
        lfp = self.last_frame_time if self.last_frame_time > 0 else None
        elapsed = (now - lfp) if lfp is not None else None
        is_stale = (elapsed is not None) and (elapsed > self.stale_threshold)

        return {
            "connected": self.is_connected,
            "queue_size": self.queue_size_current,
            "fps": round(self.fps_actual, 2),
            "frame_count": self.frame_count,
            "dropped_frames": self.dropped_frames,
            "source_type": "file" if self.is_file_source else "stream",
            # last_frame_time 관련
            "last_frame_time": lfp,
            "seconds_since_last_frame": round(elapsed, 3) if elapsed is not None else None,
            "is_stale": is_stale,
            # 재연결 텔레메트리
            "reconnect_attempts": self.reconnect_attempts,
            "last_connect_time": self.last_connect_time if self.last_connect_time > 0 else None,
            "consecutive_failures": self._consecutive_failures,
        }

    # ------------------------------------------------------------------
    # 프로퍼티
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """연결 상태 확인"""
        return self.cap is not None and self.cap.isOpened()

    @property
    def queue_size_current(self) -> int:
        """현재 큐 크기"""
        return self.frame_queue.qsize()

    @property
    def seconds_since_last_frame(self) -> float | None:
        """마지막 프레임 수신 후 경과초. 프레임 미수신 시 None."""
        if self.last_frame_time <= 0:
            return None
        return time.time() - self.last_frame_time

    def is_stale(self, threshold: float | None = None) -> bool:
        """
        마지막 프레임 수신 후 threshold초 초과 시 stale 판정.

        Args:
            threshold: 판정 기준초. None이면 인스턴스 stale_threshold 사용.
        """
        elapsed = self.seconds_since_last_frame
        if elapsed is None:
            return False
        limit = threshold if threshold is not None else self.stale_threshold
        return elapsed > limit

    # ------------------------------------------------------------------
    # 내부 루프
    # ------------------------------------------------------------------

    def _capture_loop(self):
        """메인 캡처 루프 (지수 백오프 재연결 포함)"""
        while self.is_running:
            if not self._connect():
                self._consecutive_failures += 1
                # 지수 백오프: reconnect_interval * 2^(failures-1), 상한 _BACKOFF_MAX
                backoff = min(
                    self.reconnect_interval * (2 ** (self._consecutive_failures - 1)),
                    _BACKOFF_MAX,
                )
                logger.info(
                    f"[{self.camera_id}] 재연결 대기 {backoff:.1f}초 "
                    f"(연속 실패 {self._consecutive_failures}회, "
                    f"누적 시도 {self.reconnect_attempts}회)"
                )
                time.sleep(backoff)
                continue

            # 연결 성공 — 연속 실패 카운터 초기화
            self._consecutive_failures = 0

            while self.is_running and self.cap.isOpened():
                success, frame = self.cap.read()
                if not success:
                    if self.is_file_source and self.loop_file:
                        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        logger.info(f"[{self.camera_id}] 로컬 비디오를 처음부터 재생합니다")
                        continue
                    # read 실패 또는 타임아웃 — 재연결 루프로 진입
                    logger.warning(f"[{self.camera_id}] 프레임 읽기 실패 — 재연결 시도")
                    break

                self._on_frame_received()
                self.frame_count += 1

                # 리사이즈
                if self.resize:
                    frame = cv2.resize(frame, self.resize)

                # 프레임 객체 생성
                frame_obj = Frame(
                    camera_id=self.camera_id,
                    timestamp=datetime.now(),
                    image=frame,
                    frame_number=self.frame_count,
                )
                with self._frame_lock:
                    self._latest_frame = frame_obj

                # 큐 풀 방어
                if self.frame_queue.full():
                    try:
                        self.frame_queue.get_nowait()
                        self.dropped_frames += 1
                        if self.dropped_frames % 100 == 0:
                            logger.warning(
                                f"[{self.camera_id}] 프레임 큐 오버플로: "
                                f"누적 {self.dropped_frames}프레임 폐기"
                            )
                    except Exception:
                        pass

                with contextlib.suppress(Exception):
                    self.frame_queue.put_nowait(frame_obj)

                # FPS 제어
                time.sleep(1.0 / self.target_fps)

            if self.cap:
                self.cap.release()
                self.cap = None

    def _on_frame_received(self):
        """
        프레임 수신 시 공통 처리:
        - last_frame_time 갱신
        - fps_actual 이동평균 업데이트
        """
        now = time.time()

        if self._last_interval_ts > 0:
            interval = now - self._last_interval_ts
            # 비정상적으로 큰 간격(재연결 등)은 이동평균에서 제외
            if 0 < interval < 60.0:
                self._frame_intervals.append(interval)

        self._last_interval_ts = now
        self.last_frame_time = now

        # 이동평균 FPS: 누적 간격이 없으면 0 유지
        if self._frame_intervals:
            avg_interval = sum(self._frame_intervals) / len(self._frame_intervals)
            self.fps_actual = 1.0 / avg_interval if avg_interval > 0 else 0.0
        else:
            self.fps_actual = 0.0

    def _connect(self) -> bool:
        """
        RTSP/파일 연결 시도.

        FFMPEG 백엔드에서 CAP_PROP_OPEN_TIMEOUT_MSEC 및
        CAP_PROP_READ_TIMEOUT_MSEC를 self.timeout 기반으로 설정해
        죽은 스트림에서의 무한 블록을 방지한다.
        """
        self.reconnect_attempts += 1
        try:
            if self.cap:
                self.cap.release()

            timeout_ms = int(self.timeout * 1000)

            if self.is_file_source:
                # 로컬 파일은 타임아웃이 불필요하므로 기본 백엔드로 연다.
                self.cap = cv2.VideoCapture(self.stream_url)
            else:
                # RTSP/스트림: open 타임아웃은 반드시 생성자 params로 전달해야
                # 실제 적용된다. 빈 VideoCapture()에 set(CAP_PROP_OPEN_TIMEOUT_MSEC)
                # 후 open()하는 방식은 일부 OpenCV 빌드에서 set이 무시되어
                # 죽은 RTSP에서 컴파일 기본값(약 30초)까지 블록된다.
                self.cap = cv2.VideoCapture(
                    self.stream_url,
                    cv2.CAP_FFMPEG,
                    [
                        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms,
                        cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms,
                    ],
                )
            self.cap.setExceptionMode(False)

            if not self.cap.isOpened():
                logger.error(
                    f"[{self.camera_id}] 스트림 연결 실패 "
                    f"(시도 #{self.reconnect_attempts}): {self._safe_stream_url}"
                )
                return False

            # 읽기 타임아웃 재적용(생성자에서 설정되지만 백엔드별 보강) + 버퍼/FPS
            self.cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)

            if self.is_file_source:
                source_fps = self.cap.get(cv2.CAP_PROP_FPS)
                if source_fps and source_fps > 0:
                    self.target_fps = int(round(source_fps))

            self.last_connect_time = time.time()
            # 재연결 후 FPS 윈도우 초기화 (이전 스트림의 간격 오염 방지)
            self._frame_intervals.clear()
            self._last_interval_ts = 0.0

            source_name = "파일" if self.is_file_source else "RTSP/스트림"
            logger.info(
                f"[{self.camera_id}] {source_name} 연결 성공 "
                f"(누적 시도 {self.reconnect_attempts}회): {self._safe_stream_url}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.camera_id}] 연결 오류 (시도 #{self.reconnect_attempts}): {e}")
            return False

    # ------------------------------------------------------------------
    # 정적/클래스 메서드
    # ------------------------------------------------------------------

    @staticmethod
    def _is_file_source(stream_url: str) -> bool:
        """로컬 비디오 파일인지 확인"""
        suffix = Path(stream_url).suffix.lower()
        return suffix in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}

    @property
    def _safe_stream_url(self) -> str:
        """로그에 계정/비밀번호가 노출되지 않도록 URL 마스킹"""
        try:
            parsed = urlsplit(self.stream_url)
            if parsed.username or parsed.password:
                host = parsed.hostname or ""
                if parsed.port:
                    host = f"{host}:{parsed.port}"
                return urlunsplit(
                    (parsed.scheme, f"***:***@{host}", parsed.path, parsed.query, parsed.fragment)
                )
        except Exception:
            pass
        return self.stream_url


class CameraManager:
    """
    다중 카메라 관리자
    """

    def __init__(self):
        self.cameras: dict[str, RTSPCapture] = {}
        self.logger = logging.getLogger(__name__)

    def add_camera(self, camera_id: str, stream_url: str, **kwargs) -> RTSPCapture:
        """카메라 추가"""
        if camera_id in self.cameras:
            raise ValueError(f"카메라 {camera_id}는 이미 존재합니다")

        capture = RTSPCapture(
            camera_id=camera_id,
            stream_url=stream_url,
            **kwargs,
        )
        self.cameras[camera_id] = capture
        return capture

    def start_all(self):
        """모든 카메라 시작"""
        for camera_id, capture in self.cameras.items():
            capture.start()
            self.logger.info(f"[{camera_id}] 시작됨")

    def stop_all(self):
        """모든 카메라 중지"""
        for capture in self.cameras.values():
            capture.stop()
        self.logger.info("모든 카메라 중지")

    def get_camera(self, camera_id: str) -> RTSPCapture | None:
        """카메라 접근"""
        return self.cameras.get(camera_id)

    def get_status(self) -> dict:
        """모든 카메라 상태 (RTSPCapture.get_status() 위임)"""
        return {camera_id: cap.get_status() for camera_id, cap in self.cameras.items()}
