"""
알람 관리 모듈

이벤트 수신 → 로깅 → 이미지 저장 → 알람 발송 (SMTP)
"""
import logging
import json
import shutil
import smtplib
import os
import time
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

import cv2

from src.pipeline.pipeline import SafetyEvent
from src.utils.config import mask_text  # Phase 8 M5.3 S3: URL 마스킹

logger = logging.getLogger(__name__)

@dataclass
class AlertConfig:
    """알람 설정"""
    enabled: bool = True
    log_file: str = "./logs/alerts.log"
    console_output: bool = True
    save_images: bool = True
    image_dir: str = "./data/captures"
    image_retention_days: int = 30
    max_images_per_event: int = 3
    blur_faces: bool = True
    face_blur_strength: int = 35
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5
    batch_flush_interval: float = 0.0  # seconds; 0 = disabled (immediate)
    batch_max_size: int = 100


class AlertManager:
    """
    알람 관리자
    
    이벤트 수신 시:
    1. 로그 파일 기록
    2. 이미지 캡처 저장
    3. 콘솔 출력
    4. SMTP 이메일 발송 (설정 시)
    """
    
    def __init__(self, config: AlertConfig):
        self.config = config
        self.event_count = 0
        
        # Batch processing buffer (Phase 8 M5.2)
        self._batch_buffer: List[SafetyEvent] = []
        self._batch_timer = None
        self.batch_flush_interval = config.batch_flush_interval
        self.batch_max_size = config.batch_max_size
        
        # 디렉토리 생성
        Path(config.log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(config.image_dir).mkdir(parents=True, exist_ok=True)
        
        # 로그 핸들러 설정
        self._setup_logging()
        
        # 캡처 디렉토리
        self.capture_dir = Path(config.image_dir)
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.face_cascade = self._load_face_cascade() if config.blur_faces else None
        
        # SMTP 설정 로드
        self.smtp_config = self._load_smtp_config()

        # 알림 환경설정 로드 (Phase 8 M5.2)
        self._alert_prefs = self._load_alert_preferences()

        logger.info("AlertManager 초기화 완료")
    
    def _load_smtp_config(self) -> Optional[dict]:
        """SMTP 환경변수에서 설정 로드"""
        host = os.environ.get("KSI_CCTV_SMTP_HOST", "").strip()
        if not host:
            return None
        return {
            "host": host,
            "port": int(os.environ.get("KSI_CCTV_SMTP_PORT", "587")),
            "user": os.environ.get("KSI_CCTV_SMTP_USER", "").strip(),
            "password": os.environ.get("KSI_CCTV_SMTP_PASSWORD", "").strip(),
            "recipients": [
                r.strip()
                for r in os.environ.get("KSI_CCTV_SMTP_RECIPIENTS", "").split(",")
                if r.strip()
            ],
        }
    
    def _setup_logging(self):
        """로깅 설정"""
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # 파일 핸들러
        file_handler = RotatingFileHandler(
            self.config.log_file,
            maxBytes=self.config.log_max_bytes,
            backupCount=self.config.log_backup_count,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        
        # 콘솔 핸들러
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)
        
        # 루트 로거에 추가
        alert_logger = logging.getLogger('alerts')
        alert_logger.setLevel(logging.INFO)
        alert_logger.handlers.clear()
        alert_logger.addHandler(file_handler)
        alert_logger.addHandler(console_handler)
        
        self.alert_logger = alert_logger
    
    def handle_event(self, event: SafetyEvent):
        """이벤트 처리 (배치 모드 시 일정 간격/크기로 flush)"""
        if not self.config.enabled:
            return

        self.event_count += 1

        # 이미지 저장은 항상 즉시 (지연하면 안 됨)
        if self.config.save_images and event.image is not None:
            self._save_image(event)

        # 배치 모드: 버퍼에 추가 후 threshold 도달 시 flush
        if self.batch_flush_interval > 0:
            self._batch_buffer.append(event)
            self._maybe_flush_batch()
        else:
            # 즉시 모드 (기존 동작)
            self._log_event(event)
            self._send_email(event)

    def _maybe_flush_batch(self) -> None:
        """배치 flush 조건 확인 — 크기 또는 시간 도달 시 flush"""
        if not self._batch_buffer:
            return
        now = time.time()
        if self._batch_timer is None:
            self._batch_timer = now  # 첫 이벤트에서 타이머 시작
        size_full = len(self._batch_buffer) >= self.batch_max_size
        time_full = (now - self._batch_timer) >= self.batch_flush_interval
        if size_full or time_full:
            self._flush_batch()

    def _flush_batch(self) -> None:
        """버퍼의 모든 이벤트를 일괄 처리 (로그 + 이메일)"""
        if not self._batch_buffer:
            return
        events = self._batch_buffer[:]
        self._batch_buffer.clear()
        self._batch_timer = time.time()

        # 배치 로그 기록
        self._log_batch(events)

        # 배치 이메일 발송 (medium+)
        self._send_batch_email(events)

    def _log_batch(self, events: List[SafetyEvent]) -> None:
        """배치 이벤트 일괄 로깅 (URL 마스킹 적용)"""
        for event in events:
            log_data = event.to_dict()
            log_line = json.dumps(log_data, ensure_ascii=False, default=str)
            # Phase 8 M5.3 S3: RTSP/HTTP URL이 포함될 수 있는 description 등을 마스킹
            log_line = mask_text(log_line)
            self.alert_logger.info(f"EVENT | {log_line}")
        if self.config.console_output:
            print(f"\n📦 배치 알람: {len(events)}건 처리 완료")

    def _send_batch_email(self, events: List[SafetyEvent]) -> None:
        """배치 이벤트 일괄 이메일 발송 (alert_level 임계값 + per-event 설정 적용)"""
        if not self.smtp_config or not self.smtp_config["recipients"]:
            return
        # alert_level + per-event 설정 필터링 (_should_send_email 동일 로직)
        sendable = [e for e in events if self._should_send_email(e)]
        if not sendable:
            return
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.smtp_config["user"]
            msg["To"] = ", ".join(self.smtp_config["recipients"])
            msg["Subject"] = f"[KSI-CCTV] 배치 알람 {len(sendable)}건"

            body_plain = f"총 {len(sendable)}건의 알람이 발생했습니다.\n"
            for e in sendable:
                body_plain += (
                    f"- {e.timestamp.strftime('%H:%M:%S')} [{e.camera_id}] "
                    f"{e.event_type} (level: {getattr(e, 'alert_level', 'low')})\n"
                )
            body_html = (
                f"<html><body style='font-family: sans-serif; padding: 20px;'>"
                f"<h2>📦 KSI-CCTV 배치 알람 ({len(sendable)}건)</h2>"
                f"<table style='border-collapse: collapse; width: 100%;'>"
                f"<tr><th>시각</th><th>카메라</th><th>이벤트</th><th>Level</th></tr>"
            )
            for e in sendable:
                level = getattr(e, "alert_level", "low")
                color = {"medium": "#ff7f0e", "high": "#d62728", "critical": "#8c0000"}.get(level, "#2ca02c")
                body_html += (
                    f"<tr><td>{e.timestamp.strftime('%H:%M:%S')}</td>"
                    f"<td>{e.camera_id}</td><td>{e.event_type}</td>"
                    f"<td><span style='background-color: {color}; color: white; padding: 2px 6px;'>{level.upper()}</span></td></tr>"
                )
            body_html += "</table></body></html>"

            msg.attach(MIMEText(body_plain, "plain", "utf-8"))
            msg.attach(MIMEText(body_html, "html", "utf-8"))

            with smtplib.SMTP(self.smtp_config["host"], self.smtp_config["port"]) as server:
                server.starttls()
                if self.smtp_config["user"] and self.smtp_config["password"]:
                    server.login(self.smtp_config["user"], self.smtp_config["password"])
                server.send_message(msg)
            logger.info(f"배치 SMTP 알람 발송 완료: {len(sendable)}건")
        except Exception as exc:
            logger.error(f"배치 SMTP 알람 발송 실패: {exc}")
    
    def _load_alert_preferences(self) -> Dict[str, Any]:
        """`config/alert_preferences.yaml` 로드 — 실패 시 기본값"""
        prefs_path = Path("./config/alert_preferences.yaml")
        if not prefs_path.exists():
            return {"email_alert_level_threshold": "medium", "per_event_email_enabled": {}}
        try:
            from src.utils.config import ConfigLoader
            return ConfigLoader.load_yaml(str(prefs_path))
        except Exception:
            return {"email_alert_level_threshold": "medium", "per_event_email_enabled": {}}

    def _should_send_email(self, event: SafetyEvent) -> bool:
        """이메일을 발송할지 결정 — alert_level 임계값 + per-event 설정"""
        alert_level = getattr(event, "alert_level", "low")
        # alert_level 순서
        level_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        threshold = self._alert_prefs.get("email_alert_level_threshold", "medium")
        if level_order.get(alert_level, 0) < level_order.get(threshold, 1):
            return False
        # per-event 설정
        event_type = getattr(event, "event_type", "")
        per_event = self._alert_prefs.get("per_event_email_enabled", {})
        if event_type in per_event and not per_event[event_type]:
            return False
        return True

    def _send_email(self, event: SafetyEvent):
        """SMTP 알람 이메일 발송 (HTML + alert_level 필터링 + per-event 설정)"""
        if not self.smtp_config or not self.smtp_config["recipients"]:
            return

        # alert_level + per-event 설정 필터링
        if not self._should_send_email(event):
            return

        alert_level = getattr(event, "alert_level", "low")
        
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.smtp_config["user"]
            msg["To"] = ", ".join(self.smtp_config["recipients"])
            msg["Subject"] = f"[KSI-CCTV] {event.event_type} - {event.camera_id}"
            
            # Plain text
            body_plain = (
                f"이벤트: {event.event_type}\n"
                f"카메라: {event.camera_id}\n"
                f"시각: {event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"신뢰도: {event.confidence:.2f}\n"
                f"alert_level: {alert_level}\n"
            )
            
            # HTML with alert_level badge
            level_colors = {
                "low": "#2ca02c",
                "medium": "#ff7f0e",
                "high": "#d62728",
                "critical": "#8c0000",
            }
            level_color = level_colors.get(alert_level, "#2ca02c")
            
            body_html = f"""
            <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h2>🚨 KSI-CCTV 알람</h2>
                <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
                    <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>이벤트</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">{event.event_type}</td></tr>
                    <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>카메라</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">{event.camera_id}</td></tr>
                    <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>시각</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">{event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
                    <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>신뢰도</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">{event.confidence:.2f}</td></tr>
                    <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>alert_level</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">
                            <span style="background-color: {level_color}; color: white; padding: 4px 8px; border-radius: 4px;">
                                {alert_level.upper()}
                            </span>
                        </td></tr>
                </table>
                <p style="margin-top: 20px; color: #666; font-size: 12px;">
                    KSI-CCTV 자동 알람 시스템
                </p>
            </body>
            </html>
            """
            
            msg.attach(MIMEText(body_plain, "plain", "utf-8"))
            msg.attach(MIMEText(body_html, "html", "utf-8"))
            
            with smtplib.SMTP(
                self.smtp_config["host"], self.smtp_config["port"]
            ) as server:
                server.starttls()
                if self.smtp_config["user"] and self.smtp_config["password"]:
                    server.login(self.smtp_config["user"], self.smtp_config["password"])
                server.send_message(msg)
            logger.info(f"SMTP 알람 발송 완료: {event.event_type} ({alert_level})")
        except Exception as e:
            logger.error(f"SMTP 알람 발송 실패: {e}")
    
    def _log_event(self, event: SafetyEvent):
        """이벤트 로깅 (URL 마스킹 적용)"""
        log_data = event.to_dict()
        log_line = json.dumps(log_data, ensure_ascii=False, default=str)
        # Phase 8 M5.3 S3: RTSP/HTTP URL 마스킹
        log_line = mask_text(log_line)

        self.alert_logger.info(f"EVENT | {log_line}")

        if self.config.console_output:
            print(f"\n🚨 알람! [{event.camera_id}] {event.event_type} "
                  f"(신뢰도: {event.confidence:.2f})")
    
    def _save_image(self, event: SafetyEvent):
        """이벤트 이미지 저장"""
        if event.image is None:
            return
        
        # 날짜별 디렉토리
        date_str = event.timestamp.strftime('%Y%m%d')
        event_dir = self.capture_dir / date_str / event.camera_id
        event_dir.mkdir(parents=True, exist_ok=True)
        
        # 파일명
        filename = f"{event.event_type}_{event.timestamp.strftime('%H%M%S')}.jpg"
        filepath = event_dir / filename
        
        # 이미지 저장
        try:
            image = self._prepare_image_for_storage(event.image)
            cv2.imwrite(str(filepath), image)
            logger.info(f"이미지 저장 완료: {filepath}")
        except Exception as e:
            logger.error(f"이미지 저장 실패: {e}")

    def _load_face_cascade(self):
        """OpenCV 기본 얼굴 검출기 로드"""
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(str(cascade_path))
        if cascade.empty():
            logger.warning("얼굴 블러링 검출기를 로드하지 못했습니다")
            return None
        return cascade

    def _prepare_image_for_storage(self, image):
        """저장 전 개인정보 보호 처리"""
        if not self.config.blur_faces or self.face_cascade is None:
            return image

        protected = image.copy()
        gray = cv2.cvtColor(protected, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))

        kernel = max(3, int(self.config.face_blur_strength))
        if kernel % 2 == 0:
            kernel += 1

        for (x, y, w, h) in faces:
            roi = protected[y:y + h, x:x + w]
            if roi.size == 0:
                continue
            protected[y:y + h, x:x + w] = cv2.GaussianBlur(roi, (kernel, kernel), 0)

        return protected
    
    def get_event_log(self, limit: int = 100) -> List[dict]:
        """최근 이벤트 로그 조회"""
        events = []
        try:
            with open(self.config.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # JSON 파싱
            for line in lines[-limit:]:
                try:
                    parts = line.split(' | ')
                    if len(parts) >= 3 and 'EVENT' in parts[2]:
                        json_part = parts[2].split(' | ', 1)[1] if ' | ' in parts[2] else parts[2]
                        event = json.loads(json_part)
                        events.append(event)
                except Exception:
                    continue
            
        except FileNotFoundError:
            pass
        
        return events
    
    def get_captured_images(self, date: Optional[str] = None) -> List[Path]:
        """캡처된 이미지 목록"""
        if date:
            target_dir = self.capture_dir / date
        else:
            target_dir = self.capture_dir
        
        if not target_dir.exists():
            return []
        
        images = []
        for dirpath in target_dir.rglob('*'):
            if dirpath.is_file() and dirpath.suffix.lower() in {'.jpg', '.jpeg', '.png'}:
                images.append(dirpath)
        
        return sorted(images, reverse=True)
    
    def cleanup_old_images(self):
        """오래된 이미지 정리"""
        if self.config.image_retention_days <= 0:
            return
        
        cutoff = datetime.now().timestamp() - (self.config.image_retention_days * 86400)
        deleted = 0
        
        for date_dir in self.capture_dir.iterdir():
            if not date_dir.is_dir():
                continue
            
            # 디렉토리의 최근 수정 시간 확인
            try:
                mtime = date_dir.stat().st_mtime
                if mtime < cutoff:
                    shutil.rmtree(date_dir)
                    deleted += 1
            except Exception as e:
                logger.error(f"정리 실패: {e}")
        
        if deleted > 0:
            logger.info(f"{deleted}개 오래된 디렉토리 정리 완료")
