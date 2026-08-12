"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { IncidentStatusBadge } from "@/components/incident-actions";
import { EventDecisionChain, VerificationBadge } from "@/components/event-decision-chain";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { EventItem, IncidentItem } from "@/lib/engine";
import { formatTs } from "@/lib/utils";

const LEVEL_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  critical: "destructive",
  high: "destructive",
  medium: "default",
  low: "secondary",
};
const ALERT_STATE_LABEL: Record<string, string> = {
  provisional: "잠정",
  confirmed: "확인됨",
  likely_false_positive: "오탐 가능",
  needs_review: "검토 필요",
  resolved_false_positive: "오탐 처리됨",
  suppressed: "억제됨",
};
const ALERT_STATE_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  confirmed: "default",
  needs_review: "destructive",
  likely_false_positive: "secondary",
  resolved_false_positive: "secondary",
  suppressed: "secondary",
  provisional: "outline",
};
const MAX_ROWS = 200;
const REFRESH_INTERVALS = [
  { value: 30000, label: "30초" },
  { value: 60000, label: "60초" },
];
const selectCls =
  "h-9 rounded-md border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring";

function uniq(arr: (string | undefined)[]): string[] {
  return [...new Set(arr.filter((v): v is string => !!v))].sort();
}

function eventKey(event: EventItem): string {
  return event.event_id ?? JSON.stringify(event);
}

export function csvCell(value: unknown): string {
  const text = String(value ?? "");
  const safe = /^[\t\r ]*[=+\-@]/.test(text) ? `'${text}` : text;
  return `"${safe.replace(/"/g, '""')}"`;
}

function exportCsv(rows: EventItem[]) {
  const headers = ["timestamp", "camera_id", "event_type", "alert_level", "alert_state", "confidence", "description", "vlm_risk_level", "vlm_recommended_action"];
  const lines = [headers.join(","), ...rows.map((r) => headers.map((h) => csvCell((r as Record<string, unknown>)[h])).join(","))];
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "events.csv";
  a.click();
  URL.revokeObjectURL(url);
}

export function EventsLive({
  initialEvents,
  initialIncidents = [],
  connected,
  cameraNames = {},
}: {
  initialEvents: EventItem[];
  initialIncidents?: IncidentItem[];
  connected: boolean;
  cameraNames?: Record<string, string>;
}) {
  const [events, setEvents] = useState<EventItem[]>(initialEvents);
  const [incidents] = useState<Record<string, IncidentItem>>(
    () => Object.fromEntries(initialIncidents.map((incident) => [incident.event_id, incident])),
  );
  const [live, setLive] = useState(false);
  const [query, setQuery] = useState("");
  const [typeF, setTypeF] = useState("");
  const [levelF, setLevelF] = useState("");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [refreshInterval, setRefreshInterval] = useState(30000);

  const mergeEvents = useCallback((prev: EventItem[], next: EventItem[]) => {
    const seen = new Set<string>();
    const result: EventItem[] = [];
    for (const e of next) { const id = eventKey(e); if (!seen.has(id)) { seen.add(id); result.push(e); } }
    for (const e of prev) { const id = eventKey(e); if (!seen.has(id)) { seen.add(id); result.push(e); } }
    return result.slice(0, MAX_ROWS);
  }, []);

  const reconcile = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await fetch("/api/events?limit=200", { signal });
      if (!res.ok) return;
      const data = (await res.json()) as { events?: EventItem[] };
      if (!Array.isArray(data.events)) return;
      setEvents((prev) => mergeEvents(prev, data.events!));
    } catch { /* silent */ }
  }, [mergeEvents]);

  useEffect(() => {
    if (!connected) return;
    const controller = new AbortController();
    const es = new EventSource("/api/events/stream");
    es.addEventListener("alert", (e) => {
      try { setEvents((prev) => mergeEvents(prev, [JSON.parse((e as MessageEvent).data) as EventItem])); } catch { /* ignore */ }
    });
    es.addEventListener("reconciliation-required", () => { void reconcile(controller.signal); });
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    return () => { controller.abort(); es.close(); };
  }, [connected, mergeEvents, reconcile]);

  useEffect(() => {
    if (!autoRefresh || !connected) return;
    const controller = new AbortController();
    const tick = () => void reconcile(controller.signal);
    tick();
    const id = setInterval(tick, refreshInterval);
    return () => { controller.abort(); clearInterval(id); };
  }, [autoRefresh, connected, refreshInterval, reconcile]);

  const types = useMemo(() => uniq(events.map((e) => e.event_type)), [events]);
  const levels = useMemo(() => uniq(events.map((e) => e.alert_level)), [events]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return events.filter((e) => {
      if (typeF && e.event_type !== typeF) return false;
      if (levelF && e.alert_level !== levelF) return false;
      if (q) {
        const hay = `${e.description ?? ""} ${e.event_type ?? ""} ${e.camera_id ?? ""} ${e.camera_id ? cameraNames[e.camera_id] ?? "" : ""} ${e.vlm_recommended_action ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [cameraNames, events, query, typeF, levelF]);

  const selectedEvent = useMemo(
    () => filtered.find((e) => eventKey(e) === selectedKey) ?? filtered[0] ?? null,
    [filtered, selectedKey],
  );

  return (
    <div data-workspace className="flex h-full flex-col">
      <div className="shrink-0 space-y-2 border-b px-4 py-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className={`inline-block h-2 w-2 rounded-full ${live ? "bg-emerald-500" : "bg-muted-foreground/40"}`} aria-hidden />
            {live ? "실시간 수신 중" : connected ? "연결 대기" : "엔진 미연결"}
            <span className="ml-2">총 {filtered.length}건</span>
          </div>
          <Button size="sm" variant="outline" disabled={filtered.length === 0} onClick={() => exportCsv(filtered)}>
            CSV 내보내기
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Input aria-label="이벤트 검색" placeholder="설명·카메라·권고 검색" value={query} onChange={(e) => setQuery(e.target.value)} className="h-9 max-w-xs" />
          <select aria-label="이벤트 유형 필터" className={selectCls} value={typeF} onChange={(e) => setTypeF(e.target.value)}>
            <option value="">모든 유형</option>
            {types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <select aria-label="위험 등급 필터" className={selectCls} value={levelF} onChange={(e) => setLevelF(e.target.value)}>
            <option value="">모든 등급</option>
            {levels.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
          <div className="flex items-center gap-2 rounded-md border px-3 py-1 text-sm">
            <button
              type="button"
              role="switch"
              aria-label={`자동 새로고침 ${autoRefresh ? "끄기" : "켜기"}`}
              aria-checked={autoRefresh}
              tabIndex={0}
              onClick={() => setAutoRefresh((v) => !v)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setAutoRefresh((v) => !v); } }}
              className="relative size-11 shrink-0 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span aria-hidden="true" className={`absolute top-3 left-1 h-5 w-9 rounded-full transition-colors ${autoRefresh ? "bg-emerald-500" : "bg-muted-foreground/30"}`}>
                <span className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white transition-transform ${autoRefresh ? "translate-x-4" : "translate-x-0"}`} />
              </span>
            </button>
            <span className="text-muted-foreground">{autoRefresh ? "켜짐" : "꺼짐"}</span>
            {autoRefresh && (
              <select className={`${selectCls} h-7 px-1 text-xs`} value={refreshInterval} onChange={(e) => setRefreshInterval(Number(e.target.value))} aria-label="자동 새로고침 간격">
                {REFRESH_INTERVALS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
              </select>
            )}
          </div>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {filtered.length === 0 ? (
            <div className="flex flex-1 items-center justify-center p-8">
              <p className="text-sm text-muted-foreground">{connected ? "조건에 맞는 이벤트가 없습니다." : "엔진 API에 연결되지 않았습니다."}</p>
            </div>
          ) : (
            <div className="flex-1 overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10 bg-muted/50 text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="whitespace-nowrap px-3 py-2.5 font-medium">시각</th>
                    <th className="whitespace-nowrap px-3 py-2.5 font-medium">카메라</th>
                    <th className="whitespace-nowrap px-3 py-2.5 font-medium">유형</th>
                    <th className="whitespace-nowrap px-3 py-2.5 font-medium">등급</th>
                    <th className="px-3 py-2.5 font-medium">설명</th>
                    <th className="whitespace-nowrap px-3 py-2.5 font-medium text-right">신뢰도</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((e, i) => {
                    const key = e.event_id ?? String(i);
                    const isSelected = key === (selectedEvent ? eventKey(selectedEvent) : null);
                    return (
                      <tr
                        key={key}
                        onClick={() => setSelectedKey(key)}
                        onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); setSelectedKey(key); } }}
                        tabIndex={0}
                        role="row"
                        aria-selected={isSelected}
                        className={cn(
                          "cursor-pointer border-t transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                          isSelected ? "bg-accent/40" : "hover:bg-muted/30",
                        )}
                      >
                        <td className="whitespace-nowrap px-3 py-2.5 text-xs text-muted-foreground">{formatTs(e.timestamp)}</td>
                        <td className="px-3 py-2.5">{e.camera_id ? cameraNames[e.camera_id] ?? e.camera_id : "—"}</td>
                        <td className="px-3 py-2.5"><Badge variant="secondary">{e.event_type ?? "—"}</Badge></td>
                        <td className="px-3 py-2.5">{e.alert_level ? <Badge variant={LEVEL_VARIANT[e.alert_level] ?? "outline"}>{e.alert_level}</Badge> : "—"}</td>
                        <td className="max-w-xs truncate px-3 py-2.5 text-muted-foreground">{e.description ?? "—"}</td>
                        <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums">{typeof e.confidence === "number" ? `${Math.round(e.confidence * 100)}%` : "—"}</td>
                        <td className="whitespace-nowrap px-3 py-2.5 lg:hidden">
                          {e.event_id && (
                            <Link
                              href={`/incidents/${encodeURIComponent(e.event_id)}`}
                              className="inline-flex min-h-9 items-center rounded-md border border-border px-2 text-xs font-medium hover:bg-muted"
                              onClick={(ev) => ev.stopPropagation()}
                            >
                              상세
                            </Link>
                          )}
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
          {selectedEvent ? (
            <EventInspector event={selectedEvent} incident={selectedEvent.event_id ? incidents[selectedEvent.event_id] : undefined} cameraNames={cameraNames} />
          ) : (
            <div className="flex flex-1 items-center justify-center p-4">
              <p className="text-sm text-muted-foreground">이벤트를 선택하세요.</p>
            </div>
          )}
        </div>
      </div>
      {selectedEvent && (
        <div className="border-t lg:hidden">
          <EventInspector event={selectedEvent} incident={selectedEvent.event_id ? incidents[selectedEvent.event_id] : undefined} cameraNames={cameraNames} />
        </div>
      )}
    </div>
  );
}

function EventInspector({ event: e, incident, cameraNames }: { event: EventItem; incident?: IncidentItem; cameraNames: Record<string, string> }) {
  return (
    <div className="flex min-h-0 flex-col overflow-hidden lg:h-full">
      <div className="shrink-0 border-b px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          {e.alert_level && <Badge variant={LEVEL_VARIANT[e.alert_level] ?? "outline"}>{e.alert_level}</Badge>}
          <VerificationBadge event={e} />
          {e.alert_state && !e.verification_state && !e.vlm_unconfirmed && <Badge variant={ALERT_STATE_VARIANT[e.alert_state] ?? "outline"}>{ALERT_STATE_LABEL[e.alert_state] ?? e.alert_state}</Badge>}
          <IncidentStatusBadge status={incident?.status} />
        </div>
        <h3 className="mt-2 text-base font-semibold leading-tight">{e.description ?? e.event_type ?? "이벤트"}</h3>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>{e.camera_id ? cameraNames[e.camera_id] ?? e.camera_id : "—"}</span>
          <span>{formatTs(e.timestamp)}</span>
          {typeof e.confidence === "number" && <span>신뢰도 {Math.round(e.confidence * 100)}%</span>}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {e.evidence_image && (
          <div className="mb-3 overflow-hidden rounded-lg border">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={`/api/captures/${e.evidence_image}`} alt="증거 이미지" className="w-full object-contain" />
          </div>
        )}

        {(e.vlm_risk_level || (e.vlm_detected_events?.length ?? 0) > 0 || e.vlm_recommended_action) && (
          <div className="mb-3 border-t pt-3 text-sm">
            <p className="font-medium">VLM 분석</p>
            <div className="mt-1 space-y-1 text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
              {e.vlm_risk_level && (
                <p>위험도: <Badge variant={LEVEL_VARIANT[e.vlm_risk_level] ?? "outline"} className="ml-1">{e.vlm_risk_level}</Badge>
                  {typeof e.vlm_confidence === "number" && <span className="ml-1">(신뢰도 {e.vlm_confidence.toFixed(2)})</span>}
                </p>
              )}
              {(e.vlm_detected_events?.length ?? 0) > 0 && <p>탐지: {e.vlm_detected_events!.join(", ")}</p>}
              {e.vlm_recommended_action && <p>권고: {e.vlm_recommended_action}</p>}
            </div>
          </div>
        )}

        <EventDecisionChain event={e} />

        {(e.metadata?.track_id !== undefined || e.metadata?.zone_id || e.metadata?.direction_label || typeof e.metadata?.dwell_sec === "number") && (
          <div className="mb-3 border-t pt-3 text-sm">
            <p className="font-medium">추적 정보</p>
            <div className="mt-1 space-y-0.5 text-xs text-muted-foreground">
              {e.metadata.track_id !== undefined && <p>추적 ID: {e.metadata.track_id}</p>}
              {e.metadata.zone_id && <p>영역: {e.metadata.zone_id}</p>}
              {e.metadata.direction_label && <p>방향: {e.metadata.direction_label}</p>}
              {typeof e.metadata.dwell_sec === "number" && <p>체류: {e.metadata.dwell_sec.toFixed(1)}초</p>}
            </div>
          </div>
        )}
      </div>

      <div className="shrink-0 space-y-2 border-t px-4 py-3">
        {e.camera_id && (
          <Link
            href={`/live?camera=${encodeURIComponent(e.camera_id)}${e.event_id ? `&event=${encodeURIComponent(e.event_id)}` : ""}`}
            className="inline-flex min-h-11 w-full items-center justify-center rounded-md border border-border px-4 text-sm font-medium hover:bg-muted"
          >
            라이브 보기
          </Link>
        )}
        {e.event_id && (
          <Link
            href={`/incidents/${encodeURIComponent(e.event_id)}`}
            className="inline-flex min-h-11 w-full items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            사건 상세 및 처리
          </Link>
        )}
      </div>
    </div>
  );
}
