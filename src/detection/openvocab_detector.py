"""Open-vocabulary 탐지기 — 학습 없이 텍스트 프롬프트로 임의 객체를 탐지.

YOLO-World(ultralytics)를 래핑해 `set_classes(["지게차","안전모 미착용", ...])`만으로 새 객체를
즉시 탐지한다(별도 학습 불필요). 반환은 `YOLODetector`와 동일한 `Detection` 객체라 파이프라인·
정책엔진이 무수정으로 소비한다. 프롬프트 클래스명이 곧 KSI 표준명(class_adapter는 identity).

설계:
- ultralytics/clip은 **lazy import**(생성자에서만) — 미설치 환경의 기본 CCTV 동작을 막지 않는다.
- ONNX 경로(cross-platform 가속)는 후속 과제(docs/roadmap-capability.md) — 현재는 torch 런타임.
- 실시간성은 GPU 의존(CPU는 초 단위) — 저사양/엣지는 skip_frames 상향 또는 트리거 기반 호출 권장.
"""
from __future__ import annotations

import logging
from typing import Any

from src.detection.yolo_detector import Detection

logger = logging.getLogger(__name__)

# YOLO-World 기본 가중치(첫 사용 시 ultralytics가 자동 다운로드). 라이선스: GPLv3(상용 검토).
DEFAULT_WEIGHTS = "yolov8s-world.pt"


class OpenVocabDetector:
    """텍스트 프롬프트 기반 open-vocab 탐지기(YOLO-World 래퍼).

    Args:
        model_path: YOLO-World .pt 경로(또는 이름 — 자동 다운로드).
        classes: 탐지할 텍스트 프롬프트 목록(= 출력 클래스명, KSI 표준명으로 사용).
        conf_threshold: 신뢰도 하한(open-vocab은 보통 낮게 — 정책 min_confidence가 추가 게이트).
        device: 'auto'|'cuda'|'cpu' 등(ultralytics 규약).
    """

    def __init__(
        self,
        model_path: str = DEFAULT_WEIGHTS,
        classes: list[str] | None = None,
        conf_threshold: float = 0.05,
        iou_threshold: float = 0.45,
        device: str = "auto",
        **_ignored: Any,
    ):
        from ultralytics import YOLOWorld  # lazy — 미설치 시 기본 시스템 영향 없음

        self.model_path = str(model_path)
        self.conf_threshold = float(conf_threshold)
        self.iou_threshold = float(iou_threshold)
        self.device = device
        self._model = YOLOWorld(self.model_path)
        self._classes: list[str] = []
        if classes:
            self.set_classes(classes)
        logger.info("OpenVocabDetector 초기화: %s, classes=%s", self.model_path, self._classes)

    @property
    def classes(self) -> dict[int, str]:
        """id→이름 매핑(YOLODetector 인터페이스 호환)."""
        return dict(enumerate(self._classes))

    def set_classes(self, classes: list[str]) -> None:
        """탐지할 프롬프트 클래스 갱신(학습 없이 즉시). 빈 목록은 무시."""
        cls = [str(c).strip() for c in (classes or []) if str(c).strip()]
        if not cls:
            return
        self._classes = cls
        self._model.set_classes(cls)

    def detect(self, frame: Any, *_args: Any, **_kwargs: Any) -> list[Detection]:
        """프레임에서 현재 프롬프트 클래스를 탐지해 Detection 목록 반환.

        *_args/**_kwargs는 YOLODetector.detect(letterbox_pad 등)와 시그니처 호환을 위해 흡수.
        """
        if not self._classes:
            return []
        results = self._model.predict(
            frame, conf=self.conf_threshold, iou=self.iou_threshold,
            device=None if self.device == "auto" else self.device, verbose=False,
        )
        if not results:
            return []
        r = results[0]
        names = r.names
        out: list[Detection] = []
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            return out
        for xyxy, cls_id, conf in zip(
            boxes.xyxy.tolist(), boxes.cls.tolist(), boxes.conf.tolist(), strict=False
        ):
            x1, y1, x2, y2 = (int(v) for v in xyxy)
            name = names.get(int(cls_id), str(int(cls_id))) if isinstance(names, dict) else str(int(cls_id))
            out.append(Detection(int(cls_id), name, float(conf), (x1, y1, x2, y2)))
        return out

    def draw_detections(self, frame: Any, detections: list[Detection]) -> Any:
        """간단 박스 렌더(YOLODetector 인터페이스 호환 — 대시보드/디버그용)."""
        import cv2

        out = frame.copy()
        for d in detections:
            x1, y1, x2, y2 = d.bbox
            cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 0), 2)
            cv2.putText(out, f"{d.class_name} {d.confidence:.2f}", (x1, max(0, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1)
        return out
