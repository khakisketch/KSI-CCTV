"""
VLM 프롬프트 자동 선택기

카메라의 detection_type에 따라 적절한 프롬프트 템플릿을 자동 선택.
"""
from __future__ import annotations

import logging
from pathlib import Path

from src.vlm.context_builder import ContextBuilder

logger = logging.getLogger(__name__)

# 기본 프롬프트 디렉토리
DEFAULT_PROMPTS_DIR = "./config/prompts"

# detection_type → 프롬프트 파일 매핑
# 전용 yaml이 없는 도메인은 의미적으로 가장 가까운 파일을 명시적으로 지정한다.
# 운영자가 새 도메인을 추가할 때는 여기에 항목을 추가하고 전용 yaml을 작성할 것.
PROMPT_MAP = {
    "general": "general.yaml",
    "construction": "construction.yaml",
    "school": "school.yaml",
    "factory": "factory.yaml",
    "warehouse": "warehouse.yaml",
    "parking": "parking.yaml",
    # 미매핑 도메인 fallback — 범용 프롬프트(특정 현장 가정 없음)가 가장 안전한 기본.
    "default": "general.yaml",
}

# 전용 프롬프트가 없어 다른 도메인 파일을 재사용하는 도메인 집합.
# 이 집합에 포함된 도메인은 get_builder() 호출 시 경고를 1회 출력한다.
_UNMAPPED_DOMAINS: set[str] = set()


class PromptSelector:
    """카메라 detection_type에 따라 VLM 프롬프트를 자동 선택"""

    def __init__(self, prompts_dir: str = DEFAULT_PROMPTS_DIR):
        self.prompts_dir = Path(prompts_dir)
        self._context_builders: dict[str, ContextBuilder] = {}

    def get_builder(self, detection_type: str) -> ContextBuilder:
        """detection_type에 맞는 ContextBuilder 반환 (캐시 사용).

        PROMPT_MAP에 없는 도메인은 최초 1회 경고를 출력하고 기본 프롬프트를 사용한다.
        대시보드 표시용으로 _UNMAPPED_DOMAINS 집합에도 추가한다.
        """
        if detection_type in self._context_builders:
            return self._context_builders[detection_type]

        # ── 미매핑 도메인 감지: PROMPT_MAP에 없고 첫 호출인 경우만 경고 1회 출력 ──
        if detection_type not in PROMPT_MAP and detection_type not in _UNMAPPED_DOMAINS:
            _UNMAPPED_DOMAINS.add(detection_type)
            logger.warning(
                f"[PromptSelector] 도메인 '{detection_type}'에 전용 프롬프트가 없습니다. "
                f"기본 프롬프트(construction)를 사용합니다. "
                f"전용 프롬프트가 필요하면 config/prompts/{detection_type}.yaml을 추가하고 "
                f"PROMPT_MAP에 항목을 등록하세요."
            )

        filename = PROMPT_MAP.get(detection_type, PROMPT_MAP["default"])
        prompt_path = self.prompts_dir / filename

        if not prompt_path.exists():
            logger.warning(
                f"프롬프트 파일을 찾을 수 없습니다: {prompt_path}. 기본값 사용."
            )
            # 기본값으로 fallback
            prompt_path = self.prompts_dir / PROMPT_MAP["default"]

        builder = ContextBuilder(str(prompt_path))
        self._context_builders[detection_type] = builder
        logger.info(f"VLM 프롬프트 로드: {detection_type} → {prompt_path}")
        return builder

    def build_prompt(
        self,
        detection_type: str,
        detections_summary: str,
        recent_events: str | None = None,
    ) -> str:
        """detection_type에 맞는 프롬프트 생성"""
        builder = self.get_builder(detection_type)
        return builder.build(detections_summary, recent_events)


def get_detection_type_for_camera(camera_config: dict) -> str:
    """카메라 설정에서 detection_type 추출"""
    return camera_config.get("detection_type", "default")
