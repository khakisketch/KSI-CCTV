"""
RTSP 스트림 수신 모듈

멀티스레딩 기반 RTSP 스트림 수신 및 프레임 큐 관리
"""
import logging
import threading
import time
from queue import Queue
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import cv2
import numpy as np
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

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
    """
    
    def __init__(
        self,
        camera_id: str,
        stream_url: str,
        queue_size: int = 30,
        target_fps: int = 30,
        reconnect_interval: float = 5.0,
        timeout: float = 10.0,
        resize: Optional[Tuple[int, int]] = None,
        loop_file: bool = True
    ):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.queue_size = queue_size
        self.target_fps = target_fps
        self.reconnect_interval = reconnect_interval
        self.timeout = timeout
        self.resize = resize
        self.loop_file = loop_file
        self.is_file_source = self._is_file_source(stream_url)
        
        self.frame_queue: Queue = Queue(maxsize=queue_size)
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[Frame] = None
        self.cap: Optional[cv2.VideoCapture] = None
        self.frame_count = 0
        self.dropped_frames = 0
        self.last_frame_time = time.time()
        self.fps_actual = 0.0
        
        logger.info(f"[{self.camera_id}] RTSPCapture 초기화 완료")
    
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
    
    def get_frame(self, timeout: float = 1.0) -> Optional[Frame]:
        """프레임 큐에서 프레임 획득"""
        try:
            return self.frame_queue.get(timeout=timeout)
        except Exception:
            return None
    
    def get_latest_frame(self) -> Optional[Frame]:
        """가장 최신 프레임 조회 (처리 큐를 소비하지 않음)"""
        with self._frame_lock:
            return self._latest_frame
    
    def _capture_loop(self):
        """메인 캡처 루프"""
        while self.is_running:
            if not self._connect():
                time.sleep(self.reconnect_interval)
                continue
            
            while self.is_running and self.cap.isOpened():
                success, frame = self.cap.read()
                if not success:
                    if self.is_file_source and self.loop_file:
                        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        logger.info(f"[{self.camera_id}] 로컬 비디오를 처음부터 재생합니다")
                        continue
                    logger.warning(f"[{self.camera_id}] 프레임 읽기 실패")
                    break
                
                self.frame_count += 1
                now = time.time()
                self.fps_actual = 1.0 / (now - self.last_frame_time) if self.last_frame_time else 30.0
                self.last_frame_time = now
                
                # 리사이즈
                if self.resize:
                    frame = cv2.resize(frame, self.resize)
                
                # 프레임 객체 생성
                frame_obj = Frame(
                    camera_id=self.camera_id,
                    timestamp=datetime.now(),
                    image=frame,
                    frame_number=self.frame_count
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
                
                try:
                    self.frame_queue.put_nowait(frame_obj)
                except Exception:
                    pass
                
                # FPS 제어
                time.sleep(1.0 / self.target_fps)
            
            if self.cap:
                self.cap.release()
                self.cap = None
    
    def _connect(self) -> bool:
        """RTSP 연결"""
        try:
            if self.cap:
                self.cap.release()
            
            self.cap = cv2.VideoCapture(self.stream_url)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)
            
            if not self.cap.isOpened():
                logger.error(f"[{self.camera_id}] 스트림 연결 실패: {self._safe_stream_url}")
                return False
            
            source_name = "파일" if self.is_file_source else "RTSP/스트림"
            if self.is_file_source:
                source_fps = self.cap.get(cv2.CAP_PROP_FPS)
                if source_fps and source_fps > 0:
                    self.target_fps = int(round(source_fps))
            logger.info(f"[{self.camera_id}] {source_name} 연결 성공: {self._safe_stream_url}")
            return True
            
        except Exception as e:
            logger.error(f"[{self.camera_id}] 연결 오류: {e}")
            return False
    
    @property
    def is_connected(self) -> bool:
        """연결 상태 확인"""
        return self.cap is not None and self.cap.isOpened()
    
    @property
    def queue_size_current(self) -> int:
        """현재 큐 크기"""
        return self.frame_queue.qsize()

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
                return urlunsplit((parsed.scheme, f"***:***@{host}", parsed.path, parsed.query, parsed.fragment))
        except Exception:
            pass
        return self.stream_url


class CameraManager:
    """
    다중 카메라 관리자
    """
    
    def __init__(self):
        self.cameras: Dict[str, RTSPCapture] = {}
        self.logger = logging.getLogger(__name__)
    
    def add_camera(self, camera_id: str, stream_url: str, **kwargs) -> RTSPCapture:
        """카메라 추가"""
        if camera_id in self.cameras:
            raise ValueError(f"카메라 {camera_id}는 이미 존재합니다")
        
        capture = RTSPCapture(
            camera_id=camera_id,
            stream_url=stream_url,
            **kwargs
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
    
    def get_camera(self, camera_id: str) -> Optional[RTSPCapture]:
        """카메라 접근"""
        return self.cameras.get(camera_id)
    
    def get_status(self) -> Dict:
        """모든 카메라 상태"""
        return {
            camera_id: {
                "connected": cap.is_connected,
                "queue_size": cap.queue_size_current,
                "fps": cap.fps_actual,
                "frame_count": cap.frame_count,
                "dropped_frames": cap.dropped_frames,
                "source_type": "file" if cap.is_file_source else "stream"
            }
            for camera_id, cap in self.cameras.items()
        }
