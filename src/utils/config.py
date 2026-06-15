"""
설정 로딩 유틸리티

YAML/JSON 설정 파일 로드
"""
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    yaml = None  # type: ignore[assignment]  # yaml 미설치 환경 graceful fallback

logger = logging.getLogger(__name__)

class ConfigLoader:
    """설정 파일 로더"""

    @staticmethod
    def load_yaml(path: str) -> dict[str, Any]:
        """YAML 파일 로드"""
        if not HAS_YAML:
            raise ImportError("PyYAML이 설치되지 않았습니다: pip install pyyaml")

        with open(path, encoding='utf-8') as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def load_json(path: str) -> dict[str, Any]:
        """JSON 파일 로드"""
        import json
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    @classmethod
    def load(cls, path: str) -> dict[str, Any]:
        """확장자에 따라 자동 로드"""
        p = Path(path)
        if p.suffix in {'.yaml', '.yml'}:
            return cls.load_yaml(path)
        elif p.suffix == '.json':
            return cls.load_json(path)
        else:
            # 순차 시도
            for ext, loader in [('.yaml', cls.load_yaml), ('.yml', cls.load_yaml), ('.json', cls.load_json)]:
                try:
                    return loader(str(p.with_suffix(ext)))
                except FileNotFoundError:
                    continue
            raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {path}")

    @staticmethod
    def load_env(path: str = ".env") -> None:
        """간단한 .env 로더. 기존 환경변수는 덮어쓰지 않는다."""
        env_path = Path(path)
        if not env_path.exists():
            return

        with open(env_path, encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)

class AppConfig:
    """앱 설정 관리"""

    def __init__(self, config_path: str = "./config/default.yaml"):
        ConfigLoader.load_env()
        self.config = ConfigLoader.load(config_path)

    def get(self, *keys, default=None):
        """중첩 설정 접근"""
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def get_detection_config(self) -> dict:
        """감지 설정"""
        return self.get('detection', default={})

    def get_model_config(self) -> dict:
        """모델 설정"""
        return self.get('models', default={})

    def get_alert_config(self) -> dict:
        """알람 설정"""
        return self.get('alerts', default={})

    def get_ui_config(self) -> dict:
        """UI 설정"""
        return self.get('ui', default={})

    def load_cameras(self, cameras_path: str = "./config/cameras.json") -> list[dict[str, Any]]:
        """카메라 목록 로드.

        운영 환경에서는 실제 계정/비밀번호를 파일에 직접 저장하지 말고,
        환경변수 또는 설치 현장별 보안 저장소에서 주입하는 것을 권장한다.
        """
        path = Path(cameras_path)
        if not path.exists():
            logger.warning(f"카메라 설정 파일이 없습니다: {cameras_path}")
            return []

        data = ConfigLoader.load(str(path))
        cameras: list[dict[str, Any]] = []
        if isinstance(data, list):
            cameras = data
        elif isinstance(data, dict):
            cameras = data.get("cameras", [])
        else:
            return []

        return [
            self._expand_camera_env(camera)
            for camera in cameras
            if isinstance(camera, dict)
        ]

    @staticmethod
    def _expand_camera_env(camera: dict[str, Any]) -> dict[str, Any]:
        """카메라 설정 내 환경변수 표현식을 확장"""
        expanded = dict(camera)
        stream_url = expanded.get("stream_url")
        if isinstance(stream_url, str):
            expanded["stream_url"] = os.path.expandvars(stream_url)
        return expanded


# Phase 8 M5.3 S3: URL 마스킹 유틸리티
_RTSP_PATTERN = re.compile(r'rtsps?://[^\s\'"<>)]+', re.IGNORECASE)
_HTTP_AUTH_PATTERN = re.compile(r'(https?://)([^\s/@:]+):([^\s/@]+)@([^\s/<>"\')\s]+)')


def mask_url(url: str) -> str:
    """
    로그에 노출되는 URL에서 계정/비밀번호를 마스킹합니다.
    - rtsp://user:pw@host → rtsp://***:***@host
    - http://user:pw@host → http://***:***@host
    - 파싱 실패 시 원본 그대로 반환
    """
    if not isinstance(url, str) or not url:
        return url
    try:
        parsed = urlsplit(url)
        if parsed.username or parsed.password:
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            return urlunsplit((parsed.scheme, f"***:***@{host}", parsed.path, parsed.query, parsed.fragment))
    except Exception:
        pass
    return url


def mask_text(text: str) -> str:
    """
    임의의 텍스트에서 RTSP/HTTP URL 패턴을 찾아 마스킹합니다.
    로그 메시지나 감사 로그 entry 전체를 안전하게 만드는 용도.
    """
    if not isinstance(text, str) or not text:
        return text

    def _replace_rtsp(match: re.Match) -> str:
        return mask_url(match.group(0))

    def _replace_http(match: re.Match) -> str:
        return f"{match.group(1)}***:***@{match.group(4)}"

    text = _RTSP_PATTERN.sub(_replace_rtsp, text)
    text = _HTTP_AUTH_PATTERN.sub(_replace_http, text)
    return text
