"""하드 크래시 잔여 pending VLM 검증의 시작 시 재조정(reconcile) 테스트.

graceful stop은 이미 FORCED_SHUTDOWN으로 확정되므로, 여기서는 하드 크래시로
durable 저장소에 verification_state="pending"으로 남은 항목만 다룬다.
fail-open 원칙: 억제하지 않고 unverified로 보존한다.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.events.verification_reconcile import reconcile_pending_verifications
from src.storage.sqlite_events import SQLiteEventRepository


def pending_event(event_id: str, **overrides) -> dict:
    base = {
        "event_id": event_id,
        "timestamp": "2026-09-18T00:00:00+00:00",
        "camera_id": "cam-01",
        "event_type": "intrusion",
        "alert_level": "high",
        "alert_state": "provisional",
        "verification_state": "pending",
        "verification_reason_codes": ["VLM_QUEUED"],
        "verification_summary": {"status": "pending"},
        "frame_count": 3,
        "confirm_count": 1,
        "reject_count": 0,
        "error_count": 0,
        "evidence_image": "data/captures/evt.jpg",
        "metadata": {"site": "gate", "note": "keep-me"},
    }
    base.update(overrides)
    return base


class TestReconcilePendingVerifications(unittest.TestCase):
    def test_pending_becomes_unverified_restart_lost_new_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteEventRepository(Path(tmp) / "events.db")
            try:
                repo.record(pending_event("evt-pending"))
                fixed = reconcile_pending_verifications(repo)
                self.assertEqual(fixed, 1)
                latest = repo.get("evt-pending")
                assert latest is not None
                self.assertEqual(latest["verification_state"], "unverified")
                self.assertEqual(latest["verification_reason_codes"], ["RESTART_LOST"])
                self.assertEqual(
                    latest["verification_summary"],
                    {
                        "status": "unverified",
                        "reason": "RESTART_LOST",
                        "frames": 3,
                        "confirm": 1,
                        "reject": 0,
                        "error": 0,
                    },
                )
                self.assertEqual(latest["alert_state"], "unverified")
                via_list = {item["event_id"]: item for item in repo.list(latest_only=True)}
                self.assertEqual(via_list["evt-pending"]["verification_state"], "unverified")
                # 새 revision이므로 전체 revision은 2건
                self.assertEqual(len(repo.list(latest_only=False)), 2)
            finally:
                repo.close()

    def test_non_pending_events_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteEventRepository(Path(tmp) / "events.db")
            try:
                repo.record(pending_event("evt-confirmed", verification_state="confirmed",
                                          alert_state="confirmed"))
                repo.record(pending_event("evt-unverified", verification_state="unverified",
                                          alert_state="unverified"))
                repo.record(pending_event("evt-notreq", verification_state="not_required",
                                          alert_state="confirmed"))
                before = repo.latest_stream_id()
                fixed = reconcile_pending_verifications(repo)
                self.assertEqual(fixed, 0)
                self.assertEqual(repo.latest_stream_id(), before)
                self.assertEqual(repo.get("evt-confirmed")["verification_state"], "confirmed")
                self.assertEqual(repo.get("evt-unverified")["verification_state"], "unverified")
                self.assertEqual(repo.get("evt-notreq")["verification_state"], "not_required")
                self.assertEqual(len(repo.list(latest_only=False)), 3)
            finally:
                repo.close()

    def test_idempotent_second_run_fixes_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteEventRepository(Path(tmp) / "events.db")
            try:
                repo.record(pending_event("evt-a"))
                repo.record(pending_event("evt-b"))
                self.assertEqual(reconcile_pending_verifications(repo), 2)
                self.assertEqual(reconcile_pending_verifications(repo), 0)
                self.assertEqual(repo.get("evt-a")["verification_state"], "unverified")
                self.assertEqual(repo.get("evt-b")["verification_state"], "unverified")
            finally:
                repo.close()

    def test_limit_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteEventRepository(Path(tmp) / "events.db")
            try:
                repo.record(pending_event("evt-1"))
                repo.record(pending_event("evt-2"))
                fixed = reconcile_pending_verifications(repo, limit=1)
                self.assertEqual(fixed, 1)
                states = {
                    "evt-1": repo.get("evt-1")["verification_state"],
                    "evt-2": repo.get("evt-2")["verification_state"],
                }
                self.assertEqual(sorted(states.values()), ["pending", "unverified"])
            finally:
                repo.close()

    def test_existing_payload_metadata_and_image_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteEventRepository(Path(tmp) / "events.db")
            try:
                repo.record(pending_event("evt-keep"))
                reconcile_pending_verifications(repo)
                latest = repo.get("evt-keep")
                assert latest is not None
                self.assertEqual(latest["event_id"], "evt-keep")
                self.assertEqual(latest["camera_id"], "cam-01")
                self.assertEqual(latest["event_type"], "intrusion")
                self.assertEqual(latest["evidence_image"], "data/captures/evt.jpg")
                self.assertEqual(latest["metadata"], {"site": "gate", "note": "keep-me"})
                self.assertEqual(latest["frame_count"], 3)
            finally:
                repo.close()


if __name__ == "__main__":
    unittest.main()
