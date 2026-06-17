"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
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

// SSR로 받은 초기 이벤트를 보여주고, /api/events/stream(SSE)으로 신규 이벤트를 실시간 prepend한다.
// 새로고침 없이 최신 알람이 표 상단에 쌓인다(관제 UI).
export function EventsLive({
  initialEvents,
  connected,
}: {
  initialEvents: EventItem[];
  connected: boolean;
}) {
  const [events, setEvents] = useState<EventItem[]>(initialEvents);
  const [live, setLive] = useState(false);

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

  return (
    <Card>
      <CardContent className="space-y-3 pt-6">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span
            className={`inline-block h-2 w-2 rounded-full ${live ? "bg-emerald-500" : "bg-muted-foreground/40"}`}
            aria-hidden
          />
          {live ? "실시간 수신 중" : connected ? "실시간 연결 대기" : "엔진 미연결"}
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
            </TableRow>
          </TableHeader>
          <TableBody>
            {events.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="h-28 text-center text-sm text-muted-foreground"
                  style={{ wordBreak: "keep-all" }}
                >
                  {connected ? "이벤트가 없습니다." : "엔진 API에 연결되지 않았습니다."}
                </TableCell>
              </TableRow>
            ) : (
              events.map((e, i) => (
                <TableRow key={e.event_id ?? i}>
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
                    className="max-w-[28rem] truncate text-sm text-muted-foreground"
                    style={{ wordBreak: "keep-all" }}
                  >
                    {e.description ?? "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {typeof e.confidence === "number" ? e.confidence.toFixed(2) : "—"}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
