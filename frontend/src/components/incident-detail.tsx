"use client";

import { ArrowRight, Camera, Clock3 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { CameraOperationalStatus } from "@/components/camera-operational-status";
import { IncidentActions, IncidentStatusBadge } from "@/components/incident-actions";
import { EventDecisionChain, VerificationBadge } from "@/components/event-decision-chain";
import { Badge } from "@/components/ui/badge";
import type { CameraItem, CameraTelemetryItem, EventItem, IncidentItem } from "@/lib/engine";
import { formatTs } from "@/lib/utils";

export function IncidentDetail({
  initialIncident,
  event,
  cameraName,
  camera,
  telemetry,
}: {
  initialIncident: IncidentItem;
  event: EventItem | null;
  cameraName: string;
  camera?: CameraItem | null;
  telemetry?: CameraTelemetryItem;
}) {
  const [incident, setIncident] = useState(initialIncident);
  const summary = event?.description ?? event?.event_type ?? incident.source_event_type ?? "원본 이벤트 확인 필요";
  const severity = event?.alert_level ?? incident.source_event_alert_level;

  return (
    <div data-workspace className="flex h-full flex-col lg:flex-row">
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <div className="shrink-0 border-b px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <IncidentStatusBadge status={incident.status} />
            {event && <VerificationBadge event={event} />}
            {severity && (
              <Badge variant={severity === "critical" || severity === "high" ? "destructive" : "secondary"}>
                {severity}
              </Badge>
            )}
            <Badge variant="outline">{incident.category === "camera_health" ? "카메라 상태" : "안전"}</Badge>
          </div>
          <h2 className="mt-2 text-lg font-semibold leading-tight">{summary}</h2>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <Camera className="size-4" aria-hidden="true" />
              {cameraName}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Clock3 className="size-4" aria-hidden="true" />
              {formatTs(event?.timestamp ?? incident.updated_at)}
            </span>
          </div>
        </div>

        <div className="flex flex-1 items-center justify-center overflow-hidden p-4">
          {event?.evidence_image ? (
            <div className="flex h-full w-full items-center justify-center overflow-hidden rounded-lg border bg-muted/30">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={`/api/captures/${event.evidence_image}`}
                alt="사건 증거 이미지"
                className="max-h-full max-w-full object-contain"
              />
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2 text-muted-foreground">
              <Camera className="size-8" aria-hidden="true" />
              <p className="text-sm">증거 이미지 없음</p>
              {event?.description && <p className="max-w-sm text-center text-xs">{event.description}</p>}
            </div>
          )}
        </div>

        {incident.camera_id && (
          <div className="shrink-0 border-t px-4 py-2.5">
            <Link
              href={`/live?camera=${encodeURIComponent(incident.camera_id)}&event=${encodeURIComponent(incident.event_id)}`}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
            >
              이 카메라 라이브 보기 <ArrowRight className="size-3.5" aria-hidden="true" />
            </Link>
          </div>
        )}
      </div>

      <div className="flex w-full shrink-0 flex-col border-t lg:w-[360px] lg:border-t-0 lg:border-l">
        <div className="shrink-0 border-b px-4 py-2.5">
          <p className="text-sm font-medium">운영 상태</p>
        </div>
        <div className="shrink-0 px-4 py-3">
          <CameraOperationalStatus camera={camera} telemetry={telemetry} className="sm:grid-cols-1" />
        </div>

        {incident.category === "camera_health" && (
          <div className="shrink-0 border-t px-4 py-3 text-sm">
            <p className="font-medium">카메라 복구 상태</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {incident.recovered_at
                ? `자동 복구 감지: ${formatTs(incident.recovered_at)}. 종결 전 운영자 확인이 필요합니다.`
                : "자동 복구가 아직 감지되지 않았습니다."}
            </p>
          </div>
        )}

        {(event?.vlm_risk_level || event?.vlm_detected_events?.length || event?.vlm_recommended_action) && (
          <div className="shrink-0 border-t px-4 py-3 text-sm">
            <p className="font-medium">추가 분석</p>
            <div className="mt-1 space-y-0.5 text-xs text-muted-foreground">
              {event.vlm_risk_level && <p>위험도: {event.vlm_risk_level}</p>}
              {event.vlm_detected_events?.length && <p>탐지: {event.vlm_detected_events.join(", ")}</p>}
              {event.vlm_recommended_action && <p>권고: {event.vlm_recommended_action}</p>}
            </div>
          </div>
        )}
        {event && <div className="shrink-0 border-t px-4 py-3"><EventDecisionChain event={event} /></div>}

        <div className="shrink-0 border-t px-4 py-2.5">
          <p className="text-sm font-medium">운영자 처리</p>
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-3">
          <IncidentActions eventId={incident.event_id} incident={incident} onUpdated={setIncident} />
        </div>
      </div>
    </div>
  );
}
