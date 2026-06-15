"""이벤트 정책 rule evaluator 모음."""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from src.detection.yolo_detector import Detection

logger = logging.getLogger(__name__)

RuleResult = dict[str, Any]
ModelDetections = dict[str, list[Detection]]

# pose_anomaly temporal 상태 스토어.
# camera_id → {"height": float, "cy": float, "ts": float}
# RuleEvaluatorRegistry 인스턴스 수명과 동일하게 관리된다.
_PoseState = dict[str, Any]


class RuleEvaluatorRegistry:
    """룰 타입별 평가기 레지스트리."""

    def __init__(self) -> None:
        self._evaluators: dict[str, Callable[[dict, ModelDetections, np.ndarray, dict | None, str, _PoseState | None], RuleResult]] = {
            "direct_detection": self._eval_direct_detection,
            "safety_gear_overlap": self._eval_safety_gear_overlap,
            "count_threshold": self._eval_count_threshold,
            "zone_intrusion": self._eval_zone_intrusion,
            "proximity": self._eval_proximity,
            "person_proximity": self._eval_person_proximity,
            "pose_anomaly": self._eval_pose_anomaly,
        }
        # camera_id별 pose_anomaly 이전 상태 (temporal 판단에 사용)
        self._pose_history: dict[str, _PoseState] = {}

    def evaluate(
        self,
        rule: dict,
        detections_by_model: ModelDetections,
        frame: np.ndarray,
        zones: dict | None = None,
        camera_id: str = "",
    ) -> RuleResult:
        rule_type = rule.get("type", "direct_detection") if isinstance(rule, dict) else "direct_detection"
        evaluator = self._evaluators.get(rule_type)
        if evaluator is None:
            # 오타·미정의 rule type은 조용히 무발생하지 않고 경고 로그를 남긴다.
            logger.warning(
                "알 수 없는 rule type '%s' — YAML 오타 또는 미등록 evaluator. "
                "해당 정책 이벤트는 발생하지 않습니다. camera_id=%s",
                rule_type,
                camera_id or "<unknown>",
            )
            return {"triggered": False, "confidence": 0.0, "reason": f"unknown rule type: {rule_type}"}

        # pose_anomaly에 한해 camera_id별 이전 상태를 전달한다.
        pose_state: _PoseState | None = None
        if rule_type == "pose_anomaly":
            pose_state = self._pose_history.get(camera_id)

        result = evaluator(rule or {}, detections_by_model, frame, zones or {}, camera_id, pose_state)

        # pose_anomaly 평가 후 상태를 업데이트한다.
        if rule_type == "pose_anomaly" and "pose_state_update" in result:
            if camera_id:
                self._pose_history[camera_id] = result.pop("pose_state_update")
            else:
                result.pop("pose_state_update", None)

        return result

    # ------------------------------------------------------------------
    # 개별 evaluator 구현
    # ------------------------------------------------------------------

    def _eval_direct_detection(
        self, rule: dict, detections_by_model: ModelDetections, frame, zones, camera_id, pose_state
    ) -> RuleResult:
        classes = set(rule.get("classes") or [])
        min_conf = float(rule.get("min_confidence", 0.0))
        detections = self._flatten(detections_by_model)
        if classes:
            detections = [d for d in detections if d.class_name in classes]
        detections = [d for d in detections if d.confidence >= min_conf]
        return self._result_from_detections(detections, "direct_detection")

    def _eval_count_threshold(
        self, rule: dict, detections_by_model: ModelDetections, frame, zones, camera_id, pose_state
    ) -> RuleResult:
        class_name = rule.get("class_name") or rule.get("class") or "person"
        min_count = int(rule.get("min_count", 1))
        min_conf = float(rule.get("min_confidence", 0.0))
        detections = [
            d for d in self._flatten(detections_by_model)
            if d.class_name == class_name and d.confidence >= min_conf
        ]
        triggered = len(detections) >= min_count
        return {
            "triggered": triggered,
            "confidence": max((d.confidence for d in detections), default=0.0),
            "detections": detections,
            "description": f"{class_name} count={len(detections)} threshold={min_count}",
            "reason": "count_threshold" if triggered else "below_threshold",
        }

    def _eval_safety_gear_overlap(
        self, rule: dict, detections_by_model: ModelDetections, frame, zones, camera_id, pose_state
    ) -> RuleResult:
        gear_name = rule.get("gear", "helmet")
        person_zone = rule.get("person_zone", "head")
        min_overlap = float(rule.get("min_overlap", 0.15))
        min_conf = float(rule.get("min_confidence", 0.0))
        detections = [d for d in self._flatten(detections_by_model) if d.confidence >= min_conf]
        persons = [d for d in detections if d.class_name == "person"]
        gear = [d for d in detections if d.class_name == gear_name]
        violations: list[Detection] = []

        for person in persons:
            zone = self._person_zone(person.bbox, person_zone)
            has_gear = any(self._overlap_ratio(g.bbox, zone) >= min_overlap for g in gear)
            if not has_gear:
                violations.append(person)

        return {
            "triggered": bool(violations),
            "confidence": max((d.confidence for d in violations), default=0.0),
            "detections": violations,
            "description": f"missing {gear_name}: {len(violations)} person(s)",
            "reason": "safety_gear_overlap" if violations else "gear_present_or_no_person",
            "ambiguous": bool(persons and not gear),
        }

    def _eval_zone_intrusion(
        self, rule: dict, detections_by_model: ModelDetections, frame, zones, camera_id, pose_state
    ) -> RuleResult:
        zone_id = rule.get("zone_id")
        zone = (zones or {}).get(zone_id, {}) if zone_id else {}
        points = zone.get("points", []) if isinstance(zone, dict) else []
        if not points:
            return {"triggered": False, "confidence": 0.0, "detections": [], "reason": "zone_missing"}

        class_name = rule.get("class_name", "person")
        min_conf = float(rule.get("min_confidence", 0.0))
        intruders = []
        for detection in self._flatten(detections_by_model):
            if detection.class_name != class_name or detection.confidence < min_conf:
                continue
            if self._point_in_polygon(self._bbox_center(detection.bbox), points):
                intruders.append(detection)

        return self._result_from_detections(intruders, "zone_intrusion")

    def _eval_proximity(
        self, rule: dict, detections_by_model: ModelDetections, frame, zones, camera_id, pose_state
    ) -> RuleResult:
        between = rule.get("between", ["person", []])
        if not isinstance(between, Sequence) or len(between) != 2:
            return {"triggered": False, "confidence": 0.0, "detections": [], "reason": "invalid_between"}
        left_cls = between[0]
        right_classes = between[1] if isinstance(between[1], list) else [between[1]]
        max_distance = float(rule.get("max_distance_px", 200))
        min_conf = float(rule.get("min_confidence", 0.0))

        detections = [d for d in self._flatten(detections_by_model) if d.confidence >= min_conf]
        left = [d for d in detections if d.class_name == left_cls]
        right = [d for d in detections if d.class_name in right_classes]

        # self-pair 가드: left_cls와 right_classes가 겹치면 같은 detection 인스턴스끼리
        # 페어링되는 것을 스킵한다. 객체 동일성(a is b)으로 판별 — bbox 동치(a.bbox == b.bbox)는
        # 서로 다른 두 객체가 우연히 같은 정수 bbox를 가질 때 잘못 스킵하는 오탐이 있었다.
        # (left/right는 동일 detections 리스트에서 필터링되므로 같은 객체는 동일 인스턴스다.)
        same_class_pair = left_cls in right_classes
        matched = []
        for a in left:
            for b in right:
                if same_class_pair and a is b:
                    continue
                if self._distance(self._bbox_center(a.bbox), self._bbox_center(b.bbox)) <= max_distance:
                    matched.extend([a, b])

        unique = self._unique_detections(matched)
        return self._result_from_detections(unique, "proximity")

    def _eval_person_proximity(
        self, rule: dict, detections_by_model: ModelDetections, frame, zones, camera_id, pose_state
    ) -> RuleResult:
        min_conf = float(rule.get("min_confidence", 0.0))
        persons = [
            d for d in self._flatten(detections_by_model)
            if d.class_name == "person" and d.confidence >= min_conf
        ]
        min_persons = int(rule.get("min_persons", 2))
        max_distance = float(rule.get("max_distance_px", 50))
        matched = []
        for i, a in enumerate(persons):
            for b in persons[i + 1:]:
                if self._distance(self._bbox_center(a.bbox), self._bbox_center(b.bbox)) <= max_distance:
                    matched.extend([a, b])

        unique = self._unique_detections(matched)
        if len(unique) < min_persons:
            unique = []
        return self._result_from_detections(unique, "person_proximity")

    def _eval_pose_anomaly(
        self, rule: dict, detections_by_model: ModelDetections, frame, zones, camera_id, pose_state: _PoseState | None
    ) -> RuleResult:
        """쓰러짐(fall) 후보 판단 — 단일 프레임 종횡비 + temporal 보강.

        판단 조건(OR):
        1. 종횡비 기준: width/height >= aspect_ratio_threshold (기존)
        2. 높이 급감: 이전 프레임 대비 height가 height_drop_ratio(기본 0.45) 이상 감소
        3. 중심 y 급강하: 이전 프레임 대비 중심 y가 cy_drop_ratio(기본 0.35)×이전높이 이상 증가
           (y축은 아래가 +이므로 사람이 쓰러지면 중심 y가 올라갈 수도 내려갈 수도 있으나
            쓰러지면 bbox 높이가 줄고 중심이 낮아지는(y 증가) 패턴이 일반적)

        temporal 상태는 호출자(RuleEvaluatorRegistry.evaluate)가 주입하고,
        평가 후 'pose_state_update' 키로 반환하면 상위에서 저장한다.
        """
        threshold = float(rule.get("aspect_ratio_threshold", 2.0))
        min_conf = float(rule.get("min_confidence", 0.0))
        # temporal 파라미터 — YAML rule에서 재정의 가능
        height_drop_ratio = float(rule.get("height_drop_ratio", 0.45))
        cy_drop_ratio = float(rule.get("cy_drop_ratio", 0.35))
        temporal_enabled = bool(rule.get("temporal", True))
        # stale-state 오탐 방지 게이트:
        # - TTL: 이전 상태가 오래됐으면(사람이 사라졌다 다시 등장 등) 신뢰하지 않는다.
        # - 위치 게이트: 이전 대표 bbox와 중심이 가까울 때만 같은 객체로 보고 비교한다
        #   (멀리 떨어진 다른 사람/카메라에서 멀어진 신규 person과의 cross 비교 오탐 차단).
        pose_ttl_sec = float(rule.get("pose_state_ttl_sec", 2.0))
        max_center_shift = float(rule.get("pose_max_center_shift_ratio", 1.5))

        anomalies = []
        # 이번 프레임에서 person bbox를 수집해 대표 상태를 계산한다.
        persons_this_frame: list[Detection] = []

        for detection in self._flatten(detections_by_model):
            if detection.class_name != "person" or detection.confidence < min_conf:
                continue
            x1, y1, x2, y2 = detection.bbox
            width = max(1, x2 - x1)
            height = max(1, y2 - y1)
            cy = (y1 + y2) / 2.0
            persons_this_frame.append(detection)

            # 조건 1: 단일 프레임 종횡비
            if width / height >= threshold:
                anomalies.append(detection)
                continue

            # 조건 2·3: temporal (이전 상태가 최근이고 같은 객체로 추정될 때만)
            if temporal_enabled and pose_state is not None:
                prev_h = float(pose_state.get("height", 0))
                prev_cx = float(pose_state.get("cx", 0))
                prev_cy = float(pose_state.get("cy", 0))
                prev_ts = float(pose_state.get("ts", 0))
                cx = (x1 + x2) / 2.0
                # TTL: 너무 오래된 상태는 신뢰하지 않는다(사람 부재 후 신규 등장 등).
                fresh = prev_ts > 0 and (time.monotonic() - prev_ts) <= pose_ttl_sec
                # 위치 게이트: 이전 대표 중심과 가까울 때만 같은 객체로 보고 비교한다.
                close = (
                    prev_h > 0
                    and self._distance((cx, cy), (prev_cx, prev_cy)) <= max_center_shift * prev_h
                )
                if prev_h > 0 and fresh and close:
                    # 높이가 height_drop_ratio 이상 급감한 경우
                    height_dropped = (prev_h - height) / prev_h >= height_drop_ratio
                    # 중심 y가 cy_drop_ratio×이전높이 이상 이동(아래로 쓰러짐)
                    cy_dropped = (cy - prev_cy) / prev_h >= cy_drop_ratio
                    if height_dropped or cy_dropped:
                        anomalies.append(detection)

        # 이번 프레임 대표 상태 업데이트 (person 중 가장 큰 bbox 사용)
        new_state: _PoseState | None = None
        if persons_this_frame:
            largest = max(
                persons_this_frame,
                key=lambda d: (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]),
            )
            x1, y1, x2, y2 = largest.bbox
            new_state = {
                "height": float(max(1, y2 - y1)),
                "cx": float((x1 + x2) / 2.0),
                "cy": float((y1 + y2) / 2.0),
                "ts": time.monotonic(),
            }

        result = self._result_from_detections(anomalies, "pose_anomaly")
        if new_state is not None:
            result["pose_state_update"] = new_state
        return result

    # ------------------------------------------------------------------
    # 유틸리티 정적 메서드
    # ------------------------------------------------------------------

    @staticmethod
    def _flatten(detections_by_model: ModelDetections) -> list[Detection]:
        return [d for detections in detections_by_model.values() for d in detections]

    @staticmethod
    def _result_from_detections(detections: list[Detection], reason: str) -> RuleResult:
        return {
            "triggered": bool(detections),
            "confidence": max((d.confidence for d in detections), default=0.0),
            "detections": detections,
            "description": reason,
            "reason": reason if detections else "no_match",
        }

    @staticmethod
    def _person_zone(bbox: tuple[int, int, int, int], zone: str) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = bbox
        height = max(1, y2 - y1)
        if zone == "head":
            return (x1, y1, x2, y1 + int(height * 0.30))
        if zone == "torso":
            return (x1, y1 + int(height * 0.25), x2, y1 + int(height * 0.75))
        return bbox

    @staticmethod
    def _overlap_ratio(box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int]) -> float:
        ax1, ay1, ax2, ay2 = box_a
        bx1, by1, bx2, by2 = box_b
        inter_x1, inter_y1 = max(ax1, bx1), max(ay1, by1)
        inter_x2, inter_y2 = min(ax2, bx2), min(ay2, by2)
        inter_w, inter_h = max(0, inter_x2 - inter_x1), max(0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h
        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        if area_a <= 0:
            return 0.0
        return inter_area / area_a

    @staticmethod
    def _bbox_center(bbox: tuple[int, int, int, int]) -> tuple[float, float]:
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    @staticmethod
    def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    @staticmethod
    def _point_in_polygon(point: tuple[float, float], polygon: Sequence[Sequence[float]]) -> bool:
        x, y = point
        inside = False
        n = len(polygon)
        if n < 3:
            return False
        p1x, p1y = polygon[0]
        for i in range(n + 1):
            p2x, p2y = polygon[i % n]
            if min(p1y, p2y) < y <= max(p1y, p2y) and x <= max(p1x, p2x):
                xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x if p1y != p2y else p1x
                if p1x == p2x or x <= xinters:
                    inside = not inside
            p1x, p1y = p2x, p2y
        return inside

    @staticmethod
    def _unique_detections(detections: list[Detection]) -> list[Detection]:
        seen: set[tuple] = set()
        unique = []
        for detection in detections:
            key = (detection.class_name, detection.bbox)
            if key in seen:
                continue
            seen.add(key)
            unique.append(detection)
        return unique
