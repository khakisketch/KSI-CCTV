"""
VLM 프롬프트 자동 선택기

카메라의 detection_type에 따라 적절한 프롬프트 템플릿을 자동 선택.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from src.vlm.context_builder import ContextBuilder

logger = logging.getLogger(__name__)

# 기본 프롬프트 디렉토리
DEFAULT_PROMPTS_DIR = "./config/prompts"

# detection_type → 프롬프트 파일 매핑
PROMPT_MAP = {
    "construction": "construction.yaml",
    "school": "school.yaml",
    "factory": "construction.yaml",  # factory는 construction 프롬프트 재사용
    "warehouse": "construction.yaml",
    "default": "construction.yaml",
}


class PromptSelector:
    """카메라 detection_type에 따라 VLM 프롬프트를 자동 선택"""

    def __init__(self, prompts_dir: str = DEFAULT_PROMPTS_DIR):
        self.prompts_dir = Path(prompts_dir)
        self._context_builders: dict[str, ContextBuilder] = {}

    def get_builder(self, detection_type: str) -> ContextBuilder:
        """detection_type에 맞는 ContextBuilder 반환 (캐시 사용)"""
        if detection_type in self._context_builders:
            return self._context_builders[detection_type]

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
        recent_events: Optional[str] = None,
    ) -> str:
        """detection_type에 맞는 프롬프트 생성"""
        builder = self.get_builder(detection_type)
        return builder.build(detections_summary, recent_events)


def get_detection_type_for_camera(camera_config: dict) -> str:
    """카메라 설정에서 detection_type 추출"""
    return camera_config.get("detection_type", "default")
