"""P2-T1: WebhookEndpoint 모델 + HMAC 서명 발송자 테스트 (TDD Step 1)."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import Mock
from unittest.mock import patch as _patch

import pytest

from src.alerts.webhook import (
    build_webhook_payload,
    compute_signature,
    send_webhook,
    validate_webhook_url,
)
from src.events.models import (
    NotificationAttempt,
    NotificationChannel,
    WebhookEndpoint,
)

FAKE_SECRET = "test-secret-0123456789abcdef"
FAKE_BODY = b'{"version":"v1","event_id":"evt001"}'


def _make_endpoint(url: str = "https://hooks.example.com/ksi") -> WebhookEndpoint:
    return WebhookEndpoint(id="0123456789abcdef", name="test", url=url, secret=FAKE_SECRET)


def _make_attempt() -> NotificationAttempt:
    return NotificationAttempt(
        attempt_id="attempt001",
        decision_id="dec001",
        channel=NotificationChannel.WEBHOOK,
        recipient="0123456789abcdef",
        idempotency_key="webhook_0123456789abcdef_evt001",
        payload={"event_id": "evt001", "event_type": "intrusion", "alert_level": "high"},
    )


def test_hmac_vector_rfc4231_style() -> None:
    """RFC 4231 TC1 스타일 고정 벡터: key=0x0b*20, data='Hi There'."""
    key = b"\x0b" * 20
    expected = hmac.new(key, b"Hi There", hashlib.sha256).hexdigest()
    assert compute_signature(key.decode("latin-1"), b"Hi There") == expected
    assert compute_signature(FAKE_SECRET, FAKE_BODY) == hmac.new(
        FAKE_SECRET.encode("utf-8"), FAKE_BODY, hashlib.sha256
    ).hexdigest()


def test_to_public_dict_masks_secret() -> None:
    ep = _make_endpoint()
    public = ep.to_public_dict()
    assert public["secret"] == "***"
    assert public["id"] == "0123456789abcdef"
    assert public["url"] == "https://hooks.example.com/ksi"
    assert "created_at" in public and "rotated_at" in public
    assert FAKE_SECRET not in json.dumps(public)


def test_url_validation_rejects_credentials_and_non_http() -> None:
    with pytest.raises(ValueError):
        validate_webhook_url("https://user:pass@hooks.example.com/ksi")
    with pytest.raises(ValueError):
        validate_webhook_url("ftp://hooks.example.com/ksi")
    with pytest.raises(ValueError):
        validate_webhook_url("file:///etc/passwd")
    assert validate_webhook_url("https://hooks.example.com/ksi")
    assert validate_webhook_url("http://127.0.0.1:9000/hook")


def test_endpoint_rejects_bad_url_at_construction() -> None:
    with pytest.raises(ValueError):
        WebhookEndpoint(id="a" * 16, name="bad", url="ftp://x/y", secret="s")


def test_payload_v1_schema_keys() -> None:
    before = datetime.now(timezone.utc)
    payload = build_webhook_payload(_make_attempt())
    after = datetime.now(timezone.utc)
    assert payload["version"] == "v1"
    assert payload["event_id"] == "evt001"
    assert payload["idempotency_key"] == "webhook_0123456789abcdef_evt001"
    assert payload["type"] == "alert"
    sent_at = datetime.fromisoformat(payload["sent_at"])
    assert sent_at.tzinfo is not None
    assert before <= sent_at <= after
    assert payload["event"]["event_type"] == "intrusion"


def test_build_payload_rejects_invalid_message_type() -> None:
    with pytest.raises(ValueError):
        build_webhook_payload(_make_attempt(), "sms")
    assert build_webhook_payload(_make_attempt(), "test")["type"] == "test"


class _CaptureHandler(BaseHTTPRequestHandler):
    received: dict = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _CaptureHandler.received = {"headers": dict(self.headers), "body": body}
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


def _run_loopback_server() -> tuple[HTTPServer, threading.Thread]:
    server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_send_webhook_headers_and_delivery() -> None:
    server, _ = _run_loopback_server()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hook"
        ep = _make_endpoint(url=url)
        attempt = _make_attempt()
        assert send_webhook(attempt, ep, timeout_sec=5) is True
        received = _CaptureHandler.received
        # 헤더명은 requests가 Title-Case로 정규화하므로 소문자로 비교
        headers = {k.lower(): v for k, v in received["headers"].items()}
        assert "x-ksi-signature" in headers
        assert headers["x-ksi-delivery"] == "webhook_0123456789abcdef_evt001"
        assert headers["x-ksi-event"] == "intrusion"
        # X-KSI-Timestamp: 숫자 unix sec + 최근성(±60s)
        ts = int(headers["x-ksi-timestamp"])
        assert abs(time.time() - ts) <= 60
        expected_sig = hmac.new(FAKE_SECRET.encode(), received["body"], hashlib.sha256).hexdigest()
        assert headers["x-ksi-signature"] == expected_sig
        body = json.loads(received["body"])
        assert body["version"] == "v1"
        assert body["event_id"] == "evt001"
        assert body["idempotency_key"] == "webhook_0123456789abcdef_evt001"
        assert body["type"] == "alert"
        sent_at = datetime.fromisoformat(body["sent_at"])
        assert sent_at.tzinfo is not None
        assert abs((datetime.now(timezone.utc) - sent_at).total_seconds()) <= 60
        assert body["event"]["event_type"] == "intrusion"
    finally:
        server.shutdown()


def test_send_webhook_returns_false_for_disabled_endpoint() -> None:
    ep = _make_endpoint()
    ep.enabled = False
    assert send_webhook(_make_attempt(), ep, timeout_sec=5) is False


def test_send_webhook_returns_false_for_invalid_message_type() -> None:
    server, _ = _run_loopback_server()
    try:
        ep = _make_endpoint(url=f"http://127.0.0.1:{server.server_port}/hook")
        assert send_webhook(_make_attempt(), ep, timeout_sec=5, message_type="sms") is False
    finally:
        server.shutdown()


def test_send_webhook_returns_false_on_server_error() -> None:
    class _FailHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), _FailHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        ep = _make_endpoint(url=f"http://127.0.0.1:{server.server_port}/hook")
        assert send_webhook(_make_attempt(), ep, timeout_sec=5) is False
    finally:
        server.shutdown()


def test_send_webhook_returns_false_on_unreachable() -> None:
    ep = _make_endpoint(url="http://127.0.0.1:1/hook")
    assert send_webhook(_make_attempt(), ep, timeout_sec=1) is False


# ── P2-T2: AlertManager 매트릭스 라우팅 (TDD RED) ──────────────────────────

import logging as _logging
from datetime import datetime as _datetime

EP1_ID = "ep00000000000001"
EP2_ID = "ep00000000000002"


def _make_t2_endpoint(ep_id: str = EP1_ID, name: str = "e1") -> WebhookEndpoint:
    return WebhookEndpoint(id=ep_id, name=name, url="https://hooks.example.com/ksi", secret=FAKE_SECRET)


def _make_t2_manager(tmp_path, prefs, endpoints):
    """AlertManager를 tmp 격리 상태로 생성 — outbox 워커 정지 + enqueue 기록."""
    am = pytest.importorskip("src.alerts.alert_manager")
    pl = pytest.importorskip("src.pipeline.pipeline")
    cfg = am.AlertConfig(
        enabled=True,
        save_images=False,
        blur_faces=False,
        console_output=False,
        log_file=str(tmp_path / "alerts.log"),
        outbox_dir=str(tmp_path / "outbox"),
    )
    mgr = am.AlertManager(cfg, webhook_endpoint_provider=lambda: list(endpoints))
    mgr._outbox.stop()
    mgr.smtp_config = {
        "host": "smtp.example.com",
        "port": 587,
        "user": "ksi",
        "password": "fake",
        "recipients": ["ops@example.com"],
        "use_ssl": False,
        "timeout": 10,
    }
    mgr._alert_prefs = prefs
    recorded = []
    mgr._outbox.enqueue = lambda attempt: recorded.append(attempt) or True
    return mgr, recorded, pl


def _t2_prefs(**overrides):
    prefs = {
        "email_alert_level_threshold": "low",
        "webhook_alert_level_threshold": "low",
        "per_event_email_enabled": {},
        "per_event_channels": {},
    }
    prefs.update(overrides)
    return prefs


def _t2_event(pl, **overrides):
    kwargs = {
        "event_id": "evt-t2-001",
        "camera_id": "cam_001",
        "event_type": "person_detected",
        "timestamp": _datetime.now(),
        "confidence": 0.9,
        "alert_level": "medium",
    }
    kwargs.update(overrides)
    return pl.SafetyEvent(**kwargs)


def test_t2_webhook_handler_registered_and_dispatches(tmp_path, monkeypatch) -> None:
    ep = _make_t2_endpoint()
    mgr, _, _ = _make_t2_manager(tmp_path, _t2_prefs(), [ep])
    calls = []
    monkeypatch.setattr(
        "src.alerts.alert_manager.send_webhook",
        lambda attempt, endpoint, timeout_sec, **kw: calls.append((attempt, endpoint)) or True,
        raising=False,
    )
    attempt = _make_attempt()
    attempt.recipient = EP1_ID
    assert mgr._dispatcher.dispatch(attempt) is True
    assert len(calls) == 1
    assert calls[0][1].id == EP1_ID


def test_t2_matrix_webhook_on_sends_per_endpoint(tmp_path) -> None:
    prefs = _t2_prefs(per_event_channels={"person_detected": ["email", "webhook"]})
    mgr, recorded, pl = _make_t2_manager(tmp_path, prefs, [_make_t2_endpoint(EP1_ID), _make_t2_endpoint(EP2_ID, "e2")])
    assert mgr.handle_event(_t2_event(pl)) is True
    by_channel = {}
    for attempt in recorded:
        by_channel.setdefault(attempt.channel, []).append(attempt)
    assert len(by_channel[NotificationChannel.EMAIL]) == 1
    webhooks = by_channel[NotificationChannel.WEBHOOK]
    assert len(webhooks) == 2
    assert {a.idempotency_key for a in webhooks} == {
        f"webhook_{EP1_ID}_evt-t2-001",
        f"webhook_{EP2_ID}_evt-t2-001",
    }
    assert {a.recipient for a in webhooks} == {EP1_ID, EP2_ID}


def test_t2_matrix_webhook_off_keeps_email(tmp_path) -> None:
    prefs = _t2_prefs(per_event_channels={"person_detected": ["email"]})
    mgr, recorded, pl = _make_t2_manager(tmp_path, prefs, [_make_t2_endpoint()])
    mgr.handle_event(_t2_event(pl))
    channels = [a.channel for a in recorded]
    assert NotificationChannel.EMAIL in channels
    assert NotificationChannel.WEBHOOK not in channels


def test_t2_webhook_threshold_blocks_but_email_passes(tmp_path) -> None:
    prefs = _t2_prefs(email_alert_level_threshold="low", webhook_alert_level_threshold="high")
    mgr, recorded, pl = _make_t2_manager(tmp_path, prefs, [_make_t2_endpoint()])
    mgr.handle_event(_t2_event(pl, alert_level="medium"))
    channels = [a.channel for a in recorded]
    assert NotificationChannel.EMAIL in channels
    assert NotificationChannel.WEBHOOK not in channels


def test_t2_legacy_email_off_still_blocks_email_allows_webhook(tmp_path) -> None:
    prefs = _t2_prefs(
        per_event_email_enabled={"person_detected": False},
        per_event_channels={"person_detected": ["email", "webhook"]},
    )
    mgr, recorded, pl = _make_t2_manager(tmp_path, prefs, [_make_t2_endpoint()])
    mgr.handle_event(_t2_event(pl))
    channels = [a.channel for a in recorded]
    assert NotificationChannel.EMAIL not in channels
    assert channels.count(NotificationChannel.WEBHOOK) == 1


def test_t2_no_endpoints_skips_without_send_webhook(tmp_path, monkeypatch, caplog) -> None:
    mgr, recorded, pl = _make_t2_manager(tmp_path, _t2_prefs(), [])
    calls = []
    monkeypatch.setattr(
        "src.alerts.alert_manager.send_webhook",
        lambda *a, **k: calls.append((a, k)) or True,
        raising=False,
    )
    with caplog.at_level(_logging.DEBUG, logger="src.alerts.alert_manager"):
        mgr.handle_event(_t2_event(pl))
    assert [a for a in recorded if a.channel == NotificationChannel.WEBHOOK] == []
    assert calls == []
    assert any(r.levelno == _logging.DEBUG and "스킵" in r.message for r in caplog.records)


def test_t2_disabled_endpoints_skipped_without_send_webhook(tmp_path, monkeypatch) -> None:
    ep = _make_t2_endpoint()
    ep.enabled = False
    mgr, recorded, pl = _make_t2_manager(tmp_path, _t2_prefs(), [ep])
    calls = []
    monkeypatch.setattr(
        "src.alerts.alert_manager.send_webhook",
        lambda *a, **k: calls.append((a, k)) or True,
        raising=False,
    )
    mgr.handle_event(_t2_event(pl))
    assert [a for a in recorded if a.channel == NotificationChannel.WEBHOOK] == []
    assert calls == []


def test_t2_dispatch_unknown_recipient_skips_send(tmp_path, monkeypatch) -> None:
    mgr, _, _ = _make_t2_manager(tmp_path, _t2_prefs(), [_make_t2_endpoint()])
    calls = []
    monkeypatch.setattr(
        "src.alerts.alert_manager.send_webhook",
        lambda *a, **k: calls.append((a, k)) or True,
        raising=False,
    )
    attempt = _make_attempt()
    attempt.recipient = "deadbeefdeadbeef"
    assert mgr._send_webhook_direct(attempt) is False
    assert calls == []


def test_t2_webhook_threshold_defaults_to_medium(tmp_path) -> None:
    prefs = {"email_alert_level_threshold": "low", "per_event_email_enabled": {}}
    mgr, _, pl = _make_t2_manager(tmp_path, prefs, [_make_t2_endpoint()])
    assert mgr._should_send_webhook(_t2_event(pl, alert_level="low")) is False
    assert mgr._should_send_webhook(_t2_event(pl, alert_level="medium")) is True


def test_t2_batch_flush_enqueues_webhooks(tmp_path) -> None:
    prefs = _t2_prefs(per_event_channels={"person_detected": ["email", "webhook"]})
    mgr, recorded, pl = _make_t2_manager(tmp_path, prefs, [_make_t2_endpoint()])
    mgr._batch_buffer.append(_t2_event(pl))
    mgr._flush_batch()
    assert [a for a in recorded if a.channel == NotificationChannel.WEBHOOK] != []


# ── R9 머지 차단 회귀 (C-1·I-1) ─────────────────────────────────────────

def test_c1_run_build_pipeline_injects_real_webhook_reader(tmp_path, monkeypatch) -> None:
    """C-1: 데몬 경로 AlertManager가 실제 리더를 주입받는다 (영구 스킵 방지)."""
    run_mod = pytest.importorskip("src.run")
    wh = pytest.importorskip("src.api.routes.webhooks")

    cfg = Mock()
    cfg.get_detection_config.return_value = {}
    cfg.get_alert_config.return_value = {"save_images": False, "encrypt_images": False}
    cfg.get.return_value = {}

    detector = Mock(classes={0: "person"})
    reg = Mock()
    reg.active_model_names = ["general_yolo"]
    reg.failed_models = {}
    reg.get_model.return_value = detector
    reg.models = {"general_yolo": detector}

    captured: dict = {}

    class _FakeAlertManager:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)
            self.config = args[0] if args else Mock(blur_faces=False)
            self.event_repository = kwargs.get("event_repository")
            self.incident_store = kwargs.get("incident_store")

        def prepare_image_for_display(self, *args, **kwargs):
            return None

    fake_pipeline = Mock()

    with _patch.object(run_mod.MultiModelRegistry, "from_config", return_value=reg), \
         _patch.object(run_mod.EventPolicyEngine, "from_config", return_value=Mock()), \
         _patch.object(run_mod, "SQLiteObservationStore", return_value=Mock()), \
         _patch.object(run_mod, "SQLiteEventRepository", return_value=Mock()), \
         _patch.object(run_mod, "reconcile_pending_verifications", return_value=0), \
         _patch.object(run_mod, "IncidentStore", return_value=Mock()), \
         _patch.object(run_mod, "ByteTrackManager", return_value=Mock()), \
         _patch.object(run_mod, "SafetyPipeline", return_value=fake_pipeline), \
         _patch.object(run_mod, "AlertManager", _FakeAlertManager), \
         _patch.object(run_mod, "create_vlm_provider", return_value=None):
        run_mod.build_pipeline(cfg)

    provider = captured.get("webhook_endpoint_provider")
    assert provider is wh.load_webhook_endpoints  # 실제 리더 주입 (None·스텁 금지)
    assert isinstance(provider(tmp_path / "missing-webhooks.yaml"), list)


def _i1_client(tmp_path, monkeypatch):
    """I-1 API 회귀용 격리 클라이언트 — env 경로 + 감사 리다이렉트."""
    from fastapi.testclient import TestClient

    from src.api import service
    from src.api.app import create_app
    from src.api.routes import webhooks as wh

    webhooks_file = tmp_path / "webhooks.yaml"
    prefs_file = tmp_path / "alert_preferences.yaml"
    monkeypatch.setenv("KSI_CCTV_API_KEY", "test-api-key-strong")
    monkeypatch.setenv(wh._WEBHOOKS_ENV, str(webhooks_file))
    monkeypatch.setenv(wh._PREFS_ENV, str(prefs_file))
    monkeypatch.setattr(service, "_AUDIT_LOG", tmp_path / "audit.jsonl")
    return TestClient(create_app()), {"X-API-Key": "test-api-key-strong"}, prefs_file


def test_i1_all_empty_lists_without_confirm_is_422(tmp_path, monkeypatch) -> None:
    """I-1: {fire:[]} 전부-빈목록도 확인 없이 200 통과 금지 (frontend shouldConfirmChannelsSave와 정합)."""
    client, headers, prefs_file = _i1_client(tmp_path, monkeypatch)
    prefs_file.write_text("email_alert_level_threshold: medium\n", encoding="utf-8")
    before = prefs_file.read_bytes()
    r = client.put("/api/webhooks/channels", headers=headers, json={"channels": {"fire": []}})
    assert r.status_code == 422
    assert prefs_file.read_bytes() == before  # 거부 시 파일 불변


def test_i1_all_empty_lists_with_confirm_is_200(tmp_path, monkeypatch) -> None:
    client, headers, _ = _i1_client(tmp_path, monkeypatch)
    r = client.put(
        "/api/webhooks/channels",
        headers=headers,
        json={"channels": {"fire": []}, "confirm": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["channels"] == {"fire": []}


def test_i1_mixed_empty_and_nonempty_without_confirm_is_200(tmp_path, monkeypatch) -> None:
    """전부-빈목록이 아니면 confirm 없이 통과 (과차단 방지)."""
    client, headers, _ = _i1_client(tmp_path, monkeypatch)
    matrix = {"fire": [], "intrusion": ["email"]}
    r = client.put("/api/webhooks/channels", headers=headers, json={"channels": matrix})
    assert r.status_code == 200, r.text
    assert r.json()["channels"] == matrix
