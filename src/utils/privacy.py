"""개인정보 보호 유틸 — 얼굴 블러 SSOT(저장·표시 공용).

AlertManager(이미지 저장 시)와 대시보드(이미지 표시 시)가 **동일한** 얼굴 블러 로직을
쓰도록 standalone으로 분리한다. blur_faces=True가 저장 경로에서만 적용되고 대시보드
표시에서 누락되어 in-memory event.image의 얼굴 PII가 노출되던 불일치(#11)를 닫는다.

한계: 얼굴 미검출 시 입력을 그대로 반환하며, 이는 Haar cascade의 정확도 한계다.
검출기 로드 실패는 빈 목록으로 표시하고 저장 경로의 AlertManager가 시작을 거부한다.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# CCTV 각도(상방·측방) 대응 — 정면 + 측면 cascade 병행.
_CASCADE_NAMES = (
    "haarcascade_frontalface_default.xml",
    "haarcascade_profileface.xml",
)

_cascades_cache: list[Any] | None = None  # 지연 로드 싱글턴(저장/표시 양측이 재로드 없이 공유)


def get_face_cascades() -> list[Any]:
    """얼굴 검출기를 로드해 반환(프로세스당 1회 캐시).

    cv2 미설치/cascade 로드 실패 시 빈 목록을 반환해 호출측이 정책을 결정하게 한다.
    """
    global _cascades_cache
    if _cascades_cache is not None:
        return _cascades_cache

    try:
        import cv2
    except Exception:  # pragma: no cover - cv2 미설치 환경
        logger.warning("cv2 미설치 — 얼굴 블러 비활성.")
        _cascades_cache = []
        return _cascades_cache

    loaded: list[Any] = []
    # cv2.data.haarcascades는 런타임 유효(번들 cascade 경로)이나 cv2 스텁이 불완전해 mypy가
    # 잘못 플래그한다 — 실동작 확인됨.
    try:
        data_dir = Path(cv2.data.haarcascades)  # type: ignore[attr-defined]
        for name in _CASCADE_NAMES:
            cascade = cv2.CascadeClassifier(str(data_dir / name))
            if cascade.empty():
                logger.warning("얼굴 블러 검출기 로드 실패: %s", name)
            else:
                loaded.append(cascade)
    except (AttributeError, OSError) as exc:
        logger.error("얼굴 블러 검출기 초기화 실패: %s", exc)
    if not loaded:
        logger.warning("사용 가능한 얼굴 검출기가 없습니다. 블러링 비활성.")
    _cascades_cache = loaded
    return _cascades_cache


def blur_faces(image: Any, cascades: list[Any] | None = None, strength: int = 35) -> Any:
    """이미지의 얼굴 영역을 가우시안 블러로 가린다(원본 불변 — copy 반환).

    cascades 미지정 시 get_face_cascades()를 사용한다. 검출기 없음/얼굴 미검출/예외 시
    입력을 그대로 반환한다(저장 경로와 동일한 fail-open 동작 — 호출측이 정책 책임).
    여러 cascade(정면+측면) 결과를 NMS 없이 순서대로 적용한다.
    """
    if image is None:
        return image
    try:
        import cv2
    except Exception:  # pragma: no cover - cv2 미설치 환경
        return image

    if cascades is None:
        cascades = get_face_cascades()
    if not cascades:
        return image

    protected = image.copy()
    gray = cv2.cvtColor(protected, cv2.COLOR_BGR2GRAY)

    kernel = max(3, int(strength))
    if kernel % 2 == 0:
        kernel += 1

    for cascade in cascades:
        # scaleFactor=1.05 (촘촘하게 — 원거리/소형 얼굴), minSize=(20,20)
        faces = cascade.detectMultiScale(
            gray,
            scaleFactor=1.05,
            minNeighbors=4,
            minSize=(20, 20),
        )
        for (x, y, w, h) in (faces if len(faces) > 0 else []):
            roi = protected[y:y + h, x:x + w]
            if roi.size == 0:
                continue
            protected[y:y + h, x:x + w] = cv2.GaussianBlur(roi, (kernel, kernel), 0)

    return protected
