"""
알람 관리 모듈

이벤트 수신 → 로깅 → 이미지 저장 → 알람 발송 (SMTP)

변경 이력:
- Phase 8 M5.3 S3: URL 마스킹 (mask_text)
- Phase alerts: SMTP timeout/SSL, 이미지 암호화, cascade 다중, 날짜 기반 cleanup, VLM 필드 포함
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import smtplib
import time
from dataclasses import dataclass
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import cv2

from src.pipeline.pipeline import SafetyEvent
from src.utils.config import mask_text  # Phase 8 M5.3 S3: URL 마스킹
from src.utils.privacy import blur_faces as _blur_faces  # #11: 얼굴 블러 SSOT
from src.utils.privacy import get_face_cascades

logger = logging.getLogger(__name__)

# PII/자격증명이 실제로 들어올 수 있는 자유서술 필드. mask_text를 로그 라인 전체가 아니라
# 이 필드들에만 적용해, event_id·bbox·timestamp 같은 수치/구조 필드가 정규식 오탐으로
# 손상되는 것을 막는다(#7 — 감사 로그 식별값 정합성 보존).
_MASKED_TEXT_FIELDS = ("description", "vlm_raw_response", "vlm_recommended_action", "rule_name")


def _mask_event_payload(log_data: dict) -> dict:
    """로그 직렬화 전 자유서술 필드의 PII/자격증명만 마스킹한 사본을 반환한다.

    구조/수치 필드는 건드리지 않는다(오탐 치환 방지). vlm_detected_events(문자열 리스트)도 처리.
    """
    masked = dict(log_data)
    for key in _MASKED_TEXT_FIELDS:
        val = masked.get(key)
        if isinstance(val, str) and val:
            masked[key] = mask_text(val)
    events = masked.get("vlm_detected_events")
    if isinstance(events, list):
        masked["vlm_detected_events"] = [mask_text(e) if isinstance(e, str) else e for e in events]
    return masked


# 암호화 선택적 import (cryptography 패키지가 설치된 경우에만 활성화)
try:
    from cryptography.fernet import Fernet, InvalidToken
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False
    Fernet = None  # type: ignore[assignment,misc]
    InvalidToken = Exception  # type: ignore[assignment,misc]


def parse_alert_log_line(line: str) -> dict | None:
    """`alerts.log` 한 줄에서 EVENT JSON payload를 파싱한다.

    실제 formatter 출력은 `timestamp | level | EVENT | {json}` 형태이며,
    과거 파서는 split index가 맞지 않아 JSON payload를 읽지 못했다.
    """
    if not line or "EVENT |" not in line:
        return None
    try:
        payload = line.split("EVENT |", 1)[1].strip()
        if not payload:
            return None
        return json.loads(payload)
    except Exception:
        return None


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
    # 이미지 암호화 저장 옵션 (Phase alerts M6.2)
    # True로 설정하면 KSI_CCTV_CAPTURE_KEY 환경변수에서 Fernet 키를 읽어 암호화 저장
    encrypt_images: bool = False


class AlertManager:
    """
    알람 관리자

    이벤트 수신 시:
    1. 로그 파일 기록
    2. 이미지 캡처 저장 (암호화 옵션 지원)
    3. 콘솔 출력
    4. SMTP 이메일 발송 (설정 시, SSL/STARTTLS 분기, timeout 지원)
    """

    # SMTP 기본 연결 timeout (초) — hang 방지
    SMTP_DEFAULT_TIMEOUT = 10

    def __init__(self, config: AlertConfig):
        self.config = config
        self.event_count = 0

        # Batch processing buffer (Phase 8 M5.2)
        self._batch_buffer: list[SafetyEvent] = []
        self._batch_timer: float | None = None
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

        # 얼굴 블러링용 cascade 로드 (정면 + 측면 병행 — CCTV 각도 대응)
        self._face_cascades = self._load_face_cascades() if config.blur_faces else []

        # 하위 호환: .face_cascade 속성 (단일 cascade 참조)
        self.face_cascade = self._face_cascades[0] if self._face_cascades else None

        # 이미지 암호화 키 로드 (Phase alerts M6.2)
        self._fernet = self._load_fernet_key() if config.encrypt_images else None

        # SMTP 설정 로드
        self.smtp_config = self._load_smtp_config()

        # 알림 환경설정 로드 (Phase 8 M5.2)
        self._alert_prefs = self._load_alert_preferences()

        # 오래된 캡처 이미지 주기적 정리 throttle (handle_event에서 호출)
        self._last_image_cleanup = 0.0
        self._image_cleanup_interval_sec = 6 * 3600  # 6시간마다 최대 1회

        logger.info("AlertManager 초기화 완료")

    # ------------------------------------------------------------------
    # SMTP 설정 / 연결 헬퍼
    # ------------------------------------------------------------------

    def _load_smtp_config(self) -> dict | None:
        """SMTP 환경변수에서 설정 로드.

        추가 환경변수:
        - KSI_CCTV_SMTP_USE_SSL: 'true'면 SMTP_SSL(465) 사용, 기본 STARTTLS(587)
        - KSI_CCTV_SMTP_TIMEOUT: SMTP 연결 timeout(초), 기본 10
        """
        host = os.environ.get("KSI_CCTV_SMTP_HOST", "").strip()
        if not host:
            return None

        use_ssl_str = os.environ.get("KSI_CCTV_SMTP_USE_SSL", "false").strip().lower()
        use_ssl = use_ssl_str in ("1", "true", "yes")

        try:
            smtp_timeout = float(os.environ.get("KSI_CCTV_SMTP_TIMEOUT", str(self.SMTP_DEFAULT_TIMEOUT)))
        except ValueError:
            smtp_timeout = self.SMTP_DEFAULT_TIMEOUT

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
            # 새 필드: SSL 모드 + timeout
            "use_ssl": use_ssl,
            "timeout": smtp_timeout,
        }

    def _smtp_connection(self):
        """SMTP 연결을 반환하는 컨텍스트 매니저 팩토리.

        KSI_CCTV_SMTP_USE_SSL=true → SMTP_SSL(implicit SSL, 보통 포트 465)
        그 외 → SMTP + STARTTLS(보통 포트 587)

        모든 연결에 timeout을 지정해 hang 방지.
        """
        cfg = self.smtp_config
        host = cfg["host"]
        port = cfg["port"]
        timeout = cfg.get("timeout", self.SMTP_DEFAULT_TIMEOUT)
        use_ssl = cfg.get("use_ssl", False)

        if use_ssl:
            # Implicit SSL (465): SMTP_SSL, starttls() 불필요
            return smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            # STARTTLS (587): 평문 연결 후 STARTTLS 업그레이드
            return smtplib.SMTP(host, port, timeout=timeout)

    def _smtp_send(self, msg) -> None:
        """SMTP 연결을 열고 메시지를 발송하는 공통 헬퍼.

        use_ssl에 따라 SMTP_SSL / SMTP+STARTTLS 분기 처리.
        인증 정보가 있는 경우에만 login().
        """
        cfg = self.smtp_config
        # 호출처(_send_email/_send_batch_email)가 'if not self.smtp_config: return'으로
        # 가드하므로 여기 도달 시 cfg는 None이 아니다(불변식 명시 — mypy/방어용).
        assert cfg is not None
        use_ssl = cfg.get("use_ssl", False)

        with self._smtp_connection() as server:
            if not use_ssl:
                # STARTTLS 업그레이드
                server.starttls()
            if cfg["user"] and cfg["password"]:
                server.login(cfg["user"], cfg["password"])
            server.send_message(msg)

    # ------------------------------------------------------------------
    # 이미지 암호화 / 복호화
    # ------------------------------------------------------------------

    def _load_fernet_key(self):
        """KSI_CCTV_CAPTURE_KEY 환경변수에서 Fernet 키를 로드한다.

        키가 없거나 cryptography 패키지가 없으면 None 반환(평문 fallback).
        """
        if not HAS_CRYPTOGRAPHY:
            logger.warning(
                "encrypt_images=True이지만 cryptography 패키지가 없습니다. "
                "pip install cryptography 후 재시작하세요. 평문 저장으로 fallback."
            )
            return None

        raw_key = os.environ.get("KSI_CCTV_CAPTURE_KEY", "").strip()
        if not raw_key:
            logger.warning(
                "encrypt_images=True이지만 KSI_CCTV_CAPTURE_KEY 환경변수가 없습니다. "
                "평문 저장으로 fallback."
            )
            return None

        try:
            return Fernet(raw_key.encode())
        except Exception as exc:
            logger.error("Fernet 키 로드 실패 (평문 fallback): %s", exc)
            return None

    def encrypt_image_bytes(self, image_bytes: bytes) -> bytes:
        """이미지 바이트를 Fernet으로 암호화한다. 키가 없으면 원본 반환."""
        if self._fernet is None:
            return image_bytes
        return self._fernet.encrypt(image_bytes)

    def decrypt_image_bytes(self, data: bytes) -> bytes:
        """Fernet 암호화 데이터를 복호화한다. 실패하면 원본 반환."""
        if self._fernet is None:
            return data
        try:
            return self._fernet.decrypt(data)
        except (InvalidToken, Exception) as exc:
            logger.error("이미지 복호화 실패: %s", exc)
            return data

    # ------------------------------------------------------------------
    # 로깅
    # ------------------------------------------------------------------

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
        for handler in list(alert_logger.handlers):
            handler.close()
        alert_logger.handlers.clear()
        alert_logger.addHandler(file_handler)
        alert_logger.addHandler(console_handler)

        self.alert_logger = alert_logger

    # ------------------------------------------------------------------
    # 이벤트 처리 흐름
    # ------------------------------------------------------------------

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

        # 오래된 캡처 이미지 주기적 정리 (디스크 풀 방지)
        self._maybe_cleanup_images()

    def _maybe_cleanup_images(self) -> None:
        """오래된 캡처 이미지를 주기적으로 정리한다(throttle).

        handle_event마다 호출되지만 _image_cleanup_interval_sec 간격으로만 실제
        cleanup_old_images()를 실행해 매 이벤트 디스크 스캔 비용을 피한다.
        """
        if self.config.image_retention_days <= 0:
            return
        now = time.time()
        if now - self._last_image_cleanup < self._image_cleanup_interval_sec:
            return
        self._last_image_cleanup = now
        try:
            self.cleanup_old_images()
        except Exception as exc:
            logger.debug("이미지 정리 실패: %s", exc)

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

    def _log_batch(self, events: list[SafetyEvent]) -> None:
        """배치 이벤트 일괄 로깅 (자유서술 필드 PII/URL 마스킹)"""
        for event in events:
            log_data = _mask_event_payload(event.to_dict())
            log_line = json.dumps(log_data, ensure_ascii=False, default=str)
            self.alert_logger.info(f"EVENT | {log_line}")
        if self.config.console_output:
            print(f"\n배치 알람: {len(events)}건 처리 완료")

    # ------------------------------------------------------------------
    # 이메일 발송
    # ------------------------------------------------------------------

    def _load_alert_preferences(self) -> dict[str, Any]:
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
        return not (event_type in per_event and not per_event[event_type])

    def _build_vlm_plain(self, event: SafetyEvent) -> str:
        """VLM 분석 결과를 평문 이메일용 텍스트로 반환한다."""
        lines = []
        risk = getattr(event, "vlm_risk_level", None)
        action = getattr(event, "vlm_recommended_action", None)
        parse_ok = getattr(event, "vlm_parse_success", None)
        detected = getattr(event, "vlm_detected_events", None)

        if parse_ok is None:
            # VLM 미실행. 정책이 VLM 확인을 요구(vlm_needed)했는데 미실행이면 'AI 미확인'을 명시한다
            # (provider 미가동/큐 drop 등 — 공개 모델 false positive가 AI 확인 없이 알람될 수 있음).
            if getattr(event, "vlm_needed", False):
                return "⚠️ VLM 미확인 — AI 2차 확인 없이 발생(VLM 미가동/지연). 수동 확인 필요."
            return ""
        if not parse_ok:
            lines.append("VLM 분석: 파싱 실패")
        else:
            if risk:
                lines.append(f"VLM 위험도: {risk}")
            if detected:
                lines.append(f"VLM 감지 이벤트: {', '.join(detected)}")
            if action:
                lines.append(f"권장 조치: {action}")
        return "\n".join(lines)

    def _build_vlm_html_rows(self, event: SafetyEvent) -> str:
        """VLM 분석 결과를 HTML 테이블 행으로 반환한다."""
        risk = getattr(event, "vlm_risk_level", None)
        action = getattr(event, "vlm_recommended_action", None)
        parse_ok = getattr(event, "vlm_parse_success", None)
        detected = getattr(event, "vlm_detected_events", None)

        if parse_ok is None:
            return ""

        rows = ""
        td_style = "padding: 8px; border: 1px solid #ddd;"

        if not parse_ok:
            rows += (
                f"<tr><td style='{td_style}'><strong>VLM 분석</strong></td>"
                f"<td style='{td_style}'>파싱 실패</td></tr>"
            )
        else:
            risk_colors = {
                "low": "#2ca02c", "medium": "#ff7f0e",
                "high": "#d62728", "critical": "#8c0000",
            }
            if risk:
                color = risk_colors.get(risk, "#666")
                rows += (
                    f"<tr><td style='{td_style}'><strong>VLM 위험도</strong></td>"
                    f"<td style='{td_style}'>"
                    f"<span style='background-color:{color};color:white;padding:2px 6px;border-radius:3px;'>"
                    f"{risk.upper()}</span></td></tr>"
                )
            if detected:
                rows += (
                    f"<tr><td style='{td_style}'><strong>VLM 감지 이벤트</strong></td>"
                    f"<td style='{td_style}'>{', '.join(detected)}</td></tr>"
                )
            if action:
                rows += (
                    f"<tr><td style='{td_style}'><strong>권장 조치</strong></td>"
                    f"<td style='{td_style}'>{action}</td></tr>"
                )
        return rows

    def _send_batch_email(self, events: list[SafetyEvent]) -> None:
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
                vlm_plain = self._build_vlm_plain(e)
                if vlm_plain:
                    for ln in vlm_plain.splitlines():
                        body_plain += f"  {ln}\n"

            body_html = (
                f"<html><body style='font-family: sans-serif; padding: 20px;'>"
                f"<h2>KSI-CCTV 배치 알람 ({len(sendable)}건)</h2>"
                f"<table style='border-collapse: collapse; width: 100%;'>"
                f"<tr><th>시각</th><th>카메라</th><th>이벤트</th><th>Level</th><th>VLM 위험도</th></tr>"
            )
            for e in sendable:
                level = getattr(e, "alert_level", "low")
                color = {"medium": "#ff7f0e", "high": "#d62728", "critical": "#8c0000"}.get(level, "#2ca02c")
                vlm_risk = getattr(e, "vlm_risk_level", "") or ""
                body_html += (
                    f"<tr><td>{e.timestamp.strftime('%H:%M:%S')}</td>"
                    f"<td>{e.camera_id}</td><td>{e.event_type}</td>"
                    f"<td><span style='background-color: {color}; color: white; padding: 2px 6px;'>{level.upper()}</span></td>"
                    f"<td>{vlm_risk.upper() if vlm_risk else '-'}</td></tr>"
                )
            body_html += "</table></body></html>"

            msg.attach(MIMEText(body_plain, "plain", "utf-8"))
            msg.attach(MIMEText(body_html, "html", "utf-8"))

            # 공통 헬퍼 사용 (timeout + SSL 분기)
            self._smtp_send(msg)
            logger.info(f"배치 SMTP 알람 발송 완료: {len(sendable)}건")
        except Exception as exc:
            logger.error(f"배치 SMTP 알람 발송 실패: {exc}")

    def _send_email(self, event: SafetyEvent):
        """SMTP 알람 이메일 발송 (HTML + alert_level 필터링 + per-event 설정 + VLM 필드)"""
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
            # VLM 필드 추가
            vlm_plain = self._build_vlm_plain(event)
            if vlm_plain:
                body_plain += vlm_plain + "\n"

            # HTML with alert_level badge
            level_colors = {
                "low": "#2ca02c",
                "medium": "#ff7f0e",
                "high": "#d62728",
                "critical": "#8c0000",
            }
            level_color = level_colors.get(alert_level, "#2ca02c")
            td_style = "padding: 8px; border: 1px solid #ddd;"

            # VLM HTML 행
            vlm_rows = self._build_vlm_html_rows(event)

            body_html = f"""
            <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h2>KSI-CCTV 알람</h2>
                <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
                    <tr><td style="{td_style}"><strong>이벤트</strong></td>
                        <td style="{td_style}">{event.event_type}</td></tr>
                    <tr><td style="{td_style}"><strong>카메라</strong></td>
                        <td style="{td_style}">{event.camera_id}</td></tr>
                    <tr><td style="{td_style}"><strong>시각</strong></td>
                        <td style="{td_style}">{event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
                    <tr><td style="{td_style}"><strong>신뢰도</strong></td>
                        <td style="{td_style}">{event.confidence:.2f}</td></tr>
                    <tr><td style="{td_style}"><strong>alert_level</strong></td>
                        <td style="{td_style}">
                            <span style="background-color: {level_color}; color: white; padding: 4px 8px; border-radius: 4px;">
                                {alert_level.upper()}
                            </span>
                        </td></tr>
                    {vlm_rows}
                </table>
                <p style="margin-top: 20px; color: #666; font-size: 12px;">
                    KSI-CCTV 자동 알람 시스템
                </p>
            </body>
            </html>
            """

            msg.attach(MIMEText(body_plain, "plain", "utf-8"))
            msg.attach(MIMEText(body_html, "html", "utf-8"))

            # 공통 헬퍼 사용 (timeout + SSL 분기)
            self._smtp_send(msg)
            logger.info(f"SMTP 알람 발송 완료: {event.event_type} ({alert_level})")
        except Exception as e:
            logger.error(f"SMTP 알람 발송 실패: {e}")

    # ------------------------------------------------------------------
    # 이벤트 로깅
    # ------------------------------------------------------------------

    def _log_event(self, event: SafetyEvent):
        """이벤트 로깅 (자유서술 필드 PII/URL 마스킹)"""
        log_data = _mask_event_payload(event.to_dict())
        log_line = json.dumps(log_data, ensure_ascii=False, default=str)

        self.alert_logger.info(f"EVENT | {log_line}")

        if self.config.console_output:
            print(f"\n알람! [{event.camera_id}] {event.event_type} "
                  f"(신뢰도: {event.confidence:.2f})")

    # ------------------------------------------------------------------
    # 이미지 저장 / 얼굴 블러링 / 암호화
    # ------------------------------------------------------------------

    def _save_image(self, event: SafetyEvent):
        """이벤트 이미지 저장.

        encrypt_images=True이고 Fernet 키가 있으면 .jpg.enc로 암호화 저장.
        키가 없으면 경고 후 평문 .jpg로 저장.
        """
        if event.image is None:
            return

        # 날짜별 디렉토리
        date_str = event.timestamp.strftime('%Y%m%d')
        event_dir = self.capture_dir / date_str / event.camera_id
        event_dir.mkdir(parents=True, exist_ok=True)

        # 파일명
        base_name = f"{event.event_type}_{event.timestamp.strftime('%H%M%S')}"

        try:
            image = self._prepare_image_for_storage(event.image)

            if self.config.encrypt_images and self._fernet is not None:
                # 암호화 경로: JPG 인코딩 → Fernet 암호화 → .jpg.enc
                ok, buf = cv2.imencode(".jpg", image)
                if not ok:
                    raise RuntimeError("cv2.imencode 실패")
                encrypted = self._fernet.encrypt(bytes(buf))
                filepath = event_dir / f"{base_name}.jpg.enc"
                filepath.write_bytes(encrypted)
                logger.info("암호화 이미지 저장 완료: %s", filepath)
            else:
                # 평문 경로 (기존 동작, 또는 키 미설정 fallback)
                filepath = event_dir / f"{base_name}.jpg"
                cv2.imwrite(str(filepath), image)
                logger.info("이미지 저장 완료: %s", filepath)
        except Exception as e:
            logger.error("이미지 저장 실패: %s", e)

    def _load_face_cascades(self) -> list:
        """얼굴 검출기를 로드한다(공용 SSOT src.utils.privacy에 위임).

        저장(여기)과 대시보드 표시가 동일한 cascade/블러 로직을 쓰도록 분리했다(#11).
        반환: 로드에 성공한 CascadeClassifier 목록 (빈 목록이면 블러링 비활성)
        """
        return get_face_cascades()

    def _prepare_image_for_storage(self, image):
        """저장 전 개인정보 보호 처리(공용 SSOT src.utils.privacy.blur_faces에 위임).

        대시보드 표시 경로와 동일한 블러 로직을 공유한다(#11).
        """
        if not self.config.blur_faces or not self._face_cascades:
            return image
        return _blur_faces(image, self._face_cascades, self.config.face_blur_strength)

    # ------------------------------------------------------------------
    # 이미지 조회 / 정리
    # ------------------------------------------------------------------

    def get_event_log(self, limit: int = 100) -> list[dict]:
        """최근 이벤트 로그 조회"""
        events = []
        try:
            with open(self.config.log_file, encoding='utf-8') as f:
                lines = f.readlines()

            for line in lines[-limit:]:
                event = parse_alert_log_line(line)
                if event is not None:
                    events.append(event)

        except FileNotFoundError:
            pass

        return events

    def get_captured_images(self, date: str | None = None) -> list[Path]:
        """캡처된 이미지 목록.

        암호화 저장 시 .jpg.enc 파일도 포함한다.
        """
        target_dir = self.capture_dir / date if date else self.capture_dir

        if not target_dir.exists():
            return []

        images = []
        for dirpath in target_dir.rglob('*'):
            if dirpath.is_file() and dirpath.suffix.lower() in {'.jpg', '.jpeg', '.png', '.enc'}:
                images.append(dirpath)

        return sorted(images, reverse=True)

    def cleanup_old_images(self):
        """오래된 이미지를 보관 기간 기준으로 정리한다.

        디렉토리명(YYYYMMDD)을 우선 파싱해 날짜를 결정한다.
        파싱이 불가능한 디렉토리는 st_mtime fallback.

        기존 동작(mtime 단독)의 문제: 디렉토리에 파일이 나중에 추가/이동되면
        mtime이 갱신돼 보관기간을 넘긴 데이터가 삭제되지 않을 수 있었다.
        디렉토리명 기반은 OS mtime에 영향받지 않는 신뢰 가능한 날짜 기준이다.
        """
        if self.config.image_retention_days <= 0:
            return

        cutoff_date = date.fromtimestamp(
            datetime.now().timestamp() - self.config.image_retention_days * 86400
        )
        deleted = 0

        for date_dir in self.capture_dir.iterdir():
            if not date_dir.is_dir():
                continue

            # 디렉토리명(YYYYMMDD)으로 날짜 파싱 시도
            dir_date: date | None = None
            with contextlib.suppress(ValueError):
                dir_date = datetime.strptime(date_dir.name, "%Y%m%d").date()

            try:
                if dir_date is not None:
                    # 디렉토리명 기반 날짜 판정 (mtime 무관)
                    should_delete = dir_date < cutoff_date
                else:
                    # 날짜 디렉토리가 아닌 경우 mtime fallback
                    mtime = date_dir.stat().st_mtime
                    cutoff_ts = datetime.now().timestamp() - self.config.image_retention_days * 86400
                    should_delete = mtime < cutoff_ts

                if should_delete:
                    shutil.rmtree(date_dir)
                    deleted += 1
            except Exception as e:
                logger.error("정리 실패: %s", e)

        if deleted > 0:
            logger.info("%d개 오래된 디렉토리 정리 완료", deleted)
