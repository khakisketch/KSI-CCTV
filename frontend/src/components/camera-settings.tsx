"use client";

import { useState } from "react";
import { toast } from "sonner";
import { useRouter } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { CameraZone } from "@/lib/cameras";
import type { EventCatalogItem } from "@/lib/engine";

type Ev = { name: string; alert_level?: string };
type ProfilePreset = { label?: string; default_events: string[] };

export function CameraSettings({
  cameraId,
  initialDomain,
  initialEvents,
  policyProfiles,
  eventCatalog = [],
  profilePresets = {},
  zones = [],
  revision: initialRevision,
  enabled = true,
}: {
  cameraId: string;
  initialDomain?: string;
  initialEvents: string[];
  policyProfiles: Record<string, Ev[]>;
  eventCatalog?: EventCatalogItem[];
  profilePresets?: Record<string, ProfilePreset>;
  zones?: CameraZone[];
  revision?: number;
  enabled?: boolean;
}) {
  const router = useRouter();
  const domains = Object.keys(profilePresets).length > 0
    ? Object.keys(profilePresets)
    : Object.keys(policyProfiles);
  const [open, setOpen] = useState(false);
  const [domain, setDomain] = useState(
    initialDomain && domains.includes(initialDomain) ? initialDomain : (domains[0] ?? ""),
  );
  const [events, setEvents] = useState<string[]>(initialEvents);
  const [busy, setBusy] = useState(false);
  const [revisionOverride, setRevisionOverride] = useState<number>();
  const revision = revisionOverride ?? initialRevision;

  const isOn = (name: string) => events.includes(name);
  const catalogEvents: EventCatalogItem[] = eventCatalog.length > 0
    ? eventCatalog
    : Array.from(
        new Map(
          Object.values(policyProfiles).flat().map((event) => [
            event.name,
            { ...event, label: event.name, selectable: true, reason: null } satisfies EventCatalogItem,
          ]),
        ).values(),
      );
  const selectableNames = new Set(
    catalogEvents.filter((event) => event.selectable !== false).map((event) => event.name),
  );
  const missingZoneEvents = catalogEvents.filter(
    (event) => isOn(event.name) && event.requires_zone && !zones.some((zone) => zone.id === event.requires_zone),
  );

  function applyPreset() {
    const configured = profilePresets[domain]?.default_events;
    const fallback = (policyProfiles[domain] ?? []).map((event) => event.name);
    setEvents((configured ?? fallback).filter((name) => {
      const item = catalogEvents.find((event) => event.name === name);
      return selectableNames.has(name)
        && (!item?.requires_zone || zones.some((zone) => zone.id === item.requires_zone));
    }));
  }

  async function save() {
    setBusy(true);
    try {
      const response = await fetch(`/api/cameras/${encodeURIComponent(cameraId)}/profile`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain, enabled_events: events, expected_revision: revision }),
      });
      const data = (await response.json().catch(() => ({}))) as {
        detail?: string;
        applied?: boolean;
        apply_error?: string | null;
        revision?: number;
      };
      if (response.ok) {
        if (enabled && data.applied !== true) {
          toast.error(data.apply_error ?? "설정은 저장됐지만 분석 서비스에 적용되지 않았습니다.");
          return;
        }
        if (typeof data.revision === "number") setRevisionOverride(data.revision);
        toast.success(data.applied ? "감지 설정 저장과 실행 적용을 확인했습니다." : "비활성 카메라의 감지 설정을 저장했습니다.");
        router.refresh();
      } else {
        toast.error(response.status === 409
          ? "다른 작업에서 카메라 설정이 변경되었습니다. 페이지를 새로고침한 뒤 다시 시도하세요."
          : data.detail ?? "저장 실패");
      }
    } finally {
      setBusy(false);
    }
  }

  if (domains.length === 0) return null;
  if (!open) {
    return (
      <Button className="max-sm:min-h-11" size="sm" variant="ghost" onClick={() => setOpen(true)}>
        설정 편집
      </Button>
    );
  }

  return (
    <div className="space-y-3 border-t pt-3">
      <div className="space-y-2">
        <label className="block text-xs font-medium" htmlFor={`domain-${cameraId}`}>
          현장 유형
        </label>
        <div className="flex gap-2">
          <select
            id={`domain-${cameraId}`}
            className="min-h-11 flex-1 rounded-lg border bg-background px-3 text-sm"
            disabled={busy}
            value={domain}
            onChange={(event) => setDomain(event.target.value)}
          >
            {domains.map((name) => (
              <option key={name} value={name}>{profilePresets[name]?.label ?? name}</option>
            ))}
          </select>
          <Button className="min-h-11" size="sm" variant="outline" disabled={busy} onClick={applyPreset}>
            기본값 적용
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          현장 유형은 추천 기본값이며, 실제 감지는 아래에서 활성화한 항목만 실행합니다.
        </p>
      </div>

      <fieldset className="space-y-1">
        <legend className="text-xs font-medium">감지 항목</legend>
        <div className="grid grid-cols-1 gap-1">
          {catalogEvents.map((event) => {
            const selectable = event.selectable !== false;
            const blockedReason = event.blocked_reason ?? event.reason ?? "이번 릴리스에서 사용할 수 없습니다.";
            const zoneMissing = Boolean(
              event.requires_zone && !zones.some((zone) => zone.id === event.requires_zone),
            );
            return (
              <label
                key={event.name}
                className={`flex min-h-11 items-center gap-2 rounded-md border px-2 text-xs ${selectable ? "" : "opacity-60"}`}
                title={selectable ? event.reason ?? event.name : blockedReason}
              >
                <input
                  type="checkbox"
                  className="size-5 accent-primary"
                  checked={isOn(event.name)}
                  disabled={busy || (!selectable && !isOn(event.name)) || (zoneMissing && !isOn(event.name))}
                  onChange={() =>
                    setEvents((current) =>
                      current.includes(event.name)
                        ? current.filter((name) => name !== event.name)
                        : [...current, event.name],
                    )
                  }
                />
                <span className="min-w-[7rem]">
                  <span className="block">{event.label ?? event.name}</span>
                  {event.runtime === "tracking" && event.tracking && (
                    <span className="block text-[0.7rem] text-muted-foreground">
                      {event.tracking.dwell_time_sec != null && `체류 ${event.tracking.dwell_time_sec}초 · `}
                      신뢰도 {Math.round((event.tracking.min_confidence ?? 0) * 100)}%
                      {event.cooldown_sec != null && ` · 재알림 ${event.cooldown_sec}초`}
                    </span>
                  )}
                </span>
                {event.status === "experimental" && <Badge variant="outline">시험</Badge>}
                {event.runtime === "tracking" && <Badge variant="outline">Tracking</Badge>}
                {!selectable && <Badge variant="outline">{event.release_qualified === false ? "릴리스 미자격" : "준비 중"}</Badge>}
                {zoneMissing && <Badge variant="outline">영역 먼저 설정</Badge>}
                {event.alert_level && (
                  <Badge variant="secondary" className="ml-auto">{event.alert_level}</Badge>
                )}
              </label>
            );
          })}
        </div>
      </fieldset>
      {catalogEvents.some((event) => event.release_qualified === false) && (
        <p className="text-xs text-muted-foreground">릴리스 미자격 시험 기능은 현장 YAML이나 환경 변수로 활성화할 수 없으며, 자격 검증된 릴리스에서만 선택할 수 있습니다.</p>
      )}
      {missingZoneEvents.length > 0 && <p role="alert" className="rounded-md border border-amber-500/50 bg-amber-500/10 p-2 text-xs text-foreground">저장하려면 먼저 필요한 영역을 설정하거나 해당 감지 항목을 해제하세요: {missingZoneEvents.map((event) => event.label ?? event.name).join(", ")}</p>}

      <div className="flex gap-2">
        <Button size="sm" disabled={busy || missingZoneEvents.length > 0} onClick={save}>
          저장 ({events.length})
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setOpen(false)}>
          닫기
        </Button>
      </div>
    </div>
  );
}
