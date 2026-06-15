"""이벤트 정책 엔진."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

# SafetyEvent는 pipeline에서 가져온다. policy 코드 자체는 cv2에 직접 의존하지 않지만,
# rule_evaluators → yolo_detector → `import cv2` 및 pipeline → `import cv2` 경로로
# cv2가 transitive하게 필요하다. cv2는 이 프로젝트의 하드 런타임 의존성(requirements.txt)
# 이므로 별도 폴백을 두지 않는다. (cv2 없는 환경에서의 policy 단위 테스트 격리가 필요하면
# Detection/SafetyEvent를 cv2-free 모듈로 분리해야 하며, 이는 detection/pipeline 수정을
# 수반하는 별도 작업이다.)
from src.pipeline.pipeline import SafetyEvent
from src.policy.rule_evaluators import ModelDetections, RuleEvaluatorRegistry
from src.utils.config import ConfigLoader

logger = logging.getLogger(__name__)


@dataclass
class PolicyDecision:
    """정책 평가 결과."""

    event: SafetyEvent
    policy_name: str
    alert_level: str
    cooldown_sec: float
    vlm_needed: bool
    rule_name: str
    source_models: list[str]


class EventPolicyEngine:
    """YAML event policy를 평가해 SafetyEvent 후보를 생성한다."""

    def __init__(
        self,
        policies: dict[str, Any],
        rule_evaluators: RuleEvaluatorRegistry | None = None,
    ):
        self.policies = policies or {}
        self.rule_evaluators = rule_evaluators or RuleEvaluatorRegistry()
        # 알 수 없는 profile에 대해 중복 경고를 억제하기 위한 캐시
        self._warned_profiles: set[str] = set()

    @classmethod
    def from_config(cls, path: str = "./config/event_policies.yaml") -> EventPolicyEngine:
        return cls(ConfigLoader.load(path))

    def evaluate(
        self,
        profile_name: str,
        model_detections: ModelDetections,
        frame: np.ndarray,
        camera_id: str,
        timestamp: datetime | None = None,
        zones: dict | None = None,
        enabled_events: list[str] | None = None,
    ) -> list[PolicyDecision]:
        profile = self.policies.get("profiles", {}).get(profile_name, {})
        policy_map = profile.get("policies", {}) if isinstance(profile, dict) else {}
        if not isinstance(policy_map, dict):
            policy_map = {}

        # 평가 가능한 정책이 하나도 없으면 운영 가시성 경고(세션당 1회) 후 빈 결과.
        # 미정의 profile(빈 dict)뿐 아니라 profile은 등록됐으나 policies 섹션이 없거나 비어있는
        # 경우(예: camera_profiles의 factory/parking 도메인)도 모든 이벤트가 조용히 사라지는데,
        # 이전 `if not profile` 가드는 후자를 놓쳤다. (enabled_events=[]로 인한 의도적 전부 비활성
        # 과는 구분 — 그건 policy_map이 존재하므로 여기 걸리지 않는다.)
        if not policy_map:
            if profile_name not in self._warned_profiles:
                self._warned_profiles.add(profile_name)
                known = list(self.policies.get("profiles", {}).keys())
                logger.warning(
                    "policy profile '%s'에 평가 가능한 정책이 없습니다 "
                    "(event_policies.yaml에 profiles.%s.policies 미정의/비어있음). "
                    "이 카메라의 모든 이벤트가 발생하지 않습니다. 등록된 profile: %s. camera_id=%s",
                    profile_name,
                    profile_name,
                    known,
                    camera_id,
                )
            return []

        # enabled_events가 빈 리스트([])면 "아무 이벤트도 허용 안 함"이어야 한다.
        # (이전 `if enabled_events`는 []를 falsy로 처리해 필터를 해제하는 논리 반전 버그였다.)
        allowed = set(enabled_events) if enabled_events is not None else None
        decisions: list[PolicyDecision] = []

        for policy_name, policy in policy_map.items():
            if not isinstance(policy, dict) or not policy.get("enabled", True):
                continue
            if allowed is not None and policy_name not in allowed:
                continue
            trigger = dict(policy.get("trigger", {}) or {})
            if not self._trigger_matches(trigger, model_detections):
                continue

            # 룰 평가는 trigger.models 범위의 탐지만 사용한다.
            # 여러 모델이 같은 객체를 탐지해 위반이 중복 카운트되는 것을 막고,
            # 정책이 의도한 모델 출력만 보도록 한다.
            trigger_models = trigger.get("models") or []
            scoped_detections = (
                {m: model_detections.get(m, []) for m in trigger_models}
                if trigger_models else model_detections
            )

            rule = dict(policy.get("rule", {}) or {})
            rule.setdefault("classes", trigger.get("classes", []))
            # trigger.min_confidence → rule.min_confidence 상속 (SSOT 규약):
            #   trigger는 "이 클래스/모델의 탐지가 있으면 rule로 넘긴다"는 coarse 게이트.
            #   rule은 정밀 판단을 담당하며 자체 min_confidence를 우선한다.
            #   rule에 min_confidence가 없으면 trigger 값을 상속해 person 저신뢰
            #   박스(fall/fight/crowding에서 0.0이 되는 것)를 걸러낸다.
            #   의도된 person 신뢰 하한은 trigger 또는 rule 어느 쪽에 써도 되나,
            #   동일 정책의 coarse/fine 두 단계에 서로 다른 값을 쓰면 혼동의 원인이 된다 —
            #   권장: trigger.min_confidence에 한 번만 명시하고 rule은 미지정으로 상속.
            rule.setdefault("min_confidence", trigger.get("min_confidence", 0.0))

            # camera_id를 rule_evaluators에 전달 — pose_anomaly temporal 상태 관리에 사용
            rule_result = self.rule_evaluators.evaluate(
                rule, scoped_detections, frame, zones, camera_id
            )
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
    def _determine_vlm_needed(vlm_policy: str, confidence: float, rule_result: dict[str, Any]) -> bool:
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
