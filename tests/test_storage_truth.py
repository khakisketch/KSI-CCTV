"""스토리지·알람 단일진실 회귀 테스트 (U4).

- incident projection은 durable event 저장소 유무와 무관하게 동작해야 한다.
  event_repository 없이 incident_store만 있어도 high/critical 이벤트의
  incident가 생성된다.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.alerts.alert_manager import AlertConfig, AlertManager
from src.incidents import IncidentStore
from src.pipeline.pipeline import SafetyEvent


def test_incident_projection_without_event_repository(tmp_path):
    cfg = AlertConfig(
        enabled=True,
        save_images=False,
        blur_faces=False,
        encrypt_images=False,
        console_output=False,
        log_file=str(tmp_path / "alerts.log"),
        image_dir=str(tmp_path / "captures"),
        outbox_dir=str(tmp_path / "outbox"),
    )
    store = IncidentStore(tmp_path / "incidents.db")
    store.validate_schema()
    mgr = AlertManager(cfg, incident_store=store)
    try:
        event = SafetyEvent(
            event_id="evt_no_repo",
            camera_id="cam_001",
            event_type="fire",
            timestamp=datetime.now(),
            confidence=0.9,
            alert_level="high",
        )
        mgr.handle_event(event)
        assert store.get("evt_no_repo") is not None
    finally:
        mgr.shutdown()
