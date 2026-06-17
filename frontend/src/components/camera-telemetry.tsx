"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import type { CameraTelemetryItem } from "@/lib/engine";

// 카메라별 라이브 텔레메트리(연결/FPS/드롭/지연). /api/cameras/telemetry를 8초 폴링.
// 데몬 파이프라인이 mount된 경우만 데이터가 있고, standalone이면 "데몬 미연결"로 안내.
export function CameraTelemetry({ cameraId }: { cameraId: string }) {
  const [t, setT] = useState<CameraTelemetryItem | null | undefined>(undefined);

  useEffect(() => {
    let stop = false;
    async function go() {
      try {
        const r = await fetch("/api/cameras/telemetry", { cache: "no-store" });
        const d = (await r.json()) as { cameras?: Record<string, CameraTelemetryItem> };
        if (!stop) setT(d?.cameras?.[cameraId] ?? null);
      } catch {
        if (!stop) setT(null);
      }
    }
    go();
    const id = setInterval(go, 8000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [cameraId]);

  if (t === undefined) return null; // 최초 로딩 중엔 미표시(레이아웃 튐 방지)
  if (!t) {
    return (
      <span className="text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
        텔레메트리 없음 (탐지 데몬 미연결)
      </span>
    );
  }
  const stale = Boolean(t.is_stale) || !t.connected;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <Badge variant={t.connected ? "default" : "outline"}>{t.connected ? "연결" : "끊김"}</Badge>
      {typeof t.fps === "number" && (
        <span className="text-muted-foreground tabular-nums">{t.fps.toFixed(1)} FPS</span>
      )}
      {typeof t.dropped_frames === "number" && t.dropped_frames > 0 && (
        <span className="text-muted-foreground">드롭 {t.dropped_frames}</span>
      )}
      {stale && <Badge variant="destructive">지연</Badge>}
    </div>
  );
}
