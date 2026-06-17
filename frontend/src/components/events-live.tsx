"use client";

import { Fragment, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { EventItem } from "@/lib/engine";
import { formatTs } from "@/lib/utils";

const LEVEL_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  critical: "destructive",
  high: "destructive",
  medium: "default",
  low: "secondary",
};
const MAX_ROWS = 200;
const selectCls =
  "h-9 rounded-md border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring";

function uniq(arr: (string | undefined)[]): string[] {
  return [...new Set(arr.filter((v): v is string => !!v))].sort();
}

function exportCsv(rows: EventItem[]) {
  const headers = [
    "timestamp",
    "camera_id",
    "event_type",
    "alert_level",
    "confidence",
    "description",
    "vlm_risk_level",
    "vlm_recommended_action",
  ];
  const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [
    headers.join(","),
    ...rows.map((r) => headers.map((h) => esc((r as Record<string, unknown>)[h])).join(",")),
  ];
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "events.csv";
  a.click();
  URL.revokeObjectURL(url);
}

// SSR 초기 이벤트 + SSE 실시간 prepend + 필터/검색·VLM 상세·CSV export(관제 + Streamlit 패리티).
export function EventsLive({
  initialEvents,
  connected,
}: {
  initialEvents: EventItem[];
  connected: boolean;
}) {
  const [events, setEvents] = useState<EventItem[]>(initialEvents);
  const [live, setLive] = useState(false);
  const [query, setQuery] = useState("");
  const [typeF, setTypeF] = useState("");
  const [levelF, setLevelF] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!connected) return;
    const es = new EventSource("/api/events/stream");
    es.addEventListener("alert", (e) => {
      try {
        const ev = JSON.parse((e as MessageEvent).data) as EventItem;
        setEvents((prev) => {
          if (ev.event_id && prev.some((p) => p.event_id === ev.event_id)) return prev;
          return [ev, ...prev].slice(0, MAX_ROWS);
        });
      } catch {
        /* 잘못된 프레임 무시 */
      }
    });
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    return () => es.close();
  }, [connected]);

  const types = useMemo(() => uniq(events.map((e) => e.event_type)), [events]);
  const levels = useMemo(() => uniq(events.map((e) => e.alert_level)), [events]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return events.filter((e) => {
      if (typeF && e.event_type !== typeF) return false;
      if (levelF && e.alert_level !== levelF) return false;
      if (q) {
        const hay = `${e.description ?? ""} ${e.event_type ?? ""} ${e.camera_id ?? ""} ${e.vlm_recommended_action ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [events, query, typeF, levelF]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of filtered) if (e.alert_level) c[e.alert_level] = (c[e.alert_level] ?? 0) + 1;
    return c;
  }, [filtered]);

  function toggle(id: string) {
    setExpanded((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }

  return (
    <Card>
      <CardContent className="space-y-3 pt-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span
              className={`inline-block h-2 w-2 rounded-full ${live ? "bg-emerald-500" : "bg-muted-foreground/40"}`}
              aria-hidden
            />
            {live ? "실시간 수신 중" : connected ? "실시간 연결 대기" : "엔진 미연결"}
            <span className="ml-2">
              총 {filtered.length}건
              {Object.entries(counts).map(([lv, n]) => (
                <span key={lv} className="ml-1">
                  · {lv} {n}
                </span>
              ))}
            </span>
          </div>
          <Button size="sm" variant="outline" disabled={filtered.length === 0} onClick={() => exportCsv(filtered)}>
            CSV 내보내기
          </Button>
        </div>

        <div className="flex flex-wrap gap-2">
          <Input
            placeholder="설명·카메라·권고 검색"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="h-9 max-w-xs"
          />
          <select className={selectCls} value={typeF} onChange={(e) => setTypeF(e.target.value)}>
            <option value="">모든 유형</option>
            {types.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <select className={selectCls} value={levelF} onChange={(e) => setLevelF(e.target.value)}>
            <option value="">모든 등급</option>
            {levels.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </div>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>시각</TableHead>
              <TableHead>카메라</TableHead>
              <TableHead>유형</TableHead>
              <TableHead>등급</TableHead>
              <TableHead>설명</TableHead>
              <TableHead className="text-right">신뢰도</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={7}
                  className="h-28 text-center text-sm text-muted-foreground"
                  style={{ wordBreak: "keep-all" }}
                >
                  {connected ? "조건에 맞는 이벤트가 없습니다." : "엔진 API에 연결되지 않았습니다."}
                </TableCell>
              </TableRow>
            ) : (
              filtered.map((e, i) => {
                const id = e.event_id ?? String(i);
                const hasDetail =
                  !!e.vlm_risk_level ||
                  !!e.vlm_recommended_action ||
                  (e.vlm_detected_events?.length ?? 0) > 0;
                const isOpen = expanded.has(id);
                return (
                  <Fragment key={id}>
                    <TableRow>
                      <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                        {formatTs(e.timestamp)}
                      </TableCell>
                      <TableCell>{e.camera_id ?? "—"}</TableCell>
                      <TableCell>
                        <Badge variant="secondary">{e.event_type ?? "—"}</Badge>
                      </TableCell>
                      <TableCell>
                        {e.alert_level ? (
                          <Badge variant={LEVEL_VARIANT[e.alert_level] ?? "outline"}>
                            {e.alert_level}
                          </Badge>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell
                        className="max-w-[24rem] truncate text-sm text-muted-foreground"
                        style={{ wordBreak: "keep-all" }}
                      >
                        {e.description ?? "—"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {typeof e.confidence === "number" ? e.confidence.toFixed(2) : "—"}
                      </TableCell>
                      <TableCell>
                        {hasDetail && (
                          <Button size="sm" variant="ghost" onClick={() => toggle(id)}>
                            {isOpen ? "▲" : "상세"}
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                    {isOpen && hasDetail && (
                      <TableRow>
                        <TableCell colSpan={7} className="bg-muted/40 text-xs">
                          <div className="space-y-1" style={{ wordBreak: "keep-all" }}>
                            {e.vlm_risk_level && (
                              <div>
                                <span className="text-muted-foreground">VLM 위험도:</span>{" "}
                                <Badge variant={LEVEL_VARIANT[e.vlm_risk_level] ?? "outline"}>
                                  {e.vlm_risk_level}
                                </Badge>
                                {typeof e.vlm_confidence === "number" && (
                                  <span className="ml-2 text-muted-foreground">
                                    신뢰도 {e.vlm_confidence.toFixed(2)}
                                  </span>
                                )}
                              </div>
                            )}
                            {(e.vlm_detected_events?.length ?? 0) > 0 && (
                              <div>
                                <span className="text-muted-foreground">탐지 상황:</span>{" "}
                                {e.vlm_detected_events!.join(", ")}
                              </div>
                            )}
                            {e.vlm_recommended_action && (
                              <div>
                                <span className="text-muted-foreground">권고 조치:</span>{" "}
                                {e.vlm_recommended_action}
                              </div>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
