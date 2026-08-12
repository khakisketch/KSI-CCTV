"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { IncidentActionItem, IncidentDisposition, IncidentItem, IncidentStatus } from "@/lib/engine";
import { formatTs } from "@/lib/utils";

const STATUS_LABEL: Record<IncidentStatus, string> = {
  new: "신규",
  acknowledged: "확인",
  in_progress: "대응 중",
  resolved: "종결",
};
const STATUS_VARIANT: Record<IncidentStatus, "default" | "secondary" | "destructive" | "outline" | "success" | "warning"> = {
  new: "destructive",
  acknowledged: "warning",
  in_progress: "default",
  resolved: "success",
};

export function IncidentStatusBadge({ status }: { status?: IncidentStatus | null }) {
  if (!status) return <Badge variant="outline">사건 미생성</Badge>;
  return <Badge variant={STATUS_VARIANT[status]}>{STATUS_LABEL[status]}</Badge>;
}

export function IncidentActions({
  eventId,
  incident,
  onUpdated,
}: {
  eventId: string;
  incident?: IncidentItem;
  onUpdated: (incident: IncidentItem) => void;
}) {
  const [note, setNote] = useState("");
  const [disposition, setDisposition] = useState<IncidentDisposition>("confirmed");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [actions, setActions] = useState<IncidentActionItem[]>(incident?.actions ?? []);
  const status = incident?.status;

  useEffect(() => {
    if (!incident || incident.actions) return;
    const controller = new AbortController();
    void fetch(`/api/incidents/${encodeURIComponent(eventId)}`, { signal: controller.signal })
      .then(async (response) => response.ok ? response.json() : null)
      .then((data: { actions?: IncidentActionItem[] } | null) => {
        if (data?.actions) setActions(data.actions);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [eventId, incident]);

  async function act(nextStatus: Exclude<IncidentStatus, "new">) {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/incidents/${encodeURIComponent(eventId)}/actions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status: nextStatus,
          disposition: nextStatus === "resolved" ? disposition : undefined,
          note,
        }),
      });
      const data = (await response.json().catch(() => ({}))) as IncidentItem & { detail?: string };
      if (!response.ok) throw new Error(data.detail ?? "사건 처리 상태를 저장하지 못했습니다.");
      onUpdated(data);
      setActions(data.actions ?? []);
      setNote("");
    } catch (value) {
      setError(value instanceof Error ? value.message : "사건 처리 상태를 저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3 border-t pt-3" aria-label="사건 처리">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">운영자 처리</span>
        <IncidentStatusBadge status={status} />
        {incident?.disposition && <span className="text-muted-foreground">판정: {incident.disposition}</span>}
        {incident?.category === "camera_health" && incident.recovered_at && (
          <span className="text-muted-foreground">자동 복구 감지됨 · 종결은 운영자가 확인합니다</span>
        )}
      </div>
      {!incident ? (
        <p className="text-sm text-muted-foreground">운영자 처리 대상 사건이 아직 생성되지 않았습니다.</p>
      ) : <>
      <label className="block space-y-1">
        <span className="text-muted-foreground">조치 메모 <span className="font-normal">(선택)</span></span>
        <Input
          aria-label="조치 메모"
          className="min-h-11"
          maxLength={2000}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="확인 내용 또는 조치 결과"
        />
      </label>
      {status !== "resolved" && (
        <div className="flex flex-wrap items-center gap-2">
          {status === "new" && <Button className="min-h-11" disabled={busy} onClick={() => void act("acknowledged")}>확인</Button>}
          {status !== "in_progress" && <Button className="min-h-11" variant="outline" disabled={busy} onClick={() => void act("in_progress")}>대응 시작</Button>}
          <label className="flex min-h-11 items-center gap-2 text-sm">
            <span className="text-muted-foreground">종결 판정</span>
            <select
              aria-label="종결 판정"
              className="h-11 rounded-md border bg-background px-2"
              value={disposition}
              onChange={(event) => setDisposition(event.target.value as IncidentDisposition)}
            >
              <option value="confirmed">실제 사건</option>
              <option value="false_positive">오탐</option>
              <option value="duplicate">중복</option>
              <option value="test">시험</option>
            </select>
          </label>
          <Button className="min-h-11" variant="secondary" disabled={busy} onClick={() => void act("resolved")}>종결</Button>
        </div>
      )}
      </>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {actions.length > 0 && (
        <ol className="space-y-1 text-xs text-muted-foreground" aria-label="조치 이력">
          {actions.map((action, index) => (
            <li key={`${action.created_at}-${index}`}>
              {formatTs(action.created_at)} · {STATUS_LABEL[action.status]}
              {action.disposition ? ` · ${action.disposition}` : ""}
              {action.note ? ` · ${action.note}` : ""}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
