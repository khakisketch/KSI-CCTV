"""이벤트 정책 엔진."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from src.pipeline.pipeline import SafetyEvent
from src.policy.rule_evaluators import ModelDetections, RuleEvaluatorRegistry
from src.utils.config import ConfigLoader


@dataclass
class PolicyDecision:
    """정책 평가 결과."""

    event: SafetyEvent
    policy_name: str
    alert_level: str
    cooldown_sec: float
    vlm_needed: bool
    rule_name: str
    source_models: List[str]


class EventPolicyEngine:
    """YAML event policy를 평가해 SafetyEvent 후보를 생성한다."""

    def __init__(
        self,
        policies: Dict[str, Any],
        rule_evaluators: Optional[RuleEvaluatorRegistry] = None,
    ):
        self.policies = policies or {}
        self.rule_evaluators = rule_evaluators or RuleEvaluatorRegistry()

    @classmethod
    def from_config(cls, path: str = "./config/event_policies.yaml") -> "EventPolicyEngine":
        return cls(ConfigLoader.load(path))

    def evaluate(
        self,
        profile_name: str,
        model_detections: ModelDetections,
        frame: np.ndarray,
        camera_id: str,
        timestamp: Optional[datetime] = None,
        zones: Optional[dict] = None,
        enabled_events: Optional[List[str]] = None,
    ) -> List[PolicyDecision]:
        profile = self.policies.get("profiles", {}).get(profile_name, {})
        policy_map = profile.get("policies", {}) if isinstance(profile, dict) else {}
        if not isinstance(policy_map, dict):
            return []

        allowed = set(enabled_events) if enabled_events else None
        decisions: List[PolicyDecision] = []

        for policy_name, policy in policy_map.items():
            if not isinstance(policy, dict) or not policy.get("enabled", True):
                continue
            if allowed is not None and policy_name not in allowed:
                continue
            if not self._trigger_matches(policy.get("trigger", {}), model_detections):
                continue

            rule = dict(policy.get("rule", {}) or {})
            trigger = dict(policy.get("trigger", {}) or {})
            rule.setdefault("classes", trigger.get("classes", []))
            rule.setdefault("min_confidence", trigger.get("min_confidence", 0.0))
            rule_result = self.rule_evaluators.evaluate(rule, model_detections, frame, zones)
            if not rule_result.get("triggered", False):
                continue

            confidence = float(rule_result.get("confidence", 0.0))
            vlm_needed = self._determine_vlm_needed(policy.get("vlm", "never"), confidence, rule_result)
            detections = list(rule_result.get("detections", []))
            event = SafetyEvent(
                event_id=f"{camera_id}_{policy_name}_{time.time_ns()}",
                camera_id=camera_id,
                event_type=policy_name,
                timestamp=timestamp or datetime.now(),
                confidence=confidence,
                detections=detections,
                image=frame.copy(),
                description=policy.get("description") or rule_result.get("description", ""),
                # Policy engine fields (Phase 4)
                alert_level=policy.get("alert_level", "low"),
                cooldown_sec=float(policy.get("cooldown_sec", 30.0)),
                vlm_needed=vlm_needed,
                source_models=list(trigger.get("models", [])),
                rule_name=rule.get("type", "direct_detection"),
            )
            decisions.append(PolicyDecision(
                event=event,
                policy_name=policy_name,
                alert_level=event.alert_level,
                cooldown_sec=event.cooldown_sec,
                vlm_needed=event.vlm_needed,
                rule_name=event.rule_name,
                source_models=list(event.source_models),
            ))

        return decisions

    @staticmethod
    def _trigger_matches(trigger: dict, model_detections: ModelDetections) -> bool:
        models = trigger.get("models", []) or []
        classes = set(trigger.get("classes", []) or [])
        min_conf = float(trigger.get("min_confidence", 0.0))
        if not models:
            return True

        for model_name in models:
            detections = model_detections.get(model_name, [])
            if not detections:
                return False
            if not classes:
                if not any(d.confidence >= min_conf for d in detections):
                    return False
                continue
            found = {
                d.class_name
                for d in detections
                if d.confidence >= min_conf and d.class_name in classes
            }
            if not found.intersection(classes):
                return False
        return True

    @staticmethod
    def _determine_vlm_needed(vlm_policy: str, confidence: float, rule_result: Dict[str, Any]) -> bool:
        policy = (vlm_policy or "never").strip().lower()
        if policy == "never":
            return False
        if policy == "always":
            return True
        if policy == "on_ambiguous":
            return bool(rule_result.get("ambiguous", False) or 0.30 <= confidence <= 0.70)
        if policy == "on_high_confidence":
            return confidence >= 0.70
        if policy == "on_low_confidence":
            return confidence <= 0.50
        return False
