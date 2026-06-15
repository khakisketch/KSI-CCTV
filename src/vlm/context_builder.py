"""
VLM 컨텍스트 빌더

YOLO 탐지 결과 + 이벤트 히스토리를 VLM 프롬프트 컨텍스트로 구성.
"""
from __future__ import annotations

import logging
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger(__name__)


class ContextBuilder:
    """YOLO 탐지 결과 + 이벤트 히스토리를 VLM 프롬프트로 구성"""

    def __init__(self, prompt_template_path: str):
        self.template = self._load_template(prompt_template_path)

    @staticmethod
    def _load_template(path: str) -> dict:
        if yaml is None:
            raise ImportError("PyYAML이 필요합니다: pip install pyyaml")
        p = Path(path)
        if not p.exists():
            logger.warning(f"프롬프트 템플릿을 찾을 수 없습니다: {path}")
            return {"system": "", "context_template": ""}
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data

    def build(
        self,
        detections_summary: str,
        recent_events: str | None = None,
    ) -> str:
        """VLM 프롬프트 전체 구성"""
        system = self.template.get("system", "")
        context_tmpl = self.template.get("context_template", "")

        if not context_tmpl:
            return system

        context = context_tmpl.format(
            detections_summary=detections_summary or "탐지 결과 없음",
            recent_events=recent_events or "최근 이벤트 없음",
        )

        return system + "\n\n" + context

    @staticmethod
    def format_detections(detections: list) -> str:
        """YOLO Detection 객체 목록을 요약 문자열로 변환"""
        if not detections:
            return "탐지 결과 없음"
        parts = []
        for d in detections[:10]:  # 최대 10개만 포함
            parts.append(f"{d.class_name}(신뢰도:{d.confidence:.2f})")
        return ", ".join(parts)

    @staticmethod
    def format_events(events: list, max_count: int = 5) -> str:
        """최근 이벤트 목록을 요약 문자열로 변환"""
        if not events:
            return "최근 이벤트 없음"
        parts = []
        for e in events[-max_count:]:
            ts = getattr(e, "timestamp", None)
            time_str = ts.strftime("%H:%M:%S") if ts else ""
            parts.append(f"[{time_str}] {e.event_type}(신뢰도:{e.confidence:.2f})")
        return "\n".join(parts)
