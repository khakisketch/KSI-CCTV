"""카메라 managed 정본 이관 회귀 테스트 (U2).

Legacy(`cameras.json`) 카메라도 managed와 동일하게 MediaMTX 게이트웨이에
sync되어야 한다. 단, 로컬 파일 입력(mp4 등)은 게이트웨이 대상이 아니므로 제외한다.
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.run import _register_cameras


class _StubPipeline:
    def __init__(self):
        self.profiles = {}
        self.added = {}

    def set_camera_profile(self, cam_id, profile):
        self.profiles[cam_id] = profile

    def add_camera(self, cam_id, stream_url, **kwargs):
        self.added[cam_id] = stream_url


class _StubConfig:
    def __init__(self, cameras):
        self._cameras = cameras

    def get(self, *keys, default=None):
        return default

    def load_cameras(self, cameras_path="./config/cameras.json"):
        return self._cameras


class _StubMediaMTX:
    def __init__(self):
        self.synced = {}

    def sync(self, cam_id, stream_url):
        self.synced[cam_id] = stream_url


def _legacy_cam(stream_url):
    return {
        "id": "cam_legacy",
        "name": "legacy",
        "stream_url": stream_url,
        "fps": 30,
        "enabled": True,
        "detection_type": "general",
    }


def test_legacy_rtsp_camera_is_synced_to_gateway():
    pipe, cfg, mtx = _StubPipeline(), _StubConfig([_legacy_cam("rtsp://127.0.0.1:8554/x")]), _StubMediaMTX()
    assert _register_cameras(pipe, cfg, mediamtx=mtx) == 1
    assert mtx.synced.get("cam_legacy") == "rtsp://127.0.0.1:8554/x"


def test_legacy_file_input_is_not_synced_to_gateway():
    pipe, cfg, mtx = _StubPipeline(), _StubConfig([_legacy_cam("./data/sample.mp4")]), _StubMediaMTX()
    assert _register_cameras(pipe, cfg, mediamtx=mtx) == 1
    assert "cam_legacy" not in mtx.synced
