"""
Alert Manager Module

Event processing: logging -> image storage -> alert dispatch (SMTP).

Changes:
- Phase 8 M5.3 S3: URL masking (mask_text)
- Phase alerts: SMTP timeout/SSL, image encryption, cascade multiple, date-based cleanup, VLM fields
- Phase 1: Notification outbox worker for email delivery (retry/dedup/idempotency)
"""
from __future__ import annotations

import contextlib
import hashlib
import html
import json
import logging
import os
import re
import shutil
import smtplib
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from email import policy
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import Parser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Protocol

import cv2

# Notification outbox (Phase 1)
from src.alerts.outbox import (
    NotificationAttempt,
    NotificationChannel,
    NotificationDispatcher,
    OutboxWorker,
)
from src.pipeline.pipeline import SafetyEvent
from src.utils.config import mask_text  # Phase 8 M5.3 S3: URL masking
from src.utils.privacy import blur_faces as _blur_faces  # #11: face blur SSOT
from src.utils.privacy import get_face_cascades

logger = logging.getLogger(__name__)

_CAMERA_HEALTH_EVENTS = {"camera_disconnect", "tamper"}
_CAMERA_RECOVERY_EVENTS = {"camera_recovered", "tamper_recovered"}
_SAFE_CAPTURE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")

# PII/credentials may appear in free-text fields. mask_text applied only to these
# fields in log lines, preserving structural/numeric fields like event_id, bbox,
# timestamp from regex false positives (#7 - audit log identifier integrity).
_MASKED_TEXT_FIELDS = ("description", "vlm_raw_response", "vlm_recommended_action", "rule_name")


class EventRepository(Protocol):
    """Minimal durable event sink contract (implemented by SQLiteEventRepository)."""

    def record(self, event: dict[str, Any]) -> int: ...


class DurableStoreStatus(Protocol):
    def record_failure(self, error: Exception, *, operation: str = "query") -> None: ...

    def record_success(self, *, operation: str = "query") -> None: ...


def _mask_event_payload(log_data: dict) -> dict:
    """Return copy with free-text fields PII/URL masked.

    Structured/numeric fields untouched (false positive protection).
    vlm_detected_events (string list) also processed.
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


# Optional crypto import (cryptography package required for encrypt_images=True)
try:
    from cryptography.fernet import Fernet, InvalidToken
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False
    Fernet = None  # type: ignore[assignment,misc]
    InvalidToken = Exception  # type: ignore[assignment,misc]


def parse_alert_log_line(line: str) -> dict | None:
    """Parse one line of `alerts.log` for EVENT JSON payload.

    Formatter outputs `timestamp | level | EVENT | {json}`.
    Old parser used wrong split index and missed JSON payload.
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
    """Alert configuration"""
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
    # Image encryption storage option (Phase alerts M6.2)
    # True: KSI_CCTV_CAPTURE_KEY env var required for Fernet key
    encrypt_images: bool = True
    outbox_dir: str | None = None


class AlertManager:
    """
    Alert manager

    On event receipt:
    1. Log file recording
    2. Image capture storage (encryption option)
    3. Console output
    4. SMTP email dispatch (configurable, SSL/STARTTLS, timeout)
    """

    # SMTP default connection timeout (seconds) - hang prevention
    SMTP_DEFAULT_TIMEOUT = 10

    def __init__(
        self,
        config: AlertConfig,
        event_repository: EventRepository | None = None,
        incident_store: Any | None = None,
        durable_store_status: DurableStoreStatus | None = None,
    ):
        self.config = config
        self.event_repository = event_repository
        self.incident_store = incident_store
        self.durable_store_status = durable_store_status
        self.event_count = 0

        # Batch processing buffer (Phase 8 M5.2)
        self._batch_buffer: list[SafetyEvent] = []
        self._batch_timer: float | None = None
        self.batch_flush_interval = config.batch_flush_interval
        self.batch_max_size = config.batch_max_size
        self._batch_lock = threading.RLock()
        self._batch_flush_timer: threading.Timer | None = None

        # Directories
        Path(config.log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(config.image_dir).mkdir(parents=True, exist_ok=True)

        # Log handler
        self._setup_logging()

        # Capture directory
        self.capture_dir = Path(config.image_dir)
        self.capture_dir.mkdir(parents=True, exist_ok=True)

        # Face blurring cascades (frontal + profile for CCTV angles)
        self._face_cascades = self._load_face_cascades() if config.blur_faces else []
        if config.blur_faces and not self._face_cascades:
            raise RuntimeError(
                "blur_faces=True requires at least one available face detector"
            )

        # Backwards compat: .face_cascade attribute (single cascade reference)
        self.face_cascade = self._face_cascades[0] if self._face_cascades else None

        # Image encryption key load (Phase alerts M6.2)
        self._fernet = self._load_fernet_key() if config.encrypt_images and config.save_images else None
        if config.encrypt_images and config.save_images and self._fernet is None:
            raise RuntimeError(
                "encrypt_images=True requires cryptography and a valid "
                "KSI_CCTV_CAPTURE_KEY"
            )

        # SMTP config load
        self.smtp_config = self._load_smtp_config()

        # Alert preferences load (Phase 8 M5.2)
        self._alert_prefs = self._load_alert_preferences()

        # Notification outbox (Phase 1)
        self._dispatcher = NotificationDispatcher()
        self._dispatcher.register(NotificationChannel.EMAIL, self._send_email_direct)
        outbox_dir = config.outbox_dir or str(Path(config.log_file).parent / "outbox")
        self._outbox = OutboxWorker(
            outbox_dir=outbox_dir,
            dispatcher=self._dispatcher,
        )
        self._outbox.start()

        # Batch buffer
        self._batch_buffer: list[SafetyEvent] = []
        self._batch_timer: float | None = None
        self.batch_flush_interval = config.batch_flush_interval
        self.batch_max_size = config.batch_max_size

        logger.info("AlertManager init complete (OutboxWorker included)")

        # Image cleanup throttle
        self._last_image_cleanup = 0.0
        self._image_cleanup_interval_sec = 6 * 3600
        self._image_cleanup_lock = threading.RLock()
        self._image_cleanup_timer: threading.Timer | None = None
        self._image_cleanup_shutdown = False
        self._start_image_cleanup()

    # ------------------------------------------------------------------
    # Logging setup
    # ------------------------------------------------------------------

    def _setup_logging(self):
        """Configure logging handlers."""
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # File handler
        file_handler = RotatingFileHandler(
            self.config.log_file,
            maxBytes=self.config.log_max_bytes,
            backupCount=self.config.log_backup_count,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)

        # Add to alerts logger
        alert_logger = logging.getLogger('alerts')
        alert_logger.setLevel(logging.INFO)
        for handler in list(alert_logger.handlers):
            handler.close()
        alert_logger.handlers.clear()
        alert_logger.addHandler(file_handler)
        alert_logger.addHandler(console_handler)

        self.alert_logger = alert_logger

    # ------------------------------------------------------------------
    # SMTP config / connection helpers
    # ------------------------------------------------------------------

    def _load_smtp_config(self) -> dict | None:
        """Load SMTP config from env vars.

        Additional env vars:
        - KSI_CCTV_SMTP_USE_SSL: true for SMTP_SSL(465), default STARTTLS(587)
        - KSI_CCTV_SMTP_TIMEOUT: SMTP connection timeout(sec), default 10
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
            # New fields: SSL mode + timeout
            "use_ssl": use_ssl,
            "timeout": smtp_timeout,
        }

    def _smtp_connection(self):
        """Context manager factory returning SMTP connection.

        KSI_CCTV_SMTP_USE_SSL=true -> SMTP_SSL (implicit SSL, usually port 465)
        Else -> SMTP + STARTTLS (usually port 587)

        All connections specify timeout to prevent hangs.
        """
        cfg = self.smtp_config
        host = cfg["host"]
        port = cfg["port"]
        timeout = cfg.get("timeout", self.SMTP_DEFAULT_TIMEOUT)
        use_ssl = cfg.get("use_ssl", False)

        if use_ssl:
            # Implicit SSL (465): SMTP_SSL, starttls() not needed
            return smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            # STARTTLS (587): plain connect then STARTTLS upgrade
            return smtplib.SMTP(host, port, timeout=timeout)

    def _smtp_send(self, msg) -> None:
        """Open SMTP connection and send message.

        Branches by use_ssl for SMTP_SSL / SMTP+STARTTLS.
        Logins only if credentials provided.
        """
        cfg = self.smtp_config
        # Callers (_send_email/_send_batch_email) guard with 'if not self.smtp_config: return'
        # so cfg is not None here (invariant - mypy/defensive).
        assert cfg is not None
        use_ssl = cfg.get("use_ssl", False)

        with self._smtp_connection() as server:
            if not use_ssl:
                # STARTTLS upgrade
                server.starttls()
            if cfg["user"] and cfg["password"]:
                server.login(cfg["user"], cfg["password"])
            server.send_message(msg)

    # ------------------------------------------------------------------
    # Image encryption / decryption
    # ------------------------------------------------------------------

    def _load_fernet_key(self):
        """Load Fernet key from KSI_CCTV_CAPTURE_KEY env var.

        Returns None if key or the cryptography package is unavailable. The
        caller fails startup rather than silently writing plaintext evidence.
        """
        if not HAS_CRYPTOGRAPHY:
            logger.warning(
                "encrypt_images=True but cryptography package not installed. "
                "pip install cryptography then restart."
            )
            return None

        raw_key = os.environ.get("KSI_CCTV_CAPTURE_KEY", "").strip()
        if not raw_key:
            logger.warning(
                "encrypt_images=True but KSI_CCTV_CAPTURE_KEY env var not set. "
                "Evidence encryption cannot start."
            )
            return None

        try:
            return Fernet(raw_key.encode())
        except Exception as exc:
            logger.error("Fernet key load failed: %s", exc)
            return None

    def encrypt_image_bytes(self, image_bytes: bytes) -> bytes:
        """Encrypt image bytes with Fernet."""
        if self._fernet is None:
            raise RuntimeError("capture encryption key is unavailable")
        return self._fernet.encrypt(image_bytes)

    def decrypt_image_bytes(self, data: bytes) -> bytes:
        """Decrypt Fernet-encrypted data. Return original on failure."""
        if self._fernet is None:
            return data
        try:
            return self._fernet.decrypt(data)
        except (InvalidToken, Exception) as exc:
            logger.error("Image decryption failed: %s", exc)
            return data

    # ------------------------------------------------------------------
    # Alert preferences
    # ------------------------------------------------------------------

    def _load_alert_preferences(self) -> dict[str, Any]:
        """Load `config/alert_preferences.yaml` - defaults on failure"""
        prefs_path = Path("./config/alert_preferences.yaml")
        if not prefs_path.exists():
            return {"email_alert_level_threshold": "medium", "per_event_email_enabled": {}}
        try:
            from src.utils.config import ConfigLoader
            return ConfigLoader.load_yaml(str(prefs_path))
        except Exception:
            return {"email_alert_level_threshold": "medium", "per_event_email_enabled": {}}

    def _should_send_email(self, event: SafetyEvent) -> bool:
        """Decide whether to email - alert_level threshold + per-event setting"""
        alert_level = getattr(event, "alert_level", "low")
        # alert_level ordering
        level_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        threshold = self._alert_prefs.get("email_alert_level_threshold", "medium")
        if level_order.get(alert_level, 0) < level_order.get(threshold, 1):
            return False
        # per-event setting
        event_type = getattr(event, "event_type", "")
        per_event = self._alert_prefs.get("per_event_email_enabled", {})
        return not (event_type in per_event and not per_event[event_type])

    def _build_vlm_plain(self, event: SafetyEvent) -> str:
        """Format VLM analysis for plaintext email."""
        lines = []
        risk = getattr(event, "vlm_risk_level", None)
        action = getattr(event, "vlm_recommended_action", None)
        parse_ok = getattr(event, "vlm_parse_success", None)
        detected = getattr(event, "vlm_detected_events", None)

        if parse_ok is None:
            # VLM not run. Policy requires VLM but didn't run -> flag as unconfirmed
            # (provider down/queue drop - public model false positive could alert without AI confirm).
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
        """VLM analysis as HTML table rows."""
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
                    f"<tr><td style='{td_style}'><strong>VLM risk</strong></td>"
                    f"<td style='{td_style}'>"
                    f"<span style='background-color:{color};color:white;padding:2px 6px;border-radius:3px;'>"
                    f"{html.escape(str(risk).upper())}</span></td></tr>"
                )
            if detected:
                rows += (
                    f"<tr><td style='{td_style}'><strong>VLM detected</strong></td>"
                    f"<td style='{td_style}'>{', '.join(html.escape(str(value)) for value in detected)}</td></tr>"
                )
            if action:
                rows += (
                    f"<tr><td style='{td_style}'><strong>Recommended</strong></td>"
                    f"<td style='{td_style}'>{html.escape(str(action))}</td></tr>"
                )
        return rows

    # ------------------------------------------------------------------
    # Event handling flow
    # ------------------------------------------------------------------

    def handle_event(self, event: SafetyEvent):
        """Process event (batch mode flushes at threshold)."""
        if not self.config.enabled:
            return False

        self.event_count += 1

        # Image save is always immediate
        if self.config.save_images and event.image is not None:
            event.evidence_image = self._save_image(event)

        # Life-safety provisional events must not be delayed by batch settings.
        durable_accept = False
        if self.batch_flush_interval > 0 and not event.provisional_alert:
            with self._batch_lock:
                self._batch_buffer.append(event)
                if self._batch_flush_timer is None:
                    self._batch_timer = time.time()
                    self._batch_flush_timer = threading.Timer(
                        self.batch_flush_interval,
                        self._flush_batch,
                    )
                    self._batch_flush_timer.daemon = True
                    self._batch_flush_timer.start()
            self._maybe_flush_batch()
        else:
            # Immediate mode. Persist provisional delivery before logging its state.
            if event.provisional_alert:
                durable_accept = self._enqueue_email(event)
                if durable_accept and not event.provisional_sent:
                    event.provisional_sent = True
                    event.provisional_sent_at = datetime.now()
            self._log_event(event)
            if not event.provisional_alert:
                durable_accept = self._enqueue_email(event)

        # Cleanup old captures periodically
        self._maybe_cleanup_images()
        return durable_accept

    def _maybe_cleanup_images(self) -> None:
        """Clean up old capture images periodically (throttle).

        Event handling and the periodic timer share this method. The lock keeps
        shutdown from racing an in-progress filesystem cleanup.
        """
        with self._image_cleanup_lock:
            if self._image_cleanup_shutdown or self.config.image_retention_days <= 0:
                return
            now = time.time()
            if now - self._last_image_cleanup < self._image_cleanup_interval_sec:
                return
            self._last_image_cleanup = now
            try:
                self.cleanup_old_images()
            except Exception as exc:
                logger.debug("Image cleanup failed: %s", exc)

    def _start_image_cleanup(self) -> None:
        """Run the startup retention sweep and schedule future sweeps."""
        self._maybe_cleanup_images()
        self._schedule_image_cleanup()

    def _schedule_image_cleanup(self) -> None:
        """Schedule one cleanup sweep while the manager remains active."""
        if self.config.image_retention_days <= 0:
            return
        with self._image_cleanup_lock:
            if self._image_cleanup_shutdown:
                return
            timer = threading.Timer(
                self._image_cleanup_interval_sec,
                self._run_scheduled_image_cleanup,
            )
            timer.daemon = True
            self._image_cleanup_timer = timer
            timer.start()

    def _run_scheduled_image_cleanup(self) -> None:
        """Perform a timer-triggered sweep and schedule its successor."""
        with self._image_cleanup_lock:
            self._image_cleanup_timer = None
            if self._image_cleanup_shutdown:
                return
        self._maybe_cleanup_images()
        self._schedule_image_cleanup()

    def _maybe_flush_batch(self) -> None:
        """Check batch flush condition — size or time threshold."""
        with self._batch_lock:
            if not self._batch_buffer:
                return
            now = time.time()
            if self._batch_timer is None:
                self._batch_timer = now
            size_full = len(self._batch_buffer) >= self.batch_max_size
            time_full = (now - self._batch_timer) >= self.batch_flush_interval
        if size_full or time_full:
            self._flush_batch()

    def _flush_batch(self) -> None:
        """Flush all buffered events (log + email)."""
        with self._batch_lock:
            if not self._batch_buffer:
                return
            latest_by_event_id: dict[str, SafetyEvent] = {}
            event_order: list[str] = []
            for event in self._batch_buffer:
                if event.event_id not in latest_by_event_id:
                    event_order.append(event.event_id)
                latest_by_event_id[event.event_id] = event
            events = [latest_by_event_id[event_id] for event_id in event_order]
            self._batch_buffer.clear()
            self._batch_timer = None
            timer = self._batch_flush_timer
            self._batch_flush_timer = None
            if timer is not None and timer is not threading.current_thread():
                timer.cancel()

        # Batch log
        self._log_batch(events)

        # Batch email
        self._enqueue_batch_email(events)

    def _log_batch(self, events: list[SafetyEvent]) -> None:
        """Batch log events (free-text fields PII/URL masked)."""
        for event in events:
            log_data = _mask_event_payload(event.to_dict())
            log_line = json.dumps(log_data, ensure_ascii=False, default=str)
            self.alert_logger.info(f"EVENT | {log_line}")
            self._persist_event(log_data)

        if self.config.console_output:
            print(f"\nBatch alarm: {len(events)} items processed")

    # Backwards compatibility
    def _send_email(self, event: SafetyEvent):
        """Legacy direct SMTP path. New event handling uses the durable outbox."""
        self._enqueue_email(event, direct=True)

    # ------------------------------------------------------------------
    # Outbox email enqueue (replaces direct SMTP)
    # ------------------------------------------------------------------

    def _enqueue_email(self, event: SafetyEvent, direct: bool = False):
        """Enqueue email to OutboxWorker (replaces direct SMTP)."""
        if not self.smtp_config or not self.smtp_config["recipients"]:
            return False

        # alert_level + per-event filter
        if not self._should_send_email(event):
            return False

        alert_level = getattr(event, "alert_level", "low")

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.smtp_config["user"]
            msg["To"] = ", ".join(self.smtp_config["recipients"])
            msg["Subject"] = f"[KSI-CCTV] {event.event_type} - {event.camera_id}"
            message_key = hashlib.sha256(event.event_id.encode("utf-8")).hexdigest()[:32]
            msg["Message-ID"] = f"<{message_key}@ksi-cctv.local>"

            # Plain text
            body_plain = (
                f"Event: {event.event_type}\n"
                f"Camera: {event.camera_id}\n"
                f"Time: {event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Confidence: {event.confidence:.2f}\n"
                f"alert_level: {alert_level}\n"
            )
            # VLM fields
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

            # VLM HTML rows
            vlm_rows = self._build_vlm_html_rows(event)

            body_html = f"""
            <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h2>KSI-CCTV Alarm</h2>
                <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
                    <tr><td style="{td_style}"><strong>Event</strong></td>
                        <td style="{td_style}">{event.event_type}</td></tr>
                    <tr><td style="{td_style}"><strong>Camera</strong></td>
                        <td style="{td_style}">{event.camera_id}</td></tr>
                    <tr><td style="{td_style}"><strong>Time</strong></td>
                        <td style="{td_style}">{event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
                    <tr><td style="{td_style}"><strong>Confidence</strong></td>
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
                    KSI-CCTV Auto Alarm System
                </p>
            </body>
            </html>
            """

            msg.attach(MIMEText(body_plain, "plain", "utf-8"))
            msg.attach(MIMEText(body_html, "html", "utf-8"))

            if direct:
                self._smtp_send(msg)
                return True

            attempt = NotificationAttempt(
                decision_id=f"{event.event_id}",
                channel=NotificationChannel.EMAIL,
                recipient=", ".join(self.smtp_config["recipients"]),
                idempotency_key=f"email_{event.event_id}",
                payload={"message": msg.as_string()},
            )
            accepted = self._outbox.enqueue(attempt)
            if not accepted:
                logger.warning("Email outbox enqueue rejected: %s", event.event_id)
            return accepted

        except Exception as e:
            logger.error(f"Email outbox enqueue failed: {e}")
            return False

    def notification_status(self) -> dict[str, Any]:
        """Expose delivery health without exposing SMTP credentials or recipients."""
        return {
            "email_configured": bool(self.smtp_config and self.smtp_config.get("recipients")),
            "recipient_count": len(self.smtp_config.get("recipients", [])) if self.smtp_config else 0,
            "outbox": self._outbox.status(),
        }

    def _enqueue_batch_email(self, events: list[SafetyEvent]) -> None:
        """Enqueue batch email to OutboxWorker (replaces direct SMTP)."""
        if not self.smtp_config or not self.smtp_config["recipients"]:
            return
        # alert_level + per-event filter
        sendable = [e for e in events if self._should_send_email(e)]
        if not sendable:
            return
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.smtp_config["user"]
            msg["To"] = ", ".join(self.smtp_config["recipients"])
            msg["Subject"] = f"[KSI-CCTV] Batch Alarm {len(sendable)} items"
            batch_source = "|".join(event.event_id for event in sendable)
            message_key = hashlib.sha256(batch_source.encode("utf-8")).hexdigest()[:32]
            msg["Message-ID"] = f"<{message_key}@ksi-cctv.local>"

            body_plain = f"Total {len(sendable)} alarms triggered.\n"
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
                f"<h2>KSI-CCTV Batch Alarm ({len(sendable)} items)</h2>"
                f"<table style='border-collapse: collapse; width: 100%;'>"
                f"<tr><th>Time</th><th>Camera</th><th>Event</th><th>Level</th><th>VLM Risk</th></tr>"
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

            batch_id = f"batch_{sendable[0].event_id}_{sendable[-1].event_id}_{len(sendable)}"
            attempt = NotificationAttempt(
                decision_id=batch_id,
                channel=NotificationChannel.EMAIL,
                recipient=", ".join(self.smtp_config["recipients"]),
                idempotency_key=f"email_{batch_id}",
                payload={"message": msg.as_string()},
            )
            if not self._outbox.enqueue(attempt):
                logger.warning("Batch email outbox enqueue rejected: %s", attempt.attempt_id)

        except Exception as exc:
            logger.error(f"Batch email outbox enqueue failed: {exc}")

    # ------------------------------------------------------------------
    # OutboxWorker direct email sender
    # ------------------------------------------------------------------

    def _send_email_direct(self, attempt: NotificationAttempt) -> bool:
        """OutboxWorker calls this to actually send email.

        Extracts message from attempt payload and sends via SMTP.
        Returns True on success, False on failure (triggers retry).
        """
        try:
            raw_message = attempt.payload.get("message")
            if not isinstance(raw_message, str) or not raw_message:
                logger.error("Email message missing from payload")
                return False
            msg = Parser(policy=policy.default).parsestr(raw_message)
            self._smtp_send(msg)
            logger.info(f"Outbox email sent: {attempt.attempt_id}")
            return True
        except Exception as e:
            logger.error(f"Outbox email send failed [{attempt.attempt_id}]: {e}")
            return False

    # ------------------------------------------------------------------
    # Event logging
    # ------------------------------------------------------------------

    def _log_event(self, event: SafetyEvent):
        """Log event (free-text fields PII/URL masked)"""
        log_data = _mask_event_payload(event.to_dict())
        log_line = json.dumps(log_data, ensure_ascii=False, default=str)

        self.alert_logger.info(f"EVENT | {log_line}")
        self._persist_event(log_data)

        if self.config.console_output:
            print(f"\nALARM! [{event.camera_id}] {event.event_type} "
                   f"(confidence: {event.confidence:.2f})")

    def _persist_event(self, log_data: dict[str, Any]) -> None:
        """Persist the already-masked payload without blocking legacy alert delivery.

        The rotating alert log remains a fallback and SSE source. SQLite persistence is
        intentionally best-effort at this boundary: a full/corrupt DB must not suppress
        life-safety email delivery, but the failure is logged loudly for operations.

        Incident projection is independent of durable event persistence: it runs
        whenever an incident_store is present, even without an event_repository.
        """
        if self.event_repository is not None:
            try:
                self.event_repository.record(log_data)
                if self.durable_store_status is not None:
                    self.durable_store_status.record_success(operation="write")
            except Exception as exc:
                if self.durable_store_status is not None:
                    self.durable_store_status.record_failure(exc, operation="write")
                logger.error("Durable event persistence failed: %s", type(exc).__name__)
        event_id = log_data.get("event_id")
        event_type = str(log_data.get("event_type") or "")
        camera_id = str(log_data.get("camera_id") or "")
        if self.incident_store is not None and event_id and event_type in _CAMERA_RECOVERY_EVENTS and camera_id:
            try:
                self.incident_store.mark_recovered(camera_id, str(event_id))
            except Exception as exc:
                logger.error("Camera incident recovery update failed: %s", type(exc).__name__)
        elif self.incident_store is not None and event_id and (
            log_data.get("alert_level") in {"high", "critical"} or event_type in _CAMERA_HEALTH_EVENTS
        ):
            try:
                self.incident_store.ensure(
                    str(event_id),
                    category="camera_health" if event_type in _CAMERA_HEALTH_EVENTS else "safety",
                    camera_id=camera_id or None,
                    source_event_type=event_type or None,
                    source_event_alert_level=str(log_data.get("alert_level") or "") or None,
                )
            except Exception as exc:
                logger.error("Durable incident creation failed: %s", type(exc).__name__)

    # ------------------------------------------------------------------
    # Image save / face blur / encryption
    # ------------------------------------------------------------------

    def _save_image(self, event: SafetyEvent) -> str | None:
        """Save event image.

        If encrypt_images=True, startup has already required a valid Fernet key
        and this method only writes encrypted .jpg.enc evidence.
        """
        if event.image is None:
            return None
        if not _SAFE_CAPTURE_SEGMENT.fullmatch(
            event.camera_id
        ) or not _SAFE_CAPTURE_SEGMENT.fullmatch(event.event_type):
            logger.error("Image save rejected unsafe event path segment")
            return None

        # Date directory
        date_str = event.timestamp.strftime('%Y%m%d')
        event_dir = self.capture_dir / date_str / event.camera_id
        event_dir.mkdir(parents=True, exist_ok=True)

        # Filename
        base_name = f"{event.event_type}_{event.timestamp.strftime('%H%M%S_%f')}"

        try:
            image = self._prepare_image_for_storage(event.image)

            if self.config.encrypt_images:
                if self._fernet is None:
                    raise RuntimeError("capture encryption key is unavailable")
                # Encrypt path: JPG encode -> Fernet encrypt -> .jpg.enc
                ok, buf = cv2.imencode(".jpg", image)
                if not ok:
                    raise RuntimeError("cv2.imencode failed")
                encrypted = self._fernet.encrypt(bytes(buf))
                filepath = event_dir / f"{base_name}.jpg.enc"
                filepath.write_bytes(encrypted)
                logger.info("Encrypted image saved: %s", filepath)
            else:
                # Plain path when encryption is explicitly disabled.
                filepath = event_dir / f"{base_name}.jpg"
                if not cv2.imwrite(str(filepath), image):
                    raise RuntimeError("cv2.imwrite failed")
                logger.info("Image saved: %s", filepath)
            return filepath.relative_to(self.capture_dir).as_posix()
        except Exception as e:
            logger.error("Image save failed: %s", e)
            return None

    def _load_face_cascades(self) -> list:
        """Load face detectors (shared SSOT src.utils.privacy).

        Storage (here) and dashboard display use same cascade/blur logic (#11).
        Returns: list of loaded CascadeClassifier. Startup rejects an empty list
        when face blurring is enabled.
        """
        return get_face_cascades()

    def _prepare_image_for_storage(self, image):
        """Pre-storage PII protection (shared SSOT src.utils.privacy.blur_faces).

        Dashboard display uses same cascade/blur logic (#11).
        """
        if not self.config.blur_faces:
            return image
        if not self._face_cascades:
            raise RuntimeError("face detector is unavailable")
        return _blur_faces(image, self._face_cascades, self.config.face_blur_strength)

    def prepare_image_for_display(self, image):
        """Apply the same configured privacy policy to a dashboard snapshot."""
        return self._prepare_image_for_storage(image)

    # ------------------------------------------------------------------
    # Image lookup / cleanup
    # ------------------------------------------------------------------

    def get_event_log(self, limit: int = 100) -> list[dict]:
        """Fetch recent event log (rotating-file fallback; SQLite is primary)."""
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
        """List captured images.

        Encrypted .jpg.enc files included.
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
        """Clean up old images by retention period.

        Directory name (YYYYMMDD) parsed for date.
        Unparsable directories fall back to st_mtime.

        Old mtime-only issue: files added/moved later update mtime,
        causing over-retention. Dirname-based is OS mtime independent.
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

            # Try parse dirname (YYYYMMDD)
            dir_date: date | None = None
            with contextlib.suppress(ValueError):
                dir_date = datetime.strptime(date_dir.name, "%Y%m%d").date()

            try:
                if dir_date is not None:
                    # Dirname-based decision (mtime independent)
                    should_delete = dir_date < cutoff_date
                else:
                    # Non-date dir fallback to mtime
                    mtime = date_dir.stat().st_mtime
                    cutoff_ts = datetime.now().timestamp() - self.config.image_retention_days * 86400
                    should_delete = mtime < cutoff_ts

                if should_delete:
                    shutil.rmtree(date_dir)
                    deleted += 1
            except Exception as e:
                logger.error("Cleanup failed: %s", e)

        if deleted > 0:
            logger.info("%d old directories cleaned", deleted)

    def shutdown(self):
        """Graceful shutdown"""
        with self._image_cleanup_lock:
            self._image_cleanup_shutdown = True
            timer = self._image_cleanup_timer
            self._image_cleanup_timer = None
            if timer is not None:
                timer.cancel()
        self._flush_batch()
        if self._outbox:
            self._outbox.stop()
        if self.event_repository is not None:
            try:
                close = getattr(self.event_repository, "close", None)
                if callable(close):
                    close()
            except Exception as exc:
                logger.error("Durable event repository close failed: %s", type(exc).__name__)


# ----------------------------------------------------------------------
# Compatibility exports
# ----------------------------------------------------------------------
__all__ = [
    "AlertConfig",
    "AlertManager",
    "parse_alert_log_line",
]
