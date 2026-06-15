"""Class adapter for pre-trained model class name mapping.

Phase 8: 사전학습된 공개 모델의 클래스 이름을 KSI-CCTV 표준 클래스 이름으로
변환하여 기존 EventPolicyEngine 규칙과 호환되게 만든다.

예: keremberke/yolov8n-hard-hat-detection 의 'Hardhat' → 'helmet'
"""
from __future__ import annotations

import logging

from src.detection.yolo_detector import Detection

logger = logging.getLogger(__name__)


# 공개 모델 → KSI-CCTV 표준 클래스 매핑
DEFAULT_CLASS_MAPS: dict[str, dict[str, str]] = {
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
    detections: list[Detection],
    class_map: dict[str, str] | None = None,
    model_name: str | None = None,
) -> list[Detection]:
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

    adapted: list[Detection] = []
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


def get_class_map_for_profile(profile: dict[str, str]) -> dict[str, str] | None:
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


def warn_target_class_mismatch(
    model_name: str,
    detector_classes: dict[int, str],
    target_classes: list[str],
) -> None:
    """detector의 실제 클래스와 정책/파이프라인의 target_classes 불일치를 경고한다.

    Args:
        model_name: 모델 식별자 (로그용)
        detector_classes: YOLODetector.classes ({id: name})
        target_classes: 파이프라인/정책에서 기대하는 클래스 이름 목록

    불일치 패턴:
    - detector에는 없는 target_class: 해당 클래스를 이 모델로 탐지할 수 없어
      정책이 트리거되지 않을 수 있다.
    - target에 없는 detector_class: 무해하지만 불필요한 탐지 비용일 수 있다.
    """
    if not target_classes:
        return

    detector_class_names: set[str] = set(detector_classes.values())
    target_set: set[str] = set(target_classes)

    # detector에 없는 target_class (누락 위험)
    missing_in_detector = target_set - detector_class_names
    if missing_in_detector:
        logger.warning(
            "target_classes 불일치 [%s]: 정책/파이프라인이 요구하는 클래스 %s가 "
            "detector 클래스 목록에 없습니다. 해당 클래스는 탐지되지 않아 "
            "관련 정책이 트리거되지 않을 수 있습니다. "
            "(detector 보유 클래스: %s)",
            model_name,
            sorted(missing_in_detector),
            sorted(detector_class_names),
        )

    # target에 없는 detector_class (과잉 탐지 알림 — WARNING 아닌 DEBUG)
    extra_in_detector = detector_class_names - target_set
    if extra_in_detector:
        logger.debug(
            "target_classes 범위 초과 [%s]: detector가 %s 클래스를 탐지할 수 있으나 "
            "target_classes에 포함되지 않았습니다(무해, 정책 필터로 제거됨).",
            model_name,
            sorted(extra_in_detector),
        )
