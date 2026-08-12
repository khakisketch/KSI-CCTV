"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  isCameraReceiving,
  type CameraTelemetryItem,
  type CameraTelemetryResponse,
  type TelemetryStatus,
} from "@/lib/engine";

interface TelemetryContextValue {
  cameras: Record<string, CameraTelemetryItem> | null;
  status: TelemetryStatus;
  stale: boolean;
}

const TelemetryContext = createContext<TelemetryContextValue | undefined>(undefined);
const POLL_MS = 8_000;
const SOURCE_STALE_MS = 30_000;

export function CameraTelemetryProvider({ children }: { children: React.ReactNode }) {
  const [value, setValue] = useState<TelemetryContextValue | undefined>(undefined);
  const inFlight = useRef(false);

  useEffect(() => {
    let mounted = true;
    let controller: AbortController | null = null;
    let lastSuccessAt: number | null = null;
    async function refresh() {
      if (inFlight.current) return;
      inFlight.current = true;
      controller = new AbortController();
      try {
        const response = await fetch("/api/cameras/telemetry", {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!mounted) return;
        if (!response.ok) {
          setValue((prev) => ({
            cameras: prev?.cameras ?? null,
            status: "error",
            stale: true,
          }));
          return;
        }
        const data = (await response.json()) as CameraTelemetryResponse;
        const status = data.telemetry_status ?? (data.connected ? "ok" : "unavailable");
        if (status === "ok" && data.cameras && typeof data.cameras === "object") {
          lastSuccessAt = Date.now();
          setValue({ cameras: data.cameras, status, stale: false });
        } else {
          setValue((prev) => ({
            cameras: prev?.cameras ?? null,
            status,
            stale: true,
          }));
        }
      } catch (error) {
        if (mounted && (error as { name?: string } | null)?.name !== "AbortError") {
          setValue((prev) => ({
            cameras: prev?.cameras ?? null,
            status: "error",
            stale: true,
          }));
        }
      } finally {
        inFlight.current = false;
      }
    }
    void refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    const staleTimer = window.setInterval(() => {
      if (!mounted || !lastSuccessAt || Date.now() - lastSuccessAt <= SOURCE_STALE_MS) return;
      setValue((prev) => prev && !prev.stale ? { ...prev, stale: true } : prev);
    }, 5_000);
    return () => {
      mounted = false;
      controller?.abort();
      window.clearInterval(timer);
      window.clearInterval(staleTimer);
    };
  }, []);

  return <TelemetryContext value={value}>{children}</TelemetryContext>;
}

export function useCameraTelemetry() {
  return useContext(TelemetryContext);
}

// 카메라별 라이브 텔레메트리(연결/FPS/드롭/지연). API source freshness와 개별
// 카메라 frame freshness를 분리해, polling 실패를 "정상 수신"으로 오인하지 않는다.
export function CameraTelemetry({ cameraId }: { cameraId: string }) {
  const source = useCameraTelemetry();
  if (source === undefined) return null; // 최초 로딩 중엔 미표시(레이아웃 튐 방지)

  const t = source.cameras?.[cameraId] ?? null;
  if (!t) {
    const message = source.status === "error"
      ? "텔레메트리 갱신 실패"
      : "텔레메트리 없음 (탐지 데몬 미연결)";
    return (
      <span className="text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
        {message}
      </span>
    );
  }
  const cameraStale = t.health_state === "stale" || t.health_state === "disconnected" || Boolean(t.is_stale);
  const healthy = isCameraReceiving(t) && !source.stale;
  const state = source.stale
    ? "상태 갱신 지연"
    : t.health_state === "initializing"
      ? "첫 프레임 대기"
      : t.health_state === "disconnected"
        ? "연결 끊김"
        : t.health_state === "stale"
          ? "영상 지연"
          : healthy
            ? "정상 수신"
            : !t.connected
              ? "연결 끊김"
              : "첫 프레임 대기";
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md bg-muted/50 px-2 py-1.5 text-xs">
      <Badge variant={healthy ? "success" : cameraStale || source.stale ? "destructive" : "warning"}>{state}</Badge>
      {t.tamper_state === "tampered" && <Badge variant="destructive">화면 조작 의심</Badge>}
      {t.tamper_state === "suspected" && <Badge variant="outline">화면 상태 확인 중</Badge>}
      {typeof t.fps === "number" && (
        <span className="text-muted-foreground tabular-nums">{t.fps.toFixed(1)} FPS</span>
      )}
      {typeof t.dropped_frames === "number" && t.dropped_frames > 0 && (
        <span className="text-muted-foreground">드롭 {t.dropped_frames}</span>
      )}
      {typeof t.tracking?.active_tracks === "number" && t.tracking.active_tracks > 0 && (
        <span className="text-muted-foreground">Track {t.tracking.active_tracks}</span>
      )}
      {typeof t.tracking?.loitering?.active_episodes === "number" && t.tracking.loitering.active_episodes > 0 && (
        <span className="text-muted-foreground">체류 관찰 {t.tracking.loitering.active_episodes}</span>
      )}
      {(t.tracking?.runtime_errors ?? 0) + (t.tracking?.errors ?? 0) > 0 && (
        <Badge variant="destructive">Tracking 오류</Badge>
      )}
      {typeof t.seconds_since_last_frame === "number" && (
        <span className="text-muted-foreground tabular-nums">최근 프레임 {t.seconds_since_last_frame.toFixed(1)}초 전</span>
      )}
    </div>
  );
}
