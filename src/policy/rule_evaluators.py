"""이벤트 정책 rule evaluator 모음."""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from src.detection.yolo_detector import Detection


RuleResult = Dict[str, Any]
ModelDetections = Dict[str, List[Detection]]


class RuleEvaluatorRegistry:
    """룰 타입별 평가기 레지스트리."""

    def __init__(self):
        self._evaluators: Dict[str, Callable[[dict, ModelDetections, np.ndarray, Optional[dict]], RuleResult]] = {
            "direct_detection": self._eval_direct_detection,
            "safety_gear_overlap": self._eval_safety_gear_overlap,
            "count_threshold": self._eval_count_threshold,
            "zone_intrusion": self._eval_zone_intrusion,
            "proximity": self._eval_proximity,
            "person_proximity": self._eval_person_proximity,
            "pose_anomaly": self._eval_pose_anomaly,
        }

    def evaluate(
        self,
        rule: dict,
        detections_by_model: ModelDetections,
        frame: np.ndarray,
        zones: Optional[dict] = None,
    ) -> RuleResult:
        rule_type = rule.get("type", "direct_detection") if isinstance(rule, dict) else "direct_detection"
        evaluator = self._evaluators.get(rule_type)
        if evaluator is None:
            return {"triggered": False, "confidence": 0.0, "reason": f"unknown rule type: {rule_type}"}
        return evaluator(rule or {}, detections_by_model, frame, zones or {})

    def _eval_direct_detection(self, rule: dict, detections_by_model: ModelDetections, frame, zones) -> RuleResult:
        classes = set(rule.get("classes") or [])
        min_conf = float(rule.get("min_confidence", 0.0))
        detections = self._flatten(detections_by_model)
        if classes:
            detections = [d for d in detections if d.class_name in classes]
        detections = [d for d in detections if d.confidence >= min_conf]
        return self._result_from_detections(detections, "direct_detection")

    def _eval_count_threshold(self, rule: dict, detections_by_model: ModelDetections, frame, zones) -> RuleResult:
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

    def _eval_safety_gear_overlap(self, rule: dict, detections_by_model: ModelDetections, frame, zones) -> RuleResult:
        gear_name = rule.get("gear", "helmet")
        person_zone = rule.get("person_zone", "head")
        min_overlap = float(rule.get("min_overlap", 0.15))
        min_conf = float(rule.get("min_confidence", 0.0))
        detections = [d for d in self._flatten(detections_by_model) if d.confidence >= min_conf]
        persons = [d for d in detections if d.class_name == "person"]
        gear = [d for d in detections if d.class_name == gear_name]
        violations: List[Detection] = []

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

    def _eval_zone_intrusion(self, rule: dict, detections_by_model: ModelDetections, frame, zones) -> RuleResult:
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

    def _eval_proximity(self, rule: dict, detections_by_model: ModelDetections, frame, zones) -> RuleResult:
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
        matched = []
        for a in left:
            for b in right:
                if self._distance(self._bbox_center(a.bbox), self._bbox_center(b.bbox)) <= max_distance:
                    matched.extend([a, b])

        unique = self._unique_detections(matched)
        return self._result_from_detections(unique, "proximity")

    def _eval_person_proximity(self, rule: dict, detections_by_model: ModelDetections, frame, zones) -> RuleResult:
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

    def _eval_pose_anomaly(self, rule: dict, detections_by_model: ModelDetections, frame, zones) -> RuleResult:
        threshold = float(rule.get("aspect_ratio_threshold", 2.0))
        min_conf = float(rule.get("min_confidence", 0.0))
        anomalies = []
        for detection in self._flatten(detections_by_model):
            if detection.class_name != "person" or detection.confidence < min_conf:
                continue
            x1, y1, x2, y2 = detection.bbox
            width = max(1, x2 - x1)
            height = max(1, y2 - y1)
            if width / height >= threshold:
                anomalies.append(detection)
        return self._result_from_detections(anomalies, "pose_anomaly")

    @staticmethod
    def _flatten(detections_by_model: ModelDetections) -> List[Detection]:
        return [d for detections in detections_by_model.values() for d in detections]

    @staticmethod
    def _result_from_detections(detections: List[Detection], reason: str) -> RuleResult:
        return {
            "triggered": bool(detections),
            "confidence": max((d.confidence for d in detections), default=0.0),
            "detections": detections,
            "description": reason,
            "reason": reason if detections else "no_match",
        }

    @staticmethod
    def _person_zone(bbox: Tuple[int, int, int, int], zone: str) -> Tuple[int, int, int, int]:
        x1, y1, x2, y2 = bbox
        height = max(1, y2 - y1)
        if zone == "head":
            return (x1, y1, x2, y1 + int(height * 0.30))
        if zone == "torso":
            return (x1, y1 + int(height * 0.25), x2, y1 + int(height * 0.75))
        return bbox

    @staticmethod
    def _overlap_ratio(box_a: Tuple[int, int, int, int], box_b: Tuple[int, int, int, int]) -> float:
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
    def _bbox_center(bbox: Tuple[int, int, int, int]) -> Tuple[float, float]:
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    @staticmethod
    def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    @staticmethod
    def _point_in_polygon(point: Tuple[float, float], polygon: Sequence[Sequence[float]]) -> bool:
        x, y = point
        inside = False
        n = len(polygon)
        if n < 3:
            return False
        p1x, p1y = polygon[0]
        for i in range(n + 1):
            p2x, p2y = polygon[i % n]
            if min(p1y, p2y) < y <= max(p1y, p2y) and x <= max(p1x, p2x):
                if p1y != p2y:
                    xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                else:
                    xinters = p1x
                if p1x == p2x or x <= xinters:
                    inside = not inside
            p1x, p1y = p2x, p2y
        return inside

    @staticmethod
    def _unique_detections(detections: List[Detection]) -> List[Detection]:
        seen = set()
        unique = []
        for detection in detections:
            key = (detection.class_name, detection.bbox)
            if key in seen:
                continue
            seen.add(key)
            unique.append(detection)
        return unique
