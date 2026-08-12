"""Metadata-only observation aggregation and API tests."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.detection.yolo_detector import Detection
from src.events.observations import ObservationStore
from src.pipeline.pipeline import SafetyPipeline
from src.storage import SQLiteObservationStore
from src.storage.schema import UnsupportedSchemaVersionError


def test_observation_bucket_aggregates_counts_without_images():
    store = ObservationStore(max_buckets_per_camera=2)
    timestamp = datetime(2026, 7, 19, 12, 0, 1)
    detections = {
        "general_yolo": [
            Detection(0, "person", 0.9, (10, 10, 30, 50)),
            Detection(1, "person", 0.8, (60, 10, 80, 50)),
        ]
    }
    zones = {"gate": {"points": [[0, 0], [50, 0], [50, 100], [0, 100]]}}

    store.record("cam", timestamp, detections, zones)
    store.record("cam", timestamp + timedelta(seconds=20), {"general_yolo": detections["general_yolo"][:1]}, zones)
    item = store.query("cam")[0]

    assert item["samples"] == 2
    assert item["latest_counts"] == {"person": 1}
    assert item["max_counts"] == {"person": 2}
    assert item["zone_max_counts"] == {"gate:person": 1}
    assert "image" not in item


def test_observation_store_is_bounded_per_camera():
    store = ObservationStore(max_buckets_per_camera=2)
    start = datetime(2026, 7, 19, 12, 0)
    for minute in range(3):
        store.record("cam", start + timedelta(minutes=minute), {}, None)
    values = store.query("cam")
    assert len(values) == 2
    assert values[-1]["bucket_start"].startswith("2026-07-19T12:01")


def test_person_observation_does_not_emit_event_or_vlm():
    detector = Mock()
    detector.detect.return_value = [Detection(0, "person", 0.9, (0, 0, 10, 20))]
    detector.detect_safety_gear.return_value = {"violations": []}
    pipeline = SafetyPipeline(detector=detector)
    alerts = []
    pipeline.add_alert_callback(alerts.append)

    # Legacy mode still treats an explicitly configured person class as observation only.
    pipeline._process_frame_legacy("cam", Mock(), datetime.now())
    assert alerts == []
    assert pipeline.vlm_trigger_classes == set()


def test_observations_api_uses_mounted_pipeline():
    pipeline = Mock()
    pipeline.get_observations.return_value = [{"camera_id": "cam", "samples": 2}]
    client = TestClient(create_app(pipeline=pipeline))
    with patch.dict("os.environ", {"KSI_CCTV_API_KEY": "observation-key"}):
        response = client.get(
            "/api/observations?camera_id=cam&limit=10",
            headers={"X-API-Key": "observation-key"},
        )
    assert response.status_code == 200
    assert response.json()["count"] == 1
    pipeline.get_observations.assert_called_once_with(camera_id="cam", limit=10)


def test_sqlite_observations_survive_restart_and_merge_current_bucket(tmp_path):
    path = tmp_path / "state" / "observations.db"
    timestamp = datetime(2026, 7, 19, 12, 0, 1)
    detections = {"general_yolo": [Detection(0, "person", 0.9, (0, 0, 10, 20))]}

    first = SQLiteObservationStore(path, max_buckets_per_camera=10)
    first.record("cam", timestamp, detections)
    first.close()

    second = SQLiteObservationStore(path, max_buckets_per_camera=10)
    second.record("cam", timestamp + timedelta(seconds=20), detections)
    values = second.query("cam")
    second.close()

    assert values[0]["samples"] == 2
    assert values[0]["max_counts"] == {"person": 1}
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_sqlite_observation_retention_prunes_old_buckets(tmp_path):
    store = SQLiteObservationStore(
        tmp_path / "observations.db",
        retention_days=1,
        prune_interval_sec=1_000_000_000_000,
    )
    old = datetime(2026, 7, 1, 12, 0)
    current = datetime(2026, 7, 19, 12, 0)
    store.record("cam", old, {})
    store.record("cam", current, {})

    deleted = store.prune(now=current)
    remaining = store.query("cam")
    store.close()

    assert deleted == 1
    assert len(remaining) == 1
    assert remaining[0]["bucket_start"].startswith("2026-07-19")


def test_sqlite_observation_first_writes_are_atomic(tmp_path):
    path = tmp_path / "observations.db"
    store = SQLiteObservationStore(path, prune_interval_sec=1_000_000_000_000)
    timestamp = datetime(2026, 7, 19, 12, 0, 1)
    detections = {"general_yolo": [Detection(0, "person", 0.9, (0, 0, 10, 20))]}
    workers = 16
    barrier = threading.Barrier(workers)

    def record_once():
        barrier.wait()
        store.record("cam", timestamp, detections)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(record_once) for _ in range(workers)]
        for future in futures:
            future.result()
    store.close()

    reopened = SQLiteObservationStore(path)
    values = reopened.query("cam")
    reopened.close()

    assert values[0]["samples"] == workers


def test_sqlite_write_failure_rolls_back_in_memory_bucket(tmp_path):
    store = SQLiteObservationStore(tmp_path / "observations.db")
    store._load_camera("cam")
    timestamp = datetime(2026, 7, 19, 12, 0, 1)
    detections = {"general_yolo": [Detection(0, "person", 0.9, (0, 0, 10, 20))]}

    class FailingConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            raise sqlite3.OperationalError("injected write failure")

    with (
        patch.object(store, "_connect", return_value=FailingConnection()),
        pytest.raises(sqlite3.OperationalError),
    ):
        store.record("cam", timestamp, detections)

    assert ObservationStore.query(store, "cam") == []
    assert store.query("cam") == []

    store.record("cam", timestamp, detections)
    with (
        patch.object(store, "_connect", return_value=FailingConnection()),
        pytest.raises(sqlite3.OperationalError),
    ):
        store.record("cam", timestamp + timedelta(seconds=10), detections)

    assert ObservationStore.query(store, "cam")[0]["samples"] == 1
    assert store.query("cam")[0]["samples"] == 1
    store.close()

    bounded = SQLiteObservationStore(
        tmp_path / "bounded.db", max_buckets_per_camera=2
    )
    bounded.record("cam", timestamp, {})
    bounded.record("cam", timestamp + timedelta(minutes=1), {})
    with (
        patch.object(bounded, "_connect", return_value=FailingConnection()),
        pytest.raises(sqlite3.OperationalError),
    ):
        bounded.record("cam", timestamp + timedelta(minutes=2), {})

    in_memory = ObservationStore.query(bounded, "cam")
    persisted = bounded.query("cam")
    bounded.close()
    assert [item["bucket_start"] for item in in_memory] == [
        timestamp.replace(second=0).isoformat(),
        (timestamp + timedelta(minutes=1)).replace(second=0).isoformat(),
    ][::-1]
    assert [item["bucket_start"] for item in persisted] == [
        item["bucket_start"] for item in in_memory
    ]


def test_sqlite_observations_reject_newer_schema_version_without_caching_connection(tmp_path):
    path = tmp_path / "observations.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (99, 'now')"
    )
    connection.commit()
    store = SQLiteObservationStore(path)

    with (
        patch("src.storage.sqlite_observations.sqlite3.connect", return_value=connection),
        pytest.raises(UnsupportedSchemaVersionError, match="99.*supported version 1"),
    ):
        store.query()

    assert store._connection is None
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
