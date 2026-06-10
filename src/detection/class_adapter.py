"""Class adapter for pre-trained model class name mapping.

Phase 8: 사전학습된 공개 모델의 클래스 이름을 KSI-CCTV 표준 클래스 이름으로
변환하여 기존 EventPolicyEngine 규칙과 호환되게 만든다.

예: keremberke/yolov8n-hard-hat-detection 의 'Hardhat' → 'helmet'
"""
from __future__ import annotations

from typing import Dict, List, Optional

from src.detection.yolo_detector import Detection


# 공개 모델 → KSI-CCTV 표준 클래스 매핑
DEFAULT_CLASS_MAPS: Dict[str, Dict[str, str]] = {
    # PPE (hard-hat detection)
    "ppe_yolo": {
        "Hardhat": "helmet",
        "NO-Hardhat": "no_helmet",
        "Helmet": "helmet",
        "NO-Helmet": "no_helmet",
        "helmet": "helmet",
        "no_helmet": "no_helmet",
        "Vest": "vest",
        "NO-Vest": "no_vest",
        "vest": "vest",
        "no_vest": "no_vest",
        "Person": "person",
        "person": "person",
    },
    # Fire/Smoke detection
    "fire_smoke_yolo": {
        "fire": "fire",
        "Fire": "fire",
        "smoke": "smoke",
        "Smoke": "smoke",
    },
}


def adapt_detections(
    detections: List[Detection],
    class_map: Optional[Dict[str, str]] = None,
    model_name: Optional[str] = None,
) -> List[Detection]:
    """탐지 결과의 클래스 이름을 표준 이름으로 변환.

    Args:
        detections: 원본 탐지 결과 목록
        class_map: 명시적 클래스 매핑 dict. None이면 model_name으로 기본 매핑 사용.
        model_name: 기본 매핑을 조회할 모델 이름 (ppe_yolo, fire_smoke_yolo 등)

    Returns:
        클래스 이름이 변환된 Detection 목록
    """
    if class_map is None and model_name:
        class_map = DEFAULT_CLASS_MAPS.get(model_name, {})
    if not class_map:
        return detections

    adapted: List[Detection] = []
    for det in detections:
        new_name = class_map.get(det.class_name, det.class_name)
        if new_name != det.class_name:
            adapted.append(
                Detection(
                    class_id=det.class_id,
                    class_name=new_name,
                    confidence=det.confidence,
                    bbox=det.bbox,
                )
            )
        else:
            adapted.append(det)
    return adapted


def get_class_map_for_profile(profile: Dict[str, str]) -> Optional[Dict[str, str]]:
    """model_profiles.yaml profile에서 class_map을 추출.

    Args:
        profile: model_profiles.yaml의 개별 모델 프로필 dict

    Returns:
        class_map dict 또는 None
    """
    # 1. profile에 직접 class_map이 정의된 경우
    raw_map = profile.get("class_map")
    if raw_map and isinstance(raw_map, dict):
        return {str(k): str(v) for k, v in raw_map.items()}

    # 2. model_name으로 기본 매핑 조회
    model_name = profile.get("name", "")
    if model_name:
        return DEFAULT_CLASS_MAPS.get(model_name)

    return None
