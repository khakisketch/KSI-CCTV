"""
이벤트 라이프사이클 데이터 모델

SafetyEvent(파이프라인 내부용)와 분리된 영속적 이벤트 모델.
검증 상태, 알림 결정, 전송 시도를 별도 생명주기로 관리한다.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class VerificationVerdict(str, Enum):
    """VLM/분류기 검증 결과 판정"""
    CONFIRM = "confirm"           # 이벤트 확정
    REJECT = "reject"             # 오탐(거짓 양성) — 알림 억제
    ABSTAIN = "abstain"           # 불확실 — 추가 검토 필요
    ERROR = "error"               # 분석 실패 — 억제하지 않음


class AlertLifecycleState(str, Enum):
    """알림 생명주기 상태"""
    PROVISIONAL = "provisional"       # 잠정 알림 (즉시 발송)
    CONFIRMED = "confirmed"           # VLM/분류기 확인
    LIKELY_FALSE_POSITIVE = "likely_false_positive"  # 오탐 가능성 높음
    NEEDS_REVIEW = "needs_review"     # 수동 검토 필요
    RESOLVED_FALSE_POSITIVE = "resolved_false_positive"  # 오탐 확정
    SUPPRESSED = "suppressed"         # 억제됨 (알림 발송 안 함)


class NotificationChannel(str, Enum):
    EMAIL = "email"
    SSE = "sse"
    WEBHOOK = "webhook"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    DEDUPED = "deduped"


@dataclass
class VerificationResult:
    """검증 결과 (VLM, pose classifier, etc.)"""
    verdict: VerificationVerdict
    target_event_type: str
    confidence: float  # 0.0-1.0
    reason_codes: list[str] = field(default_factory=list)
    evidence_frame_ids: list[str] = field(default_factory=list)
    model_name: str = ""
    model_version: str = ""
    prompt_version: str = ""
    parse_valid: bool = True
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "target_event_type": self.target_event_type,
            "confidence": self.confidence,
            "reason_codes": self.reason_codes,
            "evidence_frame_ids": self.evidence_frame_ids,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "parse_valid": self.parse_valid,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class CandidateEvent:
    """후보 이벤트 — VLM/분류기 검증 전 상태"""
    candidate_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    camera_id: str = ""
    event_type: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    confidence: float = 0.0
    detections: list[dict] = field(default_factory=list)
    alert_level: str = "low"
    cooldown_sec: float = 30.0
    source_models: list[str] = field(default_factory=list)
    rule_name: str = ""
    image_ref: str = ""  # 저장된 이미지 경로 또는 프레임 ID
    frame_id: str = ""
    zone: str | None = None
    metadata: dict = field(default_factory=dict)
    # 검증 관련
    vlm_needed: bool = False
    verification_results: list[VerificationResult] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "camera_id": self.camera_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "confidence": self.confidence,
            "detections": self.detections,
            "alert_level": self.alert_level,
            "cooldown_sec": self.cooldown_sec,
            "source_models": self.source_models,
            "rule_name": self.rule_name,
            "image_ref": self.image_ref,
            "frame_id": self.frame_id,
            "zone": self.zone,
            "metadata": self.metadata,
            "vlm_needed": self.vlm_needed,
            "verification_results": [vr.to_dict() for vr in self.verification_results],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class AlertDecision:
    """알림 발송 결정 — 후보 이벤트에서 파생"""
    decision_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    candidate_id: str = ""
    camera_id: str = ""
    event_type: str = ""
    alert_level: str = "low"
    state: AlertLifecycleState = AlertLifecycleState.PROVISIONAL
    provisional_sent: bool = False
    provisional_sent_at: datetime | None = None
    verification_summary: dict = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    resolved_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "candidate_id": self.candidate_id,
            "camera_id": self.camera_id,
            "event_type": self.event_type,
            "alert_level": self.alert_level,
            "state": self.state.value,
            "provisional_sent": self.provisional_sent,
            "provisional_sent_at": self.provisional_sent_at.isoformat() if self.provisional_sent_at else None,
            "verification_summary": self.verification_summary,
            "reason_codes": self.reason_codes,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


@dataclass
class NotificationAttempt:
    """알림 전송 시도 — 멱등키로 중복 방지"""
    attempt_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    decision_id: str = ""
    channel: NotificationChannel = NotificationChannel.EMAIL
    recipient: str = ""
    idempotency_key: str = ""  # decision_id + channel + recipient
    status: NotificationStatus = NotificationStatus.PENDING
    payload: dict = field(default_factory=dict)
    error: str = ""
    sent_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "decision_id": self.decision_id,
            "channel": self.channel.value,
            "recipient": self.recipient,
            "idempotency_key": self.idempotency_key,
            "status": self.status.value,
            "payload": self.payload,
            "error": self.error,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "created_at": self.created_at.isoformat(),
        }


# 위험도 우선순위 (상태 전이 로직용)
RISK_PRIORITY = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def compute_alert_state_from_verdicts(
    verdicts: list[VerificationResult],
    current_alert_level: str,
    is_lifesafety: bool
) -> tuple[AlertLifecycleState, dict]:
    """검증 결과들로부터 알림 상태 계산"""
    if not verdicts:
        if is_lifesafety:
            return AlertLifecycleState.PROVISIONAL, {"status": "no_verification_lifesafety"}
        return AlertLifecycleState.NEEDS_REVIEW, {"status": "no_verification"}

    confirm_count = sum(1 for v in verdicts if v.verdict == VerificationVerdict.CONFIRM)
    reject_count = sum(1 for v in verdicts if v.verdict == VerificationVerdict.REJECT)
    abstain_count = sum(1 for v in verdicts if v.verdict == VerificationVerdict.ABSTAIN)
    error_count = sum(1 for v in verdicts if v.verdict == VerificationVerdict.ERROR)

    summary = {
        "total": len(verdicts),
        "confirm": confirm_count,
        "reject": reject_count,
        "abstain": abstain_count,
        "error": error_count,
        "max_confidence": max((v.confidence for v in verdicts), default=0.0),
    }

    # 생명안전 이벤트는 REJECT만으로 억제하지 않음 — CONFIRM 우선
    if is_lifesafety:
        if confirm_count > 0:
            return AlertLifecycleState.CONFIRMED, summary
        if error_count == len(verdicts):
            return AlertLifecycleState.PROVISIONAL, {**summary, "note": "all_errors_kept_provisional"}
        return AlertLifecycleState.NEEDS_REVIEW, summary

    # 운영 이벤트: REJECT가 있으면 억제 가능
    if reject_count > 0 and confirm_count == 0:
        return AlertLifecycleState.LIKELY_FALSE_POSITIVE, summary

    if confirm_count > 0:
        return AlertLifecycleState.CONFIRMED, summary

    if abstain_count > 0:
        return AlertLifecycleState.NEEDS_REVIEW, summary

    return AlertLifecycleState.NEEDS_REVIEW, summary
