"""Durable notification outbox integration tests."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from src.alerts.outbox import NotificationDispatcher, OutboxRecord, OutboxWorker
from src.events.models import NotificationAttempt, NotificationChannel, NotificationStatus


def _attempt(key: str = "event-1") -> NotificationAttempt:
    return NotificationAttempt(
        attempt_id=f"attempt-{key}",
        decision_id=key,
        channel=NotificationChannel.EMAIL,
        recipient="ops@example.com",
        idempotency_key=key,
        payload={"message": "Subject: Test\n\nBody"},
    )


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_record_roundtrip_restores_runtime_types():
    record = OutboxRecord(attempt=_attempt())

    restored = OutboxRecord.from_json(record.to_json())

    assert restored.attempt.channel is NotificationChannel.EMAIL
    assert restored.attempt.status is NotificationStatus.PENDING
    assert restored.to_json()


def test_latest_retry_state_is_loaded_after_restart():
    with tempfile.TemporaryDirectory() as tmp:
        dispatcher = NotificationDispatcher()
        dispatcher.register(NotificationChannel.EMAIL, lambda _attempt: False)
        worker = OutboxWorker(
            outbox_dir=tmp,
            dispatcher=dispatcher,
            max_retries=3,
            base_backoff=30,
        )
        assert worker.enqueue(_attempt())
        record = worker._queue.get_nowait()
        assert worker._process_record(record) is False

        restarted = OutboxWorker(outbox_dir=tmp, dispatcher=dispatcher)
        restored = restarted._queue.get_nowait()
        assert restored.retry_count == 1
        assert restored.next_retry_at > time.time()


def test_delivered_record_is_not_replayed_after_restart():
    with tempfile.TemporaryDirectory() as tmp:
        sends: list[str] = []
        dispatcher = NotificationDispatcher()
        dispatcher.register(
            NotificationChannel.EMAIL,
            lambda attempt: sends.append(attempt.attempt_id) or True,
        )
        worker = OutboxWorker(outbox_dir=tmp, dispatcher=dispatcher)
        attempt = _attempt()
        assert worker.enqueue(attempt)
        record = worker._queue.get_nowait()

        # Simulate a crash after terminal journaling but before pending compaction.
        assert worker._process_record(record) is True
        assert Path(worker.pending_file).read_text(encoding="utf-8").strip()

        restarted = OutboxWorker(outbox_dir=tmp, dispatcher=dispatcher)
        assert restarted._queue.qsize() == 0
        assert restarted.enqueue(_attempt()) is False
        assert sends == [attempt.attempt_id]


def test_compaction_keeps_dequeued_record_before_inflight_registration():
    with tempfile.TemporaryDirectory() as tmp:
        worker = OutboxWorker(outbox_dir=tmp)
        assert worker.enqueue(_attempt())

        # Reproduce the scheduler gap: dequeued, but not yet registered in-flight.
        worker._queue.get_nowait()
        worker._rewrite_pending()

        restarted = OutboxWorker(outbox_dir=tmp)
        assert restarted._queue.qsize() == 1


def test_json_payload_is_durable_and_owner_only():
    with tempfile.TemporaryDirectory() as tmp:
        worker = OutboxWorker(outbox_dir=tmp)
        assert worker.enqueue(_attempt())

        line = Path(worker.pending_file).read_text(encoding="utf-8").strip()
        payload = json.loads(line)["attempt"]["payload"]
        assert isinstance(payload["message"], str)
        assert Path(worker.pending_file).stat().st_mode & 0o077 == 0
        assert Path(tmp).stat().st_mode & 0o077 == 0


def test_status_reports_counts_without_notification_payloads():
    with tempfile.TemporaryDirectory() as tmp:
        worker = OutboxWorker(outbox_dir=tmp)
        assert worker.enqueue(_attempt())

        status = worker.status()

        assert status == {
            "running": False,
            "pending": 1,
            "inflight": 0,
            "delivered": 0,
            "dead_letter": 0,
        }


def test_dead_letter_key_cannot_restart_retry_budget():
    with tempfile.TemporaryDirectory() as tmp:
        dispatcher = NotificationDispatcher()
        dispatcher.register(NotificationChannel.EMAIL, lambda _attempt: False)
        worker = OutboxWorker(
            outbox_dir=tmp,
            dispatcher=dispatcher,
            max_retries=1,
        )
        attempt = _attempt()
        assert worker.enqueue(attempt)
        record = worker._queue.get_nowait()
        assert worker._process_record(record) is True

        assert worker.enqueue(_attempt()) is False
