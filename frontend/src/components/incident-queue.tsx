"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { CameraOperationalStatus } from "@/components/camera-operational-status";
import { IncidentStatusBadge } from "@/components/incident-actions";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { CameraItem, CameraTelemetryItem, EventItem, IncidentItem, IncidentStatus } from "@/lib/engine";
import { formatTs } from "@/lib/utils";

type QueueItem = {
  incident: IncidentItem;
  event: EventItem | null;
  cameraName: string;
  camera?: CameraItem | null;
  telemetry?: CameraTelemetryItem;
};

export function IncidentQueue({
  items,
  available,
  initialCategory,
}: {
  items: QueueItem[];
  available: boolean;
  initialCategory?: string;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [category, setCategory] = useState(initialCategory === "camera_health" ? "camera_health" : initialCategory === "safety" ? "safety" : "");
  const [status, setStatus] = useState<"" | IncidentStatus>("");

  const filtered = useMemo(
    () =>
      items
        .filter(
          ({ incident }) =>
            incident.status !== "resolved" &&
            (!category || incident.category === category) &&
            (!status || incident.status === status),
        )
        .sort((left, right) => {
          const statusOrder =
            { new: 0, acknowledged: 1, in_progress: 2, resolved: 3 }[left.incident.status] -
            { new: 0, acknowledged: 1, in_progress: 2, resolved: 3 }[right.incident.status];
          if (statusOrder !== 0) return statusOrder;
          const alertOrder =
            ({ critical: 0, high: 1, medium: 2, low: 3 }[
              left.event?.alert_level ?? left.incident.source_event_alert_level ?? ""
            ] ?? 4);
          const otherAlertOrder =
            ({ critical: 0, high: 1, medium: 2, low: 3 }[
              right.event?.alert_level ?? right.incident.source_event_alert_level ?? ""
            ] ?? 4);
          if (alertOrder !== otherAlertOrder) return alertOrder - otherAlertOrder;
          return (
            Date.parse(right.event?.timestamp ?? right.incident.updated_at) -
            Date.parse(left.event?.timestamp ?? left.incident.updated_at)
          );
        }),
    [items, category, status],
  );

  const selectedEventId = searchParams.get("event") ?? filtered[0]?.incident.event_id ?? null;
  const selectedItem = useMemo(
    () => filtered.find(({ incident }) => incident.event_id === selectedEventId) ?? filtered[0] ?? null,
    [filtered, selectedEventId],
  );

  const selectEvent = useCallback(
    (eventId: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("event", eventId);
      router.push(`/incidents?${params.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  if (!available) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="text-sm text-muted-foreground">사건 저장소를 사용할 수 없습니다. 엔진 연결 또는 저장소 상태를 확인하세요.</p>
      </div>
    );
  }

  return (
    <div data-workspace className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-3 border-b px-4 py-2.5">
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">분류</span>
          <select
            aria-label="사건 분류 필터"
            className="h-9 rounded-md border bg-background px-2 text-sm text-foreground"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          >
            <option value="">전체</option>
            <option value="safety">안전</option>
            <option value="camera_health">카메라 상태</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">상태</span>
          <select
            aria-label="처리 상태 필터"
            className="h-9 rounded-md border bg-background px-2 text-sm text-foreground"
            value={status}
            onChange={(event) => setStatus(event.target.value as "" | IncidentStatus)}
          >
            <option value="">미종결 전체</option>
            <option value="new">신규</option>
            <option value="acknowledged">확인</option>
            <option value="in_progress">대응 중</option>
          </select>
        </label>
        <span className="ml-auto text-xs text-muted-foreground">{filtered.length}건</span>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {filtered.length === 0 ? (
            <div className="flex flex-1 items-center justify-center p-8">
              <p className="text-sm text-muted-foreground">조건에 맞는 미종결 사건이 없습니다.</p>
            </div>
          ) : (
            <div className="flex-1 overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10 bg-muted/50 text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="whitespace-nowrap px-3 py-2.5 font-medium">등급</th>
                    <th className="whitespace-nowrap px-3 py-2.5 font-medium">상태</th>
                    <th className="whitespace-nowrap px-3 py-2.5 font-medium">시각</th>
                    <th className="px-3 py-2.5 font-medium">카메라</th>
                    <th className="px-3 py-2.5 font-medium">사건</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map(({ incident, event, cameraName }) => {
                    const severity = event?.alert_level ?? incident.source_event_alert_level;
                    const isSelected = incident.event_id === selectedItem?.incident.event_id;
                    return (
                      <tr
                        key={incident.event_id}
                        onClick={() => selectEvent(incident.event_id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            selectEvent(incident.event_id);
                          }
                        }}
                        tabIndex={0}
                        role="row"
                        aria-selected={isSelected}
                        className={cn(
                          "cursor-pointer border-t transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                          isSelected ? "bg-accent/40" : "hover:bg-muted/30",
                        )}
                      >
                        <td className="whitespace-nowrap px-3 py-2.5">
                          {severity && (
                            <Badge
                              variant={severity === "critical" || severity === "high" ? "destructive" : "secondary"}
                              className="text-[10px]"
                            >
                              {severity}
                            </Badge>
                          )}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5">
                          <IncidentStatusBadge status={incident.status} />
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5 text-xs text-muted-foreground">
                          {formatTs(event?.timestamp ?? incident.updated_at)}
                        </td>
                        <td className="px-3 py-2.5">{cameraName}</td>
                        <td className="max-w-xs truncate px-3 py-2.5">
                          {event?.description ?? event?.event_type ?? incident.source_event_type ?? "이벤트 확인 필요"}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5 lg:hidden">
                          <Link
                            href={`/incidents/${encodeURIComponent(incident.event_id)}`}
                            className="inline-flex min-h-9 items-center rounded-md border border-border px-2 text-xs font-medium hover:bg-muted"
                            onClick={(e) => e.stopPropagation()}
                          >
                            상세
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="hidden w-[360px] shrink-0 flex-col border-l lg:flex">
          {selectedItem ? (
            <QueueInspector item={selectedItem} />
          ) : (
            <div className="flex flex-1 items-center justify-center p-4">
              <p className="text-sm text-muted-foreground">사건을 선택하세요.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function QueueInspector({ item }: { item: QueueItem }) {
  const { incident, event, camera, telemetry, cameraName } = item;
  const severity = event?.alert_level ?? incident.source_event_alert_level;
  const summary = event?.description ?? event?.event_type ?? incident.source_event_type ?? "이벤트 확인 필요";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="shrink-0 border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <IncidentStatusBadge status={incident.status} />
          {severity && (
            <Badge variant={severity === "critical" || severity === "high" ? "destructive" : "secondary"}>
              {severity}
            </Badge>
          )}
        </div>
        <h3 className="mt-2 text-base font-semibold leading-tight">{summary}</h3>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>{cameraName}</span>
          <span>{formatTs(event?.timestamp ?? incident.updated_at)}</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {event?.evidence_image && (
          <div className="mb-3 overflow-hidden rounded-lg border">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`/api/captures/${event.evidence_image}`}
              alt="사건 증거"
              className="w-full object-contain"
            />
          </div>
        )}

        {event?.description && (
          <p className="mb-3 text-sm text-muted-foreground">{event.description}</p>
        )}

        <CameraOperationalStatus camera={camera} telemetry={telemetry} className="sm:grid-cols-1" />

        {(event?.vlm_risk_level || event?.vlm_detected_events?.length) && (
          <div className="mt-3 border-t pt-3 text-sm">
            <p className="font-medium">추가 분석</p>
            <div className="mt-1 space-y-0.5 text-xs text-muted-foreground">
              {event.vlm_risk_level && <p>위험도: {event.vlm_risk_level}</p>}
              {event.vlm_detected_events?.length && <p>탐지: {event.vlm_detected_events.join(", ")}</p>}
            </div>
          </div>
        )}
      </div>

      <div className="shrink-0 border-t px-4 py-3">
        <Link
          href={`/incidents/${encodeURIComponent(incident.event_id)}`}
          className="inline-flex min-h-11 w-full items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          상세 보기
        </Link>
      </div>
    </div>
  );
}
