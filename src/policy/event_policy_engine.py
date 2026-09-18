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
from src.release_capabilities import release_qualification
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

    def _resolve_policy(
        self,
        profile_name: str,
        event_name: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve a selected event to its domain override or canonical policy."""
        profiles = self.policies.get("profiles", {})
        profile = profiles.get(profile_name, {}) if isinstance(profiles, dict) else {}
        policy_map = profile.get("policies", {}) if isinstance(profile, dict) else {}
        direct = policy_map.get(event_name) if isinstance(policy_map, dict) else None

        catalog = self.policies.get("catalog", {})
        metadata = catalog.get(event_name) if isinstance(catalog, dict) else None
        if isinstance(direct, dict):
            return direct, metadata if isinstance(metadata, dict) else None

        canonical_profile = (
            metadata.get("canonical_profile") if isinstance(metadata, dict) else None
        )
        canonical = profiles.get(canonical_profile, {}) if canonical_profile else {}
        canonical_map = canonical.get("policies", {}) if isinstance(canonical, dict) else {}
        fallback = canonical_map.get(event_name) if isinstance(canonical_map, dict) else None
        return (
            fallback if isinstance(fallback, dict) else None,
            metadata if isinstance(metadata, dict) else None,
        )

    @staticmethod
    def _with_behavior(policy: dict[str, Any], metadata: dict[str, Any] | None) -> dict[str, Any]:
        """Attach catalog-owned behavior metadata without changing configured policies."""
        resolved = dict(policy)
        behavior = metadata.get("behavior") if isinstance(metadata, dict) else None
        if isinstance(behavior, dict):
            resolved["behavior"] = dict(behavior)
        verification = metadata.get("verification") if isinstance(metadata, dict) else None
        if isinstance(verification, dict):
            resolved["verification"] = dict(verification)
        return resolved

    def _selected_policies(
        self,
        profile_name: str,
        enabled_events: list[str] | None,
        runtimes: set[str] | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Return executable policies for the camera's explicit event selection.

        A profile supplies defaults and optional threshold overrides. Once a camera
        has an explicit ``enabled_events`` list, that list is authoritative and a
        canonical policy can be resolved from another profile.
        """
        profiles = self.policies.get("profiles", {})
        profile = profiles.get(profile_name, {}) if isinstance(profiles, dict) else {}
        policy_map = profile.get("policies", {}) if isinstance(profile, dict) else {}
        if not isinstance(policy_map, dict):
            policy_map = {}

        allowed_runtimes = runtimes or {"detection"}
        defaults = profile.get("default_events") if isinstance(profile, dict) else None
        names = list(enabled_events) if enabled_events is not None else (
            list(defaults)
            if isinstance(defaults, list)
            else [
                name
                for name, policy in policy_map.items()
                if isinstance(policy, dict) and policy.get("enabled", True)
            ]
        )

        selected: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()
        for event_name in names:
            if event_name in seen:
                continue
            seen.add(event_name)
            policy, metadata = self._resolve_policy(profile_name, event_name)
            if policy is None:
                continue

            if metadata is None:
                # A policy without catalog metadata has no release capability grant.
                continue
            qualified, _reason = release_qualification(
                event_name, str(metadata.get("status", "planned"))
            )
            if not qualified or metadata.get("runtime", "detection") not in allowed_runtimes:
                continue

            selected.append((event_name, self._with_behavior(policy, metadata)))
        return selected

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
        profiles = self.policies.get("profiles", {})
        profile = profiles.get(profile_name, {}) if isinstance(profiles, dict) else {}
        policy_map = profile.get("policies", {}) if isinstance(profile, dict) else {}
        if not isinstance(policy_map, dict):
            policy_map = {}

        # Unknown profiles fail visibly. A known profile may intentionally have
        # an empty policy map and resolve explicit events through catalog fallbacks.
        if not isinstance(profiles, dict) or profile_name not in profiles or not isinstance(profile, dict):
            if profile_name not in self._warned_profiles:
                self._warned_profiles.add(profile_name)
                known = list(self.policies.get("profiles", {}).keys())
                logger.warning(
                    "policy profile '%s'가 정의되어 있지 않습니다. "
                    "이 카메라의 모든 이벤트가 발생하지 않습니다. 등록된 profile: %s. camera_id=%s",
                    profile_name,
                    known,
                    camera_id,
                )
            return []

        defaults = profile.get("default_events")
        if enabled_events is None and not policy_map and not isinstance(defaults, list):
            if profile_name not in self._warned_profiles:
                self._warned_profiles.add(profile_name)
                logger.warning(
                    "policy profile '%s'에 평가 가능한 정책이 없습니다. camera_id=%s",
                    profile_name,
                    camera_id,
                )
            return []

        decisions: list[PolicyDecision] = []

        for policy_name, policy in self._selected_policies(profile_name, enabled_events):
            behavior = policy.get("behavior", {}) or {}
            if bool(behavior.get("enabled", False)):
                # An enabled action model is the trigger source; the legacy bbox
                # heuristic must not emit alongside it.
                continue
            trigger = dict(policy.get("trigger", {}) or {})
            trigger_matches = self._trigger_matches(trigger, model_detections)

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

            if not trigger_matches:
                self.rule_evaluators.observe_miss(rule, camera_id, policy_name)
                continue

            # camera_id와 policy_name을 rule_evaluators에 전달 —
            # pose_anomaly temporal 상태 및 템포럴 게이트 시퀀스 관리에 사용.
            rule_result = self.rule_evaluators.evaluate(
                rule, scoped_detections, frame, zones, camera_id, policy_name
            )
            if not rule_result.get("triggered", False):
                continue

            confidence = float(rule_result.get("confidence", 0.0))
            vlm_needed = self._determine_vlm_needed(policy.get("vlm", "never"), confidence, rule_result)
            detections = list(rule_result.get("detections", []))
            provisional_alert = bool(policy.get("provisional_alert", False))
            verification = policy.get("verification", {}) if vlm_needed else {}
            mode = str(verification.get("mode", "single" if vlm_needed else "none"))
            quorum = int(verification.get("quorum", 1 if vlm_needed else 0))
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
                # 생명안전 이벤트는 VLM 전 즉시 잠정 알림 발송
                provisional_alert=provisional_alert,
                alert_state="provisional" if (provisional_alert or vlm_needed) else "confirmed",
                verification_required=vlm_needed,
                verification_mode=mode,
                verification_state="pending" if vlm_needed else "not_required",
                verification_quorum=quorum,
                metadata={"verification_allow_reject": bool(verification.get("allow_reject", False))} if vlm_needed else {},
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

    def behavior_plans(
        self, profile_name: str, enabled_events: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return selected, enabled catalog behavior execution plans."""
        plans: list[dict[str, Any]] = []
        for event_name, policy in self._selected_policies(profile_name, enabled_events):
            behavior = policy.get("behavior", {}) or {}
            if not bool(behavior.get("enabled", False)):
                continue
            person = behavior.get("person", {}) or {}
            plan = dict(behavior)
            plan.update({
                "event_name": event_name,
                "rule": dict(policy.get("rule", {}) or {}),
                "alert_level": str(policy.get("alert_level", "medium")),
                "cooldown_sec": float(policy.get("cooldown_sec", 30.0)),
                "vlm": str(policy.get("vlm", "never")),
                "provisional_alert": bool(policy.get("provisional_alert", False)),
                "verification": dict(policy.get("verification", {}) or {}),
                "description": str(policy.get("description", "")),
                "person_models": list(person.get("models", []) or []),
                "person_classes": list(person.get("classes", []) or []),
            })
            plans.append(plan)
        return plans

    def required_models(
        self,
        profile_name: str,
        enabled_events: list[str] | None = None,
    ) -> list[str]:
        """카메라 실행 계획에 필요한 모델만 결정적으로 반환한다."""
        models: set[str] = set()
        for _event_name, policy in self._selected_policies(
            profile_name, enabled_events, {"detection", "tracking"}
        ):
            trigger = policy.get("trigger", {}) or {}
            models.update(str(name) for name in trigger.get("models", []) if name)
            behavior = policy.get("behavior", {}) or {}
            if bool(behavior.get("enabled", False)):
                person = behavior.get("person", {}) or {}
                models.update(str(name) for name in person.get("models", []) if name)
                for name in (behavior.get("pose_model"), behavior.get("action_model")):
                    if name:
                        models.add(str(name))
        return sorted(models)

    def trigger_for_event(
        self,
        profile_name: str,
        event_name: str,
        enabled_events: list[str] | None,
        runtime: str,
    ) -> dict[str, Any] | None:
        """Return the executable trigger for one selected runtime event."""
        for selected_name, policy in self._selected_policies(
            profile_name, enabled_events, {runtime}
        ):
            if selected_name == event_name:
                return dict(policy.get("trigger", {}) or {})
        return None

    def effective_tracking_policy(
        self,
        profile_name: str,
        event_name: str,
        enabled_events: list[str] | None,
    ) -> dict[str, Any] | None:
        """Return the complete executable policy for a selected tracking event.

        Includes trigger, rule, alert_level, cooldown_sec, description,
        source_models, and rule_name. Returns None when the event is not
        selected or not a tracking-runtime event.
        """
        for selected_name, policy in self._selected_policies(
            profile_name, enabled_events, {"tracking"}
        ):
            if selected_name != event_name:
                continue
            trigger = dict(policy.get("trigger", {}) or {})
            rule = dict(policy.get("rule", {}) or {})
            return {
                "trigger": trigger,
                "rule": rule,
                "alert_level": str(policy.get("alert_level", "medium")),
                "cooldown_sec": float(policy.get("cooldown_sec", 30.0)),
                "description": str(policy.get("description", "")),
                "source_models": list(trigger.get("models", []) or []),
                "rule_name": str(rule.get("type") or f"track_{event_name}"),
                "vlm": str(policy.get("vlm", "never")),
                "provisional_alert": bool(policy.get("provisional_alert", False)),
            }
        return None

    def tracking_events_for_profile(
        self,
        profile_name: str,
        enabled_events: list[str] | None,
    ) -> list[str]:
        """Return the selected tracking-runtime event names for a camera."""
        names: list[str] = []
        seen: set[str] = set()
        for selected_name, _policy in self._selected_policies(
            profile_name, enabled_events, {"tracking"}
        ):
            if selected_name in seen:
                continue
            seen.add(selected_name)
            names.append(selected_name)
        return names

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
