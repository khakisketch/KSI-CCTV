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

# PII 마스킹 — VLM 자유서술(description/vlm_raw_response)이 장면 속 연락처·주민번호를
# 전사할 수 있어, alerts.log 영속화 전에 결정론적·저오탐 패턴만 가린다.
# (이름·인상착의 같은 자유서술 PII는 정규식으로 가릴 수 없음 — 한계를 명시한다.)
# local-part/도메인 길이를 RFC 한계로 묶어 '@ 없는 긴 토큰'에서의 O(n^2) 백트래킹(ReDoS) 방지.
_EMAIL_PATTERN = re.compile(r'[\w.+-]{1,64}@[\w-]{1,255}\.[\w.-]{1,255}')
# 한국 휴대폰만 매칭: 010-1234-5678 / 01012345678 / +82-10-1234-5678 (구분자 -, ., 공백 허용).
# 접두 01[016789] / +82 10에 고정 — Unix timestamp(event_id의 1781893381, 17로 시작)나
# bbox 정수열 오탐을 막는다. (국제전화·02 유선·070은 의도적으로 미커버 — 오탐 trade-off.)
_PHONE_PATTERN = re.compile(r'(?<!\d)(?:\+82[-.\s]?1[016789]|01[016789])[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)')
# 주민등록번호: 6자리-구분자-7자리. 구분자를 **필수**로 둬, 구분자 없는 13자리 일련번호·
# 바코드·epoch-ms가 RRN으로 오탐돼 감사 로그 식별값이 손상되는 것을 막는다(#7).
_RRN_PATTERN = re.compile(r'(?<!\d)\d{6}[-\s]\d{7}(?!\d)')


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
    텍스트에서 RTSP/HTTP URL 자격증명 + 결정론적 PII(이메일·한국 휴대폰·주민번호)를 마스킹합니다.
    구조화된 로그(JSON) 전체가 아니라 **자유서술 필드**에 적용하는 것을 권장한다 — 전체에 걸면
    event_id/bbox/timestamp 같은 수치가 오탐 치환될 수 있다(호출측에서 필드 단위 적용).

    커버 범위: 이메일·한국 휴대폰(010/+82 10)·주민등록번호(구분자 있는 6-7) + URL 자격증명.
    한계: 국제전화·02 유선·**구분자 없는 13자리 RRN**·이름·인상착의 등은 가리지 못한다
    (무구분자 13자리는 컨테이너번호·epoch-ms 오탐을 피하려 의도적 미커버 — #7 trade-off).
    그런 PII의 영속화가 걱정되면 alerts.log 접근통제 또는 VLM 원문 저장 비활성이 정답이다.
    URL 자격증명 → 주민번호(전화보다 먼저) → 전화 → 이메일 순.
    """
    if not isinstance(text, str) or not text:
        return text

    def _replace_rtsp(match: re.Match) -> str:
        return mask_url(match.group(0))

    def _replace_http(match: re.Match) -> str:
        return f"{match.group(1)}***:***@{match.group(4)}"

    text = _RTSP_PATTERN.sub(_replace_rtsp, text)
    text = _HTTP_AUTH_PATTERN.sub(_replace_http, text)
    # RRN(6-7=13자리)을 전화(11자리)보다 먼저 가려, 전화 패턴이 RRN 앞부분만 부분 매칭하는 것을 막는다.
    text = _RRN_PATTERN.sub("***-*******", text)
    text = _PHONE_PATTERN.sub("***-****-****", text)
    text = _EMAIL_PATTERN.sub("***@***", text)
    return text
