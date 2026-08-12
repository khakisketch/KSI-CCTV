"use client";

import {
  ArrowRight,
  BellRing,
  Camera,
  CheckCircle2,
  Clock3,
  MapPin,
  Radio,
  ShieldAlert,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { IncidentStatusBadge } from "@/components/incident-actions";
import { VerificationBadge } from "@/components/event-decision-chain";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type {
  CameraItem,
  CameraTelemetryItem,
  EventItem,
  IncidentActionItem,
  IncidentDisposition,
  IncidentItem,
  IncidentStatus,
} from "@/lib/engine";
import { isCameraReceiving } from "@/lib/engine";
import { formatTs } from "@/lib/utils";

export type SignalWorkspaceItem = {
  incident: IncidentItem;
  event: EventItem | null;
  camera: CameraItem | null;
  cameraName: string;
  telemetry?: CameraTelemetryItem;
};

const STATUS_ORDER: Record<IncidentStatus, number> = { new: 0, acknowledged: 1, in_progress: 2, resolved: 3 };
const ALERT_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };
const STATUS_LABEL: Record<IncidentStatus, string> = { new: "신규", acknowledged: "확인", in_progress: "대응 중", resolved: "종결" };

function compareSignals(left: SignalWorkspaceItem, right: SignalWorkspaceItem): number {
  const statusOrder = STATUS_ORDER[left.incident.status] - STATUS_ORDER[right.incident.status];
  if (statusOrder !== 0) return statusOrder;
  const alertOrder =
    (ALERT_ORDER[left.event?.alert_level ?? left.incident.source_event_alert_level ?? ""] ?? 4) -
    (ALERT_ORDER[right.event?.alert_level ?? right.incident.source_event_alert_level ?? ""] ?? 4);
  if (alertOrder !== 0) return alertOrder;
  const leftTime = Date.parse(left.event?.timestamp ?? left.incident.updated_at);
  const rightTime = Date.parse(right.event?.timestamp ?? right.incident.updated_at);
  return (Number.isNaN(rightTime) ? 0 : rightTime) - (Number.isNaN(leftTime) ? 0 : leftTime);
}

export function SignalWorkspace({
  initialItems,
  available,
  metrics,
}: {
  initialItems: SignalWorkspaceItem[];
  available: boolean;
  metrics: {
    cameraHealth: string;
    pipeline: string;
    installProgress: string;
    siteName: string;
  };
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [items, setItems] = useState(initialItems);
  const [busyEventId, setBusyEventId] = useState<string | null>(null);
  const [fetchedActions, setFetchedActions] = useState<Record<string, IncidentActionItem[]>>({});
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const prioritized = useMemo(
    () => [...items].filter(({ incident }) => incident.status !== "resolved").sort(compareSignals),
    [items],
  );

  const selectedEventId = searchParams.get("incident") ?? prioritized[0]?.incident.event_id ?? null;
  const selectedItem = useMemo(
    () => prioritized.find(({ incident }) => incident.event_id === selectedEventId) ?? prioritized[0] ?? null,
    [prioritized, selectedEventId],
  );

  const actions = useMemo(() => {
    if (!selectedItem) return [];
    if (selectedItem.incident.actions?.length) return selectedItem.incident.actions;
    return fetchedActions[selectedItem.incident.event_id] ?? [];
  }, [selectedItem, fetchedActions]);

  useEffect(() => {
    if (selectedItem && selectedEventId !== selectedItem.incident.event_id) {
      const params = new URLSearchParams(searchParams.toString());
      params.set("incident", selectedItem.incident.event_id);
      router.replace(`/?${params.toString()}`, { scroll: false });
    }
  }, [selectedItem, selectedEventId, router, searchParams]);

  useEffect(() => {
    if (!selectedItem || selectedItem.incident.actions?.length) return;
    if (fetchedActions[selectedItem.incident.event_id]) return;
    const controller = new AbortController();
    void fetch(`/api/incidents/${encodeURIComponent(selectedItem.incident.event_id)}`, { signal: controller.signal })
      .then(async (response) => (response.ok ? response.json() : null))
      .then((data: { actions?: IncidentActionItem[] } | null) => {
        if (data?.actions) {
          setFetchedActions((prev) => ({ ...prev, [selectedItem.incident.event_id]: data.actions! }));
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [selectedItem, fetchedActions]);

  const selectIncident = useCallback(
    (eventId: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("incident", eventId);
      router.push(`/?${params.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  async function advanceIncident(nextStatus: "acknowledged" | "in_progress" | "resolved", disposition?: IncidentDisposition) {
    if (!selectedItem) return;
    setBusyEventId(selectedItem.incident.event_id);
    setActionError(null);
    setActionSuccess(null);
    try {
      const response = await fetch(`/api/incidents/${encodeURIComponent(selectedItem.incident.event_id)}/actions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus, disposition: nextStatus === "resolved" ? disposition : undefined, note: "" }),
      });
      const updated = (await response.json().catch(() => null)) as IncidentItem & { actions?: IncidentActionItem[]; detail?: string } | null;
      if (!response.ok || !updated) {
        setActionError(updated?.detail ?? "사건 처리 상태를 저장하지 못했습니다.");
        return;
      }
      setItems((current) =>
        current.map((item) =>
          item.incident.event_id === selectedItem.incident.event_id
            ? { ...item, incident: { ...item.incident, ...updated } }
            : item,
        ),
      );
      if (updated.actions) {
        setFetchedActions((prev) => ({ ...prev, [selectedItem.incident.event_id]: updated.actions! }));
      }
      setActionSuccess(
        nextStatus === "acknowledged" ? "사건을 확인 상태로 기록했습니다." :
        nextStatus === "in_progress" ? "대응 시작을 기록했습니다." :
        "사건을 종결했습니다."
      );
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "사건 처리 상태를 저장하지 못했습니다.");
    } finally {
      setBusyEventId(null);
    }
  }

  if (!available) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="text-sm text-muted-foreground">사건 저장소를 사용할 수 없습니다. 엔진 연결 또는 저장소 상태를 확인하세요.</p>
      </div>
    );
  }

  return (
    <div data-workspace className="flex h-full flex-col md:flex-row">
      <div className="flex w-full shrink-0 flex-col border-b md:w-[260px] md:border-b-0 md:border-r lg:w-[280px]">
        <div className="flex shrink-0 items-center justify-between border-b px-3 py-2.5">
          <div className="flex items-center gap-2 text-sm font-medium">
            <BellRing className="size-4 text-primary" aria-hidden="true" />
            <span>사건 대기열</span>
          </div>
          <Badge variant="outline" className="text-xs">{prioritized.length}</Badge>
        </div>
        <div className="flex-1 overflow-y-auto">
          {prioritized.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 p-6 text-center">
              <Radio className="size-5 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
              <p className="text-sm font-medium">미종결 사건 없음</p>
              <p className="text-xs text-muted-foreground">새 사건이 발생하면 여기에 표시됩니다.</p>
            </div>
          ) : (
            <ul role="listbox" aria-label="사건 목록" className="divide-y">
              {prioritized.map((item) => {
                const isSelected = item.incident.event_id === selectedItem?.incident.event_id;
                const severity = item.event?.alert_level ?? item.incident.source_event_alert_level;
                return (
                  <li key={item.incident.event_id}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      onClick={() => selectIncident(item.incident.event_id)}
                      className={cn(
                        "w-full px-3 py-2.5 text-left transition-colors",
                        isSelected ? "bg-accent/50" : "hover:bg-muted/50",
                      )}
                    >
                      <div className="flex items-center gap-1.5">
                        <IncidentStatusBadge status={item.incident.status} />
                        {item.event && <VerificationBadge event={item.event} />}
                        {severity && (
                          <Badge variant={severity === "critical" || severity === "high" ? "destructive" : "secondary"} className="text-[10px] px-1.5 py-0">
                            {severity}
                          </Badge>
                        )}
                      </div>
                      <p className="mt-1.5 text-sm font-medium leading-tight truncate">
                        {item.event?.description ?? item.event?.event_type ?? item.incident.source_event_type ?? "이벤트 확인 필요"}
                      </p>
                      <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                        <span className="truncate">{item.cameraName}</span>
                        <span className="shrink-0">{formatTs(item.event?.timestamp ?? item.incident.updated_at)}</span>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <div className="shrink-0 border-t px-3 py-2">
          <Link href="/incidents" className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
            전체 대기열 <ArrowRight className="size-3" aria-hidden="true" />
          </Link>
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {!selectedItem ? (
          <div className="flex flex-1 items-center justify-center p-8">
            <div className="text-center">
              <Radio className="mx-auto size-8 text-muted-foreground" aria-hidden="true" />
              <p className="mt-2 text-sm text-muted-foreground">처리할 사건을 선택하세요.</p>
            </div>
          </div>
        ) : (
          <>
            <div className="flex flex-1 flex-col overflow-hidden p-4">
              <div className="flex shrink-0 items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <IncidentStatusBadge status={selectedItem.incident.status} />
                    {selectedItem.event && <VerificationBadge event={selectedItem.event} />}
                    {(() => {
                      const severity = selectedItem.event?.alert_level ?? selectedItem.incident.source_event_alert_level;
                      return severity ? (
                        <Badge variant={severity === "critical" || severity === "high" ? "destructive" : "secondary"}>{severity}</Badge>
                      ) : null;
                    })()}
                    <Badge variant="outline">{selectedItem.incident.category === "camera_health" ? "카메라 상태" : "안전"}</Badge>
                  </div>
                  <h2 className="mt-2 text-lg font-semibold leading-tight">
                    {selectedItem.event?.description ?? selectedItem.event?.event_type ?? selectedItem.incident.source_event_type ?? "이벤트 확인 필요"}
                  </h2>
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      <Camera className="size-4" aria-hidden="true" />
                      {selectedItem.cameraName}
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <Clock3 className="size-4" aria-hidden="true" />
                      {formatTs(selectedItem.event?.timestamp ?? selectedItem.incident.updated_at)}
                    </span>
                  </div>
                </div>
              </div>
              <div className="mt-4 flex flex-1 items-center justify-center overflow-hidden rounded-lg border bg-muted/30">
                {selectedItem.event?.evidence_image ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={`/api/captures/${selectedItem.event.evidence_image}`}
                    alt="사건 증거 이미지"
                    className="max-h-full max-w-full object-contain"
                  />
                ) : selectedItem.incident.camera_id ? (
                  <div className="flex flex-col items-center gap-2 text-muted-foreground">
                    <Camera className="size-8" aria-hidden="true" />
                    <p className="text-sm">증거 이미지 없음</p>
                    <Link
                      href={`/live?camera=${encodeURIComponent(selectedItem.incident.camera_id)}&event=${encodeURIComponent(selectedItem.incident.event_id)}`}
                      className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
                    >
                      라이브 확인 <ArrowRight className="size-3.5" aria-hidden="true" />
                    </Link>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-2 text-muted-foreground">
                    <Camera className="size-8" aria-hidden="true" />
                    <p className="text-sm">카메라 정보 없음</p>
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      <div className="flex w-full shrink-0 flex-col border-t md:w-[320px] md:border-t-0 md:border-l lg:w-[360px]">
        <div className="shrink-0 border-b px-3 py-2.5">
          <p className="text-sm font-medium">운영 상태</p>
        </div>
        <div className="shrink-0 space-y-0 divide-y">
          <OperationalStatusRow
            label="영상 수신"
            icon={Radio}
            {...getVideoStatus(selectedItem?.telemetry)}
          />
          <OperationalStatusRow
            label="제한 구역"
            icon={MapPin}
            {...getZoneStatus(selectedItem?.camera)}
          />
          <OperationalStatusRow
            label="감지 설정"
            icon={ShieldAlert}
            {...getDetectionStatus(selectedItem?.camera)}
          />
        </div>

        <div className="shrink-0 border-t px-3 py-2.5">
          <p className="text-sm font-medium">운영자 조치</p>
        </div>
        <div className="flex-1 overflow-y-auto px-3 py-3">
          {selectedItem ? (
            <ActionPanel
              item={selectedItem}
              actions={actions}
              busy={busyEventId === selectedItem.incident.event_id}
              onAdvance={advanceIncident}
              error={actionError}
              success={actionSuccess}
            />
          ) : (
            <p className="text-sm text-muted-foreground">사건을 선택하세요.</p>
          )}
        </div>

        <div className="shrink-0 border-t px-3 py-2">
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span>{metrics.cameraHealth}</span>
            <span className="h-3 w-px bg-border" />
            <span>{metrics.pipeline}</span>
            <span className="h-3 w-px bg-border" />
            <span>{metrics.installProgress}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

type StatusInfo = { value: string; tone: "ready" | "attention" | "unknown" };

function getVideoStatus(telemetry?: CameraTelemetryItem): StatusInfo {
  if (isCameraReceiving(telemetry)) return { value: "정상", tone: "ready" };
  if (telemetry) return { value: telemetry.health_state === "disconnected" ? "단절" : "확인 필요", tone: "attention" };
  return { value: "확인 필요", tone: "unknown" };
}

function getZoneStatus(camera?: CameraItem | null): StatusInfo {
  if (!camera) return { value: "확인 필요", tone: "unknown" };
  const count = camera.zones?.length;
  if (count) return { value: `${count}개 설정`, tone: "ready" };
  return { value: "미설정", tone: "attention" };
}

function getDetectionStatus(camera?: CameraItem | null): StatusInfo {
  if (!camera) return { value: "확인 필요", tone: "unknown" };
  const count = camera.enabled_events?.length;
  if (count) return { value: `${count}개 활성`, tone: "ready" };
  return { value: "미설정", tone: "attention" };
}

function OperationalStatusRow({ label, icon: Icon, value, tone }: { label: string; icon: typeof Radio } & StatusInfo) {
  return (
    <div className="flex items-center gap-3 px-3 py-2.5">
      <Icon
        className={cn(
          "size-4 shrink-0",
          tone === "ready" && "text-emerald-600 dark:text-emerald-400",
          tone === "attention" && "text-amber-700 dark:text-amber-300",
          tone === "unknown" && "text-muted-foreground",
        )}
        aria-hidden="true"
      />
      <span className="text-sm text-muted-foreground w-20 shrink-0">{label}</span>
      <span
        className={cn(
          "text-sm font-medium",
          tone === "ready" && "text-emerald-700 dark:text-emerald-400",
          tone === "attention" && "text-amber-700 dark:text-amber-300",
          tone === "unknown" && "text-muted-foreground",
        )}
      >
        {value}
      </span>
    </div>
  );
}

function ActionPanel({
  item,
  actions,
  busy,
  onAdvance,
  error,
  success,
}: {
  item: SignalWorkspaceItem;
  actions: IncidentActionItem[];
  busy: boolean;
  onAdvance: (status: "acknowledged" | "in_progress" | "resolved", disposition?: IncidentDisposition) => Promise<void>;
  error: string | null;
  success: string | null;
}) {
  const status = item.incident.status;
  const [disposition, setDisposition] = useState<IncidentDisposition>("confirmed");

  return (
    <div className="space-y-3">
      {error && (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}
      {success && !error && (
        <p role="status" aria-live="polite" className="rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-400">
          {success}
        </p>
      )}
      <div className="flex items-center gap-2">
        <IncidentStatusBadge status={status} />
        {item.incident.disposition && (
          <span className="text-xs text-muted-foreground">판정: {item.incident.disposition}</span>
        )}
      </div>

      {status !== "resolved" && (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {status === "new" && (
              <Button className="min-h-11 flex-1" disabled={busy} onClick={() => void onAdvance("acknowledged")}>
                <CheckCircle2 className="mr-1.5 size-4" aria-hidden="true" />
                사건 확인
              </Button>
            )}
            {status !== "in_progress" && status !== "new" && (
              <Button className="min-h-11 flex-1" variant="outline" disabled={busy} onClick={() => void onAdvance("in_progress")}>
                대응 시작
              </Button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <select
              aria-label="종결 판정"
              className="h-11 flex-1 rounded-md border bg-background px-2 text-sm"
              value={disposition}
              onChange={(e) => setDisposition(e.target.value as IncidentDisposition)}
            >
              <option value="confirmed">실제 사건</option>
              <option value="false_positive">오탐</option>
              <option value="duplicate">중복</option>
              <option value="test">시험</option>
            </select>
            <Button className="min-h-11" variant="secondary" disabled={busy} onClick={() => void onAdvance("resolved", disposition)}>
              종결
            </Button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Link
          href={`/incidents/${encodeURIComponent(item.incident.event_id)}`}
          className="inline-flex min-h-11 items-center rounded-md border border-border px-3 text-sm font-medium hover:bg-muted"
        >
          사건 상세
        </Link>
        {item.incident.camera_id && (
          <Link
            href={`/live?camera=${encodeURIComponent(item.incident.camera_id)}&event=${encodeURIComponent(item.incident.event_id)}`}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-md px-3 text-sm font-medium text-primary hover:bg-primary/10"
          >
            라이브 확인 <ArrowRight className="size-3.5" aria-hidden="true" />
          </Link>
        )}
      </div>

      {actions.length > 0 && (
        <div className="border-t pt-2">
          <p className="text-xs font-medium text-muted-foreground mb-1.5">조치 이력</p>
          <ol className="space-y-1 text-xs text-muted-foreground" aria-label="조치 이력">
            {actions.map((action, index) => (
              <li key={`${action.created_at}-${index}`}>
                {formatTs(action.created_at)} · {STATUS_LABEL[action.status]}
                {action.disposition ? ` · ${action.disposition}` : ""}
                {action.note ? ` · ${action.note}` : ""}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
