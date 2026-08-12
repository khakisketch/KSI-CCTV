"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  CameraTelemetryItem,
  CameraTelemetryResponse,
  EventItem,
  IncidentItem,
  TelemetryStatus,
} from "@/lib/engine";
import { getLatestActiveCameraEvents } from "@/lib/live-risk";

const TELEMETRY_POLL_MS = 10_000;
const TELEMETRY_STALE_MS = 30_000;
const INCIDENT_RECONCILE_MS = 10_000;

export interface TelemetrySourceState {
  status: TelemetryStatus;
  generatedAt?: string;
  // 마지막 성공 response를 브라우저가 받은 시각. 서버 generated_at과 카메라 frame 시각을
  // 혼동하지 않도록 별도로 유지한다.
  lastSuccessAt: number | null;
  stale: boolean;
}

interface InitialTelemetrySource {
  status?: TelemetryStatus;
  generatedAt?: string;
}

// 라이브 월용 실시간 데이터 갱신 훅.
//   - telemetry: 10초 polling + source freshness 상태
//   - incident highlights: browser EventSource trigger + unresolved incident reconciliation
// 초기값은 SSR 페이지에서 주입받아 깜빡임 없이 출발한다.
export function useLiveUpdates(
  initialTelemetry: Record<string, CameraTelemetryItem>,
  initialLatestEvents: Record<string, EventItem | undefined>,
  initialSource: InitialTelemetrySource = {},
) {
  const [telemetry, setTelemetry] = useState(initialTelemetry);
  const [latestEvents, setLatestEvents] = useState(initialLatestEvents);
  const [telemetrySource, setTelemetrySource] = useState<TelemetrySourceState>(() => {
    const status = initialSource.status ?? "unavailable";
    return {
      status,
      generatedAt: initialSource.generatedAt,
      lastSuccessAt: status === "ok" ? Date.now() : null,
      stale: status !== "ok",
    };
  });

  const highlightRefreshInFlight = useRef(false);
  const reconcileHighlights = useCallback(async (signal?: AbortSignal) => {
    if (highlightRefreshInFlight.current) return;
    highlightRefreshInFlight.current = true;
    try {
      const response = await fetch("/api/incidents?unresolved=true&limit=1000", { cache: "no-store", signal });
      if (!response.ok) return;
      const data = (await response.json()) as { available?: boolean; incidents?: IncidentItem[] };
      if (data.available !== true || !Array.isArray(data.incidents)) {
        setLatestEvents({});
        return;
      }
      setLatestEvents(getLatestActiveCameraEvents(data.incidents));
    } catch (error) {
      if ((error as { name?: string } | null)?.name !== "AbortError") {
        // 마지막 정상 highlight를 유지하고 다음 SSE/poll refresh에서 다시 보정한다.
      }
    } finally {
      highlightRefreshInFlight.current = false;
    }
  }, []);

  // Telemetry polling은 single-flight로 실행한다. 네트워크가 느릴 때 setInterval 요청이
  // 중첩되어 오래된 응답이 최신 상태를 덮는 문제를 막는다.
  const telemetryInFlight = useRef(false);
  useEffect(() => {
    let mounted = true;
    let currentController: AbortController | null = null;
    const tick = async () => {
      if (telemetryInFlight.current) return;
      telemetryInFlight.current = true;
      currentController = new AbortController();
      try {
        const response = await fetch("/api/cameras/telemetry", {
          cache: "no-store",
          signal: currentController.signal,
        });
        if (!mounted) return;
        if (!response.ok) {
          setTelemetrySource((prev) => ({ ...prev, status: "error", stale: true }));
          return;
        }
        const data = (await response.json()) as CameraTelemetryResponse;
        if (!mounted) return;
        const status = data.telemetry_status ?? (data.connected ? "ok" : "unavailable");
        const receivedAt = Date.now();
        if (status === "ok" && data.cameras && typeof data.cameras === "object") {
          setTelemetry(data.cameras);
          setTelemetrySource({
            status,
            generatedAt: data.generated_at,
            lastSuccessAt: receivedAt,
            stale: false,
          });
        } else {
          // 마지막 known camera telemetry는 유지하되 source-level fault를 분명히 표시한다.
          setTelemetrySource((prev) => ({
            ...prev,
            status,
            generatedAt: data.generated_at ?? prev.generatedAt,
            stale: true,
          }));
        }
      } catch (error) {
        if (mounted && (error as { name?: string } | null)?.name !== "AbortError") {
          setTelemetrySource((prev) => ({ ...prev, status: "error", stale: true }));
        }
      } finally {
        telemetryInFlight.current = false;
      }
    };
    void tick();
    const pollId = window.setInterval(tick, TELEMETRY_POLL_MS);
    const staleId = window.setInterval(() => {
      setTelemetrySource((prev) => {
        const stale = prev.status !== "ok" || prev.lastSuccessAt === null || Date.now() - prev.lastSuccessAt > TELEMETRY_STALE_MS;
        return stale === prev.stale ? prev : { ...prev, stale };
      });
    }, 5_000);
    return () => {
      mounted = false;
      currentController?.abort();
      window.clearInterval(pollId);
      window.clearInterval(staleId);
    };
  }, []);

  // Incident lifecycle은 event stream과 별도이므로 주기적으로 미종결 목록을 재조합해
  // 다른 화면에서 종결된 사건도 라이브 월 위험 강조에서 제거한다.
  useEffect(() => {
    const controller = new AbortController();
    const pollId = window.setInterval(() => void reconcileHighlights(), INCIDENT_RECONCILE_MS);
    return () => {
      controller.abort();
      window.clearInterval(pollId);
    };
  }, [reconcileHighlights]);

  // EventSource 자체 재연결을 사용한다. 새 EventSource를 수동 생성하면 브라우저가 보관한
  // Last-Event-ID를 잃어버리므로 onerror에서 close/recreate하지 않는다.
  useEffect(() => {
    const controller = new AbortController();
    const es = new EventSource("/api/events/stream");
    es.addEventListener("alert", () => void reconcileHighlights(controller.signal));
    es.addEventListener("reconciliation-required", () => {
      void reconcileHighlights(controller.signal);
    });
    return () => {
      controller.abort();
      es.close();
    };
  }, [reconcileHighlights]);

  return { telemetry, latestEvents, telemetrySource } as const;
}
