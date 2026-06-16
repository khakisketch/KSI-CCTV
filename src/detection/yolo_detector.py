"""
YOLO26/Ultralytics 계열 객체 탐지 모듈

ONNX 기반 실시간 객체 탐지 (안전장비, 사람, 화재 등).
YOLO26처럼 end-to-end/NMS-free 출력이 가능한 모델과 기존 YOLOv8/YOLO11
형식의 raw 출력 모두를 처리할 수 있도록 후처리를 분리한다.
"""
import ast
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

logger = logging.getLogger(__name__)

# 좌표가 0~1 normalized인지 판정하는 상한. normalized 좌표는 ≤1.0이므로 1.5는 안전한 여유.
# raw/final 두 후처리 경로가 동일 기준을 쓰도록 단일 상수로 통일한다(이전 2.0/1.5 불일치 제거).
_NORMALIZED_COORD_MAX = 1.5

@dataclass
class Detection:
    """탐지 결과"""
    class_id: int
    class_name: str
    confidence: float
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2

    def to_dict(self):
        return {
            "class_name": self.class_name,
            "confidence": self.confidence,
            "bbox": self.bbox
        }


class YOLODetector:
    """
    YOLO ONNX 객체 탐지기

    Attributes:
        model_path (str): ONNX 모델 파일 경로
        input_size (tuple): 모델 입력 크기 (기본 640x640)
        conf_threshold (float): 신뢰도 임계값
        iou_threshold (float): NMS IoU 임계값
        classes (dict): 클래스 매핑 {id: name}
    """

    # 기본 클래스 정의 (커스텀 안전 관리 데이터셋)
    DEFAULT_CLASSES = {
        0: "person",
        1: "helmet",
        2: "vest",
        3: "fire",
        4: "smoke",
        5: "fall"
    }

    # COCO 80 클래스 매핑 (pretrained 모델 사용 시)
    COCO_CLASSES = {
        0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 4: "airplane",
        5: "bus", 6: "train", 7: "truck", 8: "boat", 9: "traffic light",
        10: "fire hydrant", 11: "stop sign", 12: "parking meter", 13: "bench",
        14: "bird", 15: "cat", 16: "dog", 17: "horse", 18: "sheep",
        19: "cow", 20: "elephant", 21: "bear", 22: "zebra", 23: "giraffe",
        24: "backpack", 25: "umbrella", 26: "handbag", 27: "tie", 28: "suitcase",
        29: "frisbee", 30: "skis", 31: "snowboard", 32: "sports ball", 33: "kite",
        34: "baseball bat", 35: "baseball glove", 36: "skateboard", 37: "surfboard",
        38: "tennis racket", 39: "bottle", 40: "wine glass", 41: "cup",
        42: "fork", 43: "knife", 44: "spoon", 45: "bowl", 46: "banana",
        47: "apple", 48: "sandwich", 49: "orange", 50: "broccoli",
        51: "carrot", 52: "hot dog", 53: "pizza", 54: "donut", 55: "cake",
        56: "chair", 57: "couch", 58: "potted plant", 59: "bed",
        60: "dining table", 61: "toilet", 62: "tv", 63: "laptop", 64: "mouse",
        65: "remote", 66: "keyboard", 67: "cell phone", 68: "microwave",
        69: "oven", 70: "toaster", 71: "sink", 72: "refrigerator",
        73: "book", 74: "clock", 75: "vase", 76: "scissors", 77: "teddy bear",
        78: "hair drier", 79: "toothbrush"
    }

    def __init__(
        self,
        model_path: str,
        input_size: tuple[int, int] = (640, 640),
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        classes: dict[int, str] | Sequence[str] | None = None,
        device: str = "cuda",
        fp16: bool = True,
        postprocess_mode: str = "auto",
        class_profile: str = "coco"
    ):
        self.model_path = Path(model_path)
        self.input_size = input_size
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.class_profile = class_profile
        self.device = device
        self.fp16 = fp16
        self.postprocess_mode = postprocess_mode

        self.session: ort.InferenceSession | None = None
        self.input_name: str = ""
        self.input_dtype = np.float32
        self.output_names: list[str] = []
        self._logged_output_shapes = False
        self._onnx_class_names: dict[int, str] | None = None
        # NOTE: letterbox 패딩/스케일은 인스턴스 상태(self.x)에 저장하지 않는다.
        # 단일 detector 인스턴스가 여러 카메라 스레드에서 공유되므로(MultiModelRegistry),
        # preprocess(write)↔postprocess(read) 사이에 다른 스레드의 preprocess가 끼어들면
        # 잘못된 (scale,pad)로 역보정되는 race가 생긴다. 대신 preprocess가 pad를 반환해
        # detect()가 후처리에 인자로 전달한다(stateless).

        self._init_session()
        # 클래스 매핑은 ONNX 모델에 내장된 메타데이터(names)를 1차 출처로 사용한다.
        # config의 classes는 명시적 override로만 쓰이며, 개수가 모델과 다르면
        # 메타데이터를 신뢰해 silent 오라벨을 방지한다. (_resolve_classes 참고)
        self.classes = self._resolve_classes(classes, class_profile, self._onnx_class_names)
        logger.info(f"YOLODetector 초기화 완료: {model_path}")

    @classmethod
    def _resolve_classes(
        cls,
        classes: dict[int, str] | Sequence[str] | None,
        class_profile: str = "coco",
        onnx_names: dict[int, str] | None = None,
    ) -> dict[int, str]:
        """클래스 id → 이름 매핑을 결정한다.

        우선순위(근본 수정):
        1. ONNX 모델에 내장된 `names` 메타데이터(`onnx_names`)를 1차 출처로 사용한다.
           대부분의 ultralytics export는 정확한 클래스 이름/순서를 내장하므로,
           이것이 가장 신뢰할 수 있는 ground truth다.
        2. config의 명시적 `classes`는 override로만 사용한다. 단, ONNX 메타데이터가
           있는데 개수가 다르면(=config가 잘못됨) 메타데이터를 신뢰하고 경고한다.
           이렇게 해서 "config가 모델 출력과 어긋나 silent 오라벨"되는 버그 클래스를
           구조적으로 제거한다.
        3. 메타데이터도 명시적 classes도 없으면 `class_profile`(COCO/safety)로 폴백한다.
        """
        explicit = cls._normalize_classes(classes)
        meta = {int(k): str(v) for k, v in onnx_names.items()} if onnx_names else None

        if explicit:
            if meta and len(explicit) != len(meta):
                logger.warning(
                    "config classes 개수(%d)가 ONNX 모델 실제 클래스 개수(%d)와 불일치합니다. "
                    "오라벨 방지를 위해 ONNX 메타데이터(names)를 사용합니다: %s",
                    len(explicit), len(meta), meta,
                )
                return dict(meta)
            return explicit

        if meta:
            logger.info("ONNX 메타데이터에서 클래스 %d개를 자동 인식했습니다.", len(meta))
            return dict(meta)

        profile = (class_profile or "coco").strip().lower()
        if profile in {"coco", "pretrained", "yolo26_coco", "yolo26-coco"}:
            logger.info(
                "COCO 클래스 프로파일 사용: 안전모/조끼/화재/연기/쓰러짐을 직접 탐지할 수 없습니다. "
                "VLM 분석 또는 safety_custom 모델 필요."
            )
            return dict(cls.COCO_CLASSES)
        if profile in {"safety", "safety_custom", "custom"}:
            logger.warning(
                "ONNX 메타데이터 없이 class_profile=%s 폴백을 사용합니다. "
                "모델 출력과 클래스 이름이 어긋날 수 있으니 config의 classes를 명시하세요.",
                class_profile,
            )
            return dict(cls.DEFAULT_CLASSES)

        logger.warning("알 수 없는 class_profile=%s, COCO 클래스를 사용합니다", class_profile)
        return dict(cls.COCO_CLASSES)

    @staticmethod
    def _normalize_classes(
        classes: dict[int, str] | Sequence[str] | None,
    ) -> dict[int, str] | None:
        """config classes 값(dict/list/CSV str)을 {id: name}으로 정규화. 비어 있으면 None."""
        if not classes:
            return None
        if isinstance(classes, dict):
            return {int(k): str(v) for k, v in classes.items()}
        if isinstance(classes, str):
            normalized = {
                idx: name.strip()
                for idx, name in enumerate(classes.split(','))
                if name.strip()
            }
            return normalized or None
        normalized = {idx: str(name) for idx, name in enumerate(classes)}
        return normalized or None

    @staticmethod
    def _extract_onnx_class_names(session: ort.InferenceSession) -> dict[int, str] | None:
        """ONNX 모델 메타데이터에서 클래스 이름 맵을 추출한다.

        ultralytics export는 `names`를 stringified dict("{0: 'person', ...}")로
        custom_metadata_map에 저장한다. 파싱 실패/부재 시 None을 반환해
        호출부가 config/class_profile로 폴백하게 한다.
        """
        try:
            metadata = session.get_modelmeta().custom_metadata_map or {}
        except Exception as exc:  # 메타데이터 접근 실패는 치명적이지 않음
            logger.debug("ONNX 메타데이터 접근 실패: %s", exc)
            return None

        raw = metadata.get("names")
        if not raw:
            return None

        parsed = None
        for loader in (ast.literal_eval, json.loads):
            try:
                parsed = loader(raw)
                break
            except (ValueError, SyntaxError, TypeError):
                continue

        if not isinstance(parsed, dict) or not parsed:
            return None
        try:
            return {int(k): str(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            return None

    def _init_session(self):
        """ONNX 세션 초기화"""
        try:
            if not self.model_path.exists():
                raise FileNotFoundError(
                    f"YOLO ONNX 모델을 찾을 수 없습니다: {self.model_path}. "
                    "예: yolo export model=yolo26n.pt format=onnx imgsz=640 half=True device=0"
                )

            # 방어선 이중화(#12): 업로드 검증을 우회해 배치/교체(TOCTOU)된 모델도 런타임 로드 직전
            # external-data 참조를 거부한다. InferenceSession 생성 전 그래프만 파싱(외부파일 미read).
            # 정상 self-contained ONNX(기본 제공/export본)는 통과. onnx 미설치 시 검사만 생략(로드는 진행
            # — 핵심 탐지 경로를 방어선 부재로 막지 않는다. onnx는 런타임 필수 의존이라 통상 존재).
            try:
                import onnx  # noqa: F401

                from src.models.upload_validator import validate_onnx_no_external_data
                ok, err = validate_onnx_no_external_data(self.model_path)
            except ImportError:
                logger.warning("onnx 미설치 — external-data 안전 검사 생략: %s", self.model_path)
                ok, err = True, ""
            if not ok:
                raise ValueError(f"안전하지 않은 ONNX 모델 거부: {err}")

            available = set(ort.get_available_providers())
            providers = []
            if self.device in {"cuda", "auto"} and "CUDAExecutionProvider" in available:
                providers.append("CUDAExecutionProvider")
            providers.append("CPUExecutionProvider")

            sess_options = ort.SessionOptions()
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            self.session = ort.InferenceSession(
                str(self.model_path),
                sess_options=sess_options,
                providers=providers
            )

            input_meta = self.session.get_inputs()[0]
            self.input_name = input_meta.name
            self.input_dtype = np.float16 if "float16" in input_meta.type else np.float32
            self.output_names = [o.name for o in self.session.get_outputs()]

            # 모델에 내장된 클래스 이름(ultralytics export의 `names` 메타데이터) 추출
            self._onnx_class_names = self._extract_onnx_class_names(self.session)

            output_shapes = [getattr(o, "shape", None) for o in self.session.get_outputs()]
            logger.info(
                "ONNX 세션 생성 완료 "
                f"(Providers: {self.session.get_providers()}, "
                f"Input: name={self.input_name}, type={input_meta.type}, "
                f"shape={input_meta.shape}, Outputs: {output_shapes}, "
                f"ONNX names: {self._onnx_class_names})"
            )

        except Exception as e:
            logger.error(f"ONNX 세션 초기화 실패: {e}")
            raise

    def preprocess(self, image: np.ndarray) -> tuple[np.ndarray, tuple[float, int, int]]:
        """이미지 전처리 (letterbox).

        종횡비를 유지한 채로 input_size에 맞게 리사이즈하고 남는 영역을 128(gray)로
        패딩한다. 패딩/스케일 정보는 (인스턴스 상태가 아니라) 반환값으로 넘겨 후처리
        역보정에 사용한다(다중 카메라 스레드 공유 detector의 race 방지).

        Returns:
            (batched_tensor, (scale, pad_left, pad_top))
        """
        ih, iw = image.shape[:2]
        tw, th = self.input_size  # target width, height

        # 스케일: 축소/확대 비율 (w/h 중 작은 쪽 기준)
        scale = min(tw / iw, th / ih)
        new_w = int(round(iw * scale))
        new_h = int(round(ih * scale))

        # 패딩 오프셋 (letterbox 중앙 배치)
        pad_left = (tw - new_w) // 2
        pad_top = (th - new_h) // 2

        # 리사이즈
        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # 패딩 캔버스 생성 (gray 128)
        canvas = np.full((th, tw, 3), 128, dtype=np.uint8)
        canvas[pad_top : pad_top + new_h, pad_left : pad_left + new_w] = resized

        # BGR → RGB
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)

        # 정규화 (0~1)
        dtype = getattr(self, "input_dtype", np.float32)
        normalized = (rgb.astype(np.float32) / 255.0).astype(dtype, copy=False)

        # HWC → CHW
        transposed = np.transpose(normalized, (2, 0, 1))

        # 배치 차원 추가
        batched = np.expand_dims(transposed, axis=0)

        return batched, (scale, pad_left, pad_top)

    def detect(self, image: np.ndarray) -> list[Detection]:
        """
        이미지에서 객체 탐지

        Returns:
            List[Detection]: 탐지된 객체 목록
        """
        if self.session is None:
            logger.error("세션이 초기화되지 않았습니다")
            return []

        start_time = time.time()

        # 전처리 — letterbox 패딩 정보를 지역 변수로 받아 후처리까지 전달(stateless)
        input_tensor, letterbox_pad = self.preprocess(image)

        # 추론
        outputs = self.session.run(self.output_names, {self.input_name: input_tensor})
        self._log_output_shapes_once(outputs)

        # 후처리
        detections = self._postprocess(outputs, image.shape[:2], letterbox_pad)

        inference_time = (time.time() - start_time) * 1000
        logger.debug(f"추론 시간: {inference_time:.1f}ms, 탐지 수: {len(detections)}")

        return detections

    def _log_output_shapes_once(self, outputs: list[np.ndarray]) -> None:
        """실제 ONNX 출력 shape를 최초 1회 로그로 남긴다."""
        if getattr(self, "_logged_output_shapes", False):
            return
        shapes = [tuple(np.asarray(output).shape) for output in outputs]
        dtypes = [str(np.asarray(output).dtype) for output in outputs]
        logger.info("YOLO ONNX actual output shapes=%s dtypes=%s", shapes, dtypes)
        self._logged_output_shapes = True

    def _postprocess(
        self,
        outputs: list[np.ndarray],
        original_shape: tuple[int, int],
        letterbox_pad: tuple[float, int, int] = (1.0, 0, 0),
    ) -> list[Detection]:
        """후처리.

        지원 포맷:
        - Raw YOLO: [1, 4+classes, anchors] 또는 [1, anchors, 4+classes]
        - NMS-free/final: [1, detections, 6] 또는 [detections, 6]
          (x1, y1, x2, y2, score, class_id)

        letterbox_pad: preprocess가 반환한 (scale, pad_left, pad_top). 인스턴스 상태가
        아니라 인자로 받아 좌표 역보정에 사용한다(다중 스레드 공유 detector race 방지).
        직접 호출(테스트)에서는 기본 identity(1.0,0,0).
        """
        predictions = np.asarray(outputs[0])

        # batch 차원 제거
        while predictions.ndim > 2 and predictions.shape[0] == 1:
            predictions = predictions[0]

        if predictions.ndim != 2:
            logger.warning(f"지원하지 않는 YOLO 출력 차원: {predictions.shape}")
            return []

        if self._looks_like_final_output(predictions):
            return self._postprocess_final(predictions, original_shape, letterbox_pad)

        if predictions.shape[0] < predictions.shape[1] and predictions.shape[0] >= 5:
            predictions = predictions.T

        boxes_xyxy = []
        boxes_xywh = []
        scores = []
        class_ids = []

        for pred in predictions:
            # boxes: x_center, y_center, width, height (input-size or normalized)
            cx, cy, w, h = pred[:4]

            if len(pred) < 5:
                continue

            class_scores = pred[4:]
            # YOLOv5 계열처럼 objectness가 포함된 [x,y,w,h,obj,cls...] 형식도 지원한다.
            if len(class_scores) == len(self.classes) + 1:
                objectness = float(class_scores[0])
                class_scores = class_scores[1:] * objectness

            class_id = np.argmax(class_scores)
            score = float(class_scores[class_id])

            if score < self.conf_threshold:
                continue

            x1, y1, x2, y2 = self._scale_xywh_to_original(cx, cy, w, h, original_shape, letterbox_pad)

            boxes_xyxy.append([x1, y1, x2, y2])
            boxes_xywh.append([x1, y1, max(0, x2 - x1), max(0, y2 - y1)])
            scores.append(score)
            class_ids.append(int(class_id))

        # Apply NMS
        if len(boxes_xywh) > 0:
            indices = cv2.dnn.NMSBoxes(
                boxes_xywh, scores,
                score_threshold=self.conf_threshold,
                nms_threshold=self.iou_threshold
            )

            detections = []
            for i in np.array(indices).flatten() if len(indices) > 0 else []:
                detections.append(Detection(
                    class_id=class_ids[i],
                    class_name=self.classes.get(class_ids[i], f"class_{class_ids[i]}"),
                    confidence=scores[i],
                    bbox=tuple(boxes_xyxy[i])
                ))
            return detections

        return []

    def _scale_xywh_to_original(
        self,
        cx: float,
        cy: float,
        w: float,
        h: float,
        original_shape: tuple[int, int],
        letterbox_pad: tuple[float, int, int] = (1.0, 0, 0),
    ) -> tuple[int, int, int, int]:
        """Raw YOLO cxcywh 좌표를 원본 이미지 기준 xyxy로 변환.

        letterbox 역보정: 패딩을 제거한 뒤 스케일로 나눠 원본 좌표를 복원한다.
        Ultralytics ONNX raw 출력은 보통 입력 크기(예: 640) 기준 좌표이지만,
        테스트/일부 export는 0~1 normalized 좌표를 반환할 수 있어 둘 다 처리한다.
        letterbox_pad는 호출자가 인자로 전달한다(인스턴스 상태 미사용 — 직접 호출 시 identity).
        """
        orig_h, orig_w = original_shape
        scale, pad_left, pad_top = letterbox_pad

        x1 = cx - w / 2
        y1 = cy - h / 2
        x2 = cx + w / 2
        y2 = cy + h / 2

        if max(abs(cx), abs(cy), abs(w), abs(h)) <= _NORMALIZED_COORD_MAX:
            # 0~1 normalized 좌표 → 입력 크기 기준으로 환산
            x1 *= self.input_size[0]
            x2 *= self.input_size[0]
            y1 *= self.input_size[1]
            y2 *= self.input_size[1]

        # letterbox 패딩 역보정: (좌표 - 패딩) / 스케일 = 원본 좌표
        x1 = (x1 - pad_left) / scale
        x2 = (x2 - pad_left) / scale
        y1 = (y1 - pad_top) / scale
        y2 = (y2 - pad_top) / scale

        return (
            int(max(0, min(orig_w, x1))),
            int(max(0, min(orig_h, y1))),
            int(max(0, min(orig_w, x2))),
            int(max(0, min(orig_h, y2))),
        )

    def _looks_like_final_output(self, predictions: np.ndarray) -> bool:
        """NMS-free/final output 형태인지 추정"""
        if self.postprocess_mode == "nms_free":
            return True
        if self.postprocess_mode == "nms":
            return False
        if predictions.shape[1] != 6:
            return False

        # 마지막 컬럼이 class id처럼 정수 범위이고, 5번째 컬럼이 confidence 범위이면 final로 판단
        scores = predictions[:, 4]
        class_ids = predictions[:, 5]
        return bool(
            np.all((scores >= 0.0) & (scores <= 1.0))
            and np.all(class_ids >= 0)
            and np.all(np.isclose(class_ids, np.round(class_ids)))
        )

    def _postprocess_final(
        self,
        predictions: np.ndarray,
        original_shape: tuple[int, int],
        letterbox_pad: tuple[float, int, int] = (1.0, 0, 0),
    ) -> list[Detection]:
        """YOLO26 등 NMS-free/final 출력 후처리.

        letterbox 역보정: input_size 기준 좌표에서 패딩을 제거하고
        스케일로 나눠 원본 이미지 기준 좌표로 복원한다.
        letterbox_pad는 호출자가 인자로 전달한다(인스턴스 상태 미사용 — 직접 호출 시 identity).
        """
        detections: list[Detection] = []
        orig_h, orig_w = original_shape
        scale, pad_left, pad_top = letterbox_pad

        for pred in predictions:
            x1, y1, x2, y2, score, class_id = pred[:6]
            if score < self.conf_threshold:
                continue

            # 0~1 normalized 좌표 → input_size 기준으로 환산
            if max(x1, y1, x2, y2) <= _NORMALIZED_COORD_MAX:
                x1 *= self.input_size[0]
                x2 *= self.input_size[0]
                y1 *= self.input_size[1]
                y2 *= self.input_size[1]
            # 이미 원본 이미지 기준 좌표인 경우(패딩이 0이고 scale이 1에 근사) — clamp만
            # 아래 letterbox 역보정이 이 케이스도 올바르게 처리하므로 별도 분기 불필요

            # letterbox 역보정: (좌표 - 패딩) / 스케일 = 원본 좌표
            x1 = (x1 - pad_left) / scale
            x2 = (x2 - pad_left) / scale
            y1 = (y1 - pad_top) / scale
            y2 = (y2 - pad_top) / scale

            x1 = int(max(0, min(orig_w, x1)))
            y1 = int(max(0, min(orig_h, y1)))
            x2 = int(max(0, min(orig_w, x2)))
            y2 = int(max(0, min(orig_h, y2)))
            cls = int(class_id)

            detections.append(Detection(
                class_id=cls,
                class_name=self.classes.get(cls, f"class_{cls}"),
                confidence=float(score),
                bbox=(x1, y1, x2, y2)
            ))

        return detections

    def draw_detections(
        self,
        image: np.ndarray,
        detections: list[Detection],
        color_map: dict[str, tuple[int, int, int]] | None = None
    ) -> np.ndarray:
        """탐지 결과 시각화"""
        result = image.copy()

        # 기본 색상
        default_colors = {
            "person": (0, 255, 0),    # Green
            "helmet": (0, 255, 255),  # Yellow
            "vest": (255, 0, 255),    # Magenta
            "fire": (0, 0, 255),      # Red
            "smoke": (128, 128, 128), # Gray
            "fall": (0, 165, 255),    # Orange
        }
        colors = color_map or default_colors

        for det in detections:
            x1, y1, x2, y2 = det.bbox
            color = colors.get(det.class_name, (255, 255, 255))

            # 박스 그리기
            cv2.rectangle(result, (x1, y1), (x2, y2), color, 2)

            # 레이블
            label = f"{det.class_name}: {det.confidence:.2f}"

            # 텍스트 배경
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
            cv2.rectangle(result, (x1, y1 - th - 10), (x1 + tw, y1), color, -1)
            cv2.putText(result, label, (x1, y1 - 5),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

        return result

    def detect_safety_gear(
        self,
        detections: list[Detection]
    ) -> dict[str, object]:
        """안전장비 탐지 결과 분류

        helmet: person bbox 상단 30% 영역과 overlap 있으면 착용으로 판단
        vest: person bbox 중앙 50% 영역과 overlap 있으면 착용으로 판단
        """
        safety_classes = {"helmet", "vest"}
        supported_classes = set(self.classes.values())
        if not safety_classes.issubset(supported_classes):
            logger.debug(
                "현재 YOLO 클래스 프로파일에는 안전장비 클래스가 없어 "
                "safety_gear_violation 판정을 건너뜁니다"
            )
            return {
                "persons": [d for d in detections if d.class_name == "person"],
                "gear": [],
                "violations": []
            }

        persons = [d for d in detections if d.class_name == "person"]
        gear = [d for d in detections if d.class_name in safety_classes]
        violations: list[Detection] = []

        for person in persons:
            px1, py1, px2, py2 = person.bbox
            ph = py2 - py1

            # Person bbox 영역 계산
            helmet_zone = (px1, py1, px2, py1 + int(ph * 0.3))
            vest_zone = (px1, py1 + int(ph * 0.25), px2, py1 + int(ph * 0.75))

            has_helmet = False
            has_vest = False

            for g in gear:
                # 헬멧: person 상단 30% 영역과 overlap
                if g.class_name == "helmet" and self._check_overlap(g.bbox, helmet_zone, min_overlap=0.15):
                    has_helmet = True
                    continue

                # 조끼: person 중앙 50% 영역과 overlap
                if g.class_name == "vest" and self._check_overlap(g.bbox, vest_zone, min_overlap=0.20):
                    has_vest = True
                    continue

            missing = []
            if not has_helmet:
                missing.append("helmet")
            if not has_vest:
                missing.append("vest")

            if missing:
                violations.append(person)

        return {
            "persons": persons,
            "gear": gear,
            "violations": violations
        }

    @staticmethod
    def _check_overlap(
        box_a: tuple[int, int, int, int],
        box_b: tuple[int, int, int, int],
        min_overlap: float = 0.15
    ) -> bool:
        """box_a가 box_b와 충분히 overlap하는지 확인 (IoU 기반)"""
        ax1, ay1, ax2, ay2 = box_a
        bx1, by1, bx2, by2 = box_b

        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)

        inter_w = max(0, inter_x2 - inter_x1)
        inter_h = max(0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h

        if inter_area == 0:
            return False

        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        if area_a <= 0:
            return False

        # gear bbox 대비 overlap 비율
        overlap_ratio = inter_area / area_a
        return overlap_ratio >= min_overlap

    @staticmethod
    def _compute_iou(
        box_a: tuple[int, int, int, int],
        box_b: tuple[int, int, int, int]
    ) -> float:
        """두 bbox의 IoU 계산"""
        ax1, ay1, ax2, ay2 = box_a
        bx1, by1, bx2, by2 = box_b

        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)

        inter_w = max(0, inter_x2 - inter_x1)
        inter_h = max(0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h

        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
        union = area_a + area_b - inter_area
        if union <= 0:
            return 0.0
        return inter_area / union
