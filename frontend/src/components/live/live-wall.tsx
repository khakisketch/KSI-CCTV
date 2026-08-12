"use client";

import { AlertTriangle, ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CameraOperationalStatus } from "@/components/camera-operational-status";
import { IncidentStatusBadge } from "@/components/incident-actions";
import { LiveTile } from "@/components/live/live-tile";
import { LiveToolbar } from "@/components/live/live-toolbar";
import { Badge } from "@/components/ui/badge";
import {
  useLivePresets,
  type LiveLayout,
  type LiveSort,
} from "@/components/live/use-live-presets";
import { useLiveUpdates } from "@/components/live/use-live-updates";
import type { CameraItem, CameraTelemetryItem, EventItem, IncidentItem, TelemetryStatus } from "@/lib/engine";
import { isCameraReceiving } from "@/lib/engine";
import { cn } from "@/lib/utils";

interface LiveWallProps {
  site: string | null;       // 설치 사이트 이름 — 현장별 preset 저장 키
  cameras: CameraItem[];
  telemetry: Record<string, CameraTelemetryItem>;
  latestEvents: Record<string, EventItem | undefined>; // 카메라 id별 최근 고위험 이벤트
  telemetryStatus?: TelemetryStatus;
  telemetryGeneratedAt?: string;
  initialFocusCameraId?: string | null;
  incidentEventId?: string;
  incident?: IncidentItem;
  incidentEvent?: EventItem | null;
}

const GRID_CLASS: Record<LiveLayout, string> = {
  1: "grid-cols-1 grid-rows-1",
  4: "grid-cols-2 grid-rows-2",
  9: "grid-cols-2 grid-rows-3 sm:grid-cols-3",
  16: "grid-cols-2 grid-rows-4 sm:grid-cols-4",
};

const TILE_DENSITY_COMPACT_FROM = 9; // 9분할 이상은 compactDensity=true
const MAX_AUTO_PLAY_DEFAULT = 4;

// 라이브 월(Video Wall) 클라이언트 오케스트레이터.
//   - 상시 모니터 가정: layout(1/4/9/16), sort, autoPlayCount는 localStorage(siteKey)에 저장
//   - 자동 재생 슬롯은 maxAutoPlayCount(기본 4)로 제한 — 관제 모니터 대역폭 보호
    //   - 우선순위: 미해결 위험 이벤트 > 정상 수신 > 이름
//   - 포커스 모드: 타일 클릭 시 1칸 확대, ESC로 복귀
//   - 전체화면(관제 모드) Fullscreen API + body 데이터 속성으로 사이드바/헤더 숨김
export function LiveWall({
  site,
  cameras,
  telemetry,
  latestEvents,
  telemetryStatus,
  telemetryGeneratedAt,
  initialFocusCameraId = null,
  incidentEventId,
  incident,
  incidentEvent,
}: LiveWallProps) {
  const { presets, setPresets, hydrated } = useLivePresets(site);

  // 실시간 갱신: prop으로 들어온 초기 데이터를 useLiveUpdates 훅에 시드로 전달하고,
  // 이후 telemetry 폴링(10s) + events SSE(alert)로 자동 갱신된 값을 사용.
  const {
    telemetry: liveTelemetry,
    latestEvents: liveLatestEvents,
    telemetrySource,
  } = useLiveUpdates(
    telemetry,
    latestEvents,
    { status: telemetryStatus, generatedAt: telemetryGeneratedAt },
  );

  // ── 정렬 ──────────────────────────────────────────────────────────────
  const sortedCameras = useMemo(
    () => sortCameras(cameras, liveTelemetry, liveLatestEvents, presets.sort, presets.order),
    [cameras, liveTelemetry, liveLatestEvents, presets.sort, presets.order],
  );

  // 표시 대상: 포커스 모드면 1대만, 아니면 레이아웃 용량만큼 슬라이스(페이지 적용).
  // 4분할이지만 12대 카메라가 있을 때 4개만 보여주고 나머지는 다음 페이지로 넘긴다.
  const [focusCameraId, setFocusCameraId] = useState<string | null>(initialFocusCameraId);
  const [page, setPage] = useState(0);
  const layoutCapacity = focusCameraId ? 1 : presets.layout;
  const pageCount = Math.max(1, Math.ceil(sortedCameras.length / layoutCapacity));
  const [prevPartitionKey, setPrevPartitionKey] = useState("");
  const partitionKey = `${presets.layout}|${presets.sort}|${sortedCameras.length}`;
  // 정렬/레이아웃/카메라 개수가 바뀌면 페이지 관점이 바뀌므로 1페이지로 되돌린다.
  // setState-during-render는 React가 권장하는 "Adjusting state when a prop changes" 패턴.
  if (prevPartitionKey !== partitionKey) {
    setPrevPartitionKey(partitionKey);
    setPage(0);
  }
  const safePage = Math.min(page, pageCount - 1);

  const displayedCameras =
    focusCameraId
      ? sortedCameras.filter((c) => c.id === focusCameraId)
      : sortedCameras.slice(safePage * layoutCapacity, safePage * layoutCapacity + layoutCapacity);

  // ── Drag-to-reorder ─────────────────────────────────────────────────────
  // sort=default일 때만 타일 드래그로 순서를 재지정할 수 있다. 자동 정렬 모드에서는
  // 사용자가 드래그해도 다음 갱신 때 다시 정렬되므로 의미가 없다.
  const canDrag = !focusCameraId && presets.sort === "default" && sortedCameras.length > 1;
  const [dragFromIndex, setDragFromIndex] = useState<number | null>(null);
  const [dragToIndex, setDragToIndex] = useState<number | null>(null);
  const [reorderAnnouncement, setReorderAnnouncement] = useState("");
  const persistOrder = useCallback(
    (nextOrder: string[]) => setPresets({ order: nextOrder }),
    [setPresets],
  );
  const commitReorder = useCallback(
    (from: number, to: number) => {
      if (from === to) return;
      const ids = displayedCameras.map((c) => c.id!).filter(Boolean);
      if (from < 0 || from >= ids.length || to < 0 || to >= ids.length) return;
      // ids 배열에서 from을 to 위치로 옮긴 새 순서를 계산한다.
      const moved = ids[from];
      const next = [...ids];
      next.splice(from, 1);
      next.splice(to, 0, moved);
      persistOrder(next);
      setReorderAnnouncement(`${displayedCameras[from]?.name ?? moved} 카메라를 ${to + 1}번 위치로 이동했습니다.`);
    },
    [displayedCameras, persistOrder],
  );

  // ── 활성 재생 슬롯 ──────────────────────────────────────────────────────
  // 자동 배정(위험도/수신 우선 상위 N) + 사용자 수동 override.
  // manualActive는 Map 대신 순서 배열(LRU)로 보관 — cap 초과 시 가장 오래된 수동
  // 스트림을 밀어내 대역폭 보호 계약을 유지한다.
  // manualOrder: 사용자가 직접 켠 id의 최근 추가 순서(맨 앞이 최신).
  const [manualActiveRaw, setManualActiveRaw] = useState<Record<string, boolean>>({});
  const [manualOrder, setManualOrder] = useState<string[]>([]);
  // 표시 영역 밖으로 나간 타일의 수동 상태를 매 렌더마다 정리(derived, effect-free).
  const visibleIdSet = useMemo(
    () => new Set(displayedCameras.map((c) => c.id!)),
    [displayedCameras],
  );
  const manualActive = useMemo(() => {
    const next: Record<string, boolean> = {};
    for (const [id, on] of Object.entries(manualActiveRaw)) {
      if (visibleIdSet.has(id)) next[id] = on;
    }
    return next;
  }, [manualActiveRaw, visibleIdSet]);
  const manualOnIds = useMemo(
    () => manualOrder.filter((id) => manualActive[id] === true),
    [manualOrder, manualActive],
  );

  // 스트림 cap: 자동 배정 개수 + 사용자 수동 on 개수의 합으로 산출되는 실제 동시
  // 재생 수가 MAX_AUTO_PLAY_DEFAULT를 넘을 수 없다. 자동 배정은 남은 슬롯만 사용.
  const maxAutoPlayCount = Math.max(
    0,
    Math.min(MAX_AUTO_PLAY_DEFAULT, presets.autoPlayCount) - manualOnIds.length,
  );

  const autoActiveIds = useMemo(() => {
    const ordered = sortedCameras.map((c) => c.id!).filter(Boolean);
    // 수동으로 끈 타일은 자동 배정에서 제외. 수동으로 켜진 타일은 이미 활성이므로 제외.
    const manualOnSet = new Set(manualOnIds);
    const available = ordered.filter((id) => manualActive[id] !== false && !manualOnSet.has(id));
    return available.slice(0, maxAutoPlayCount);
  }, [sortedCameras, maxAutoPlayCount, manualActive, manualOnIds]);

  const activeSet = useMemo(() => {
    const s = new Set<string>(autoActiveIds);
    for (const id of manualOnIds) s.add(id);
    return s;
  }, [autoActiveIds, manualOnIds]);

  const isTileActive = useCallback(
    (id: string): boolean => {
      // 포커스 모드에서는 포커스 타일만 재생(과도한 동시 재생 방지).
      if (focusCameraId) return focusCameraId === id;
      return activeSet.has(id);
    },
    [activeSet, focusCameraId],
  );

  // 사용자가 타일을 on/off 할 때:
  //   - on: cap 초과 시 밀려날 가장 오래된 수동 on을 자동으로 끈 뒤 새 id를 켠다.
  //   - off: manualOrder에서 제거. auto는 다음 렌더에서 남은 슬롯을 자동 채운다.
  // 렌더-시점 manualActive/manualOrder 상태를 closure로 읽지 않기 위해 빈 deps와
  // functional updater를 함께 사용한다.
  const setTileActive = useCallback(
    (id: string, on: boolean) => {
      if (on) {
        // 두 state를 동시에 갱신하기 위해 batched transition을 쓰지 않고도
        // 각각 functional updater로 다음 상태를 순수하게 계산한다.
        // manualActive: 먼저 id를 true로, 피해자(가장 오래된 수동 on)를 false로.
        setManualActiveRaw((prevRaw) => {
          const activeManualOnes = manualOrder.filter((oid) => prevRaw[oid] === true && oid !== id);
          const overflow = Math.max(0, activeManualOnes.length + 1 - MAX_AUTO_PLAY_DEFAULT);
          const victims = new Set(activeManualOnes.slice(-overflow));
          const nextRaw = { ...prevRaw, [id]: true };
          for (const vid of victims) nextRaw[vid] = false;
          // manualOrder: 피해자를 제거하고 id를 맨 앞에(최신) 둔다.
          setManualOrder((prevOrder) => {
            const keptOrder = prevOrder.filter((oid) => !victims.has(oid) && oid !== id);
            return [id, ...keptOrder];
          });
          return nextRaw;
        });
      } else {
        setManualActiveRaw((prev) => ({ ...prev, [id]: false }));
        setManualOrder((prev) => prev.filter((oid) => oid !== id));
      }
    },
    [manualOrder],
  );

  // ── 포커스/ESC ────────────────────────────────────────────────────────
  const handleFocus = useCallback((id: string) => {
    setFocusCameraId((current) => (current === id ? null : id));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      else setFocusCameraId((current) => (current ? null : current));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ── 전체화면(관제 모드) ───────────────────────────────────────────────
  const wallRef = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const toggleFullscreen = useCallback(async () => {
    const el = wallRef.current;
    if (!el) return;
    try {
      if (!document.fullscreenElement) await el.requestFullscreen();
      else await document.exitFullscreen();
    } catch {
      // 권한/iframe 차단 — 시각 모드만 유지
    }
  }, []);

  useEffect(() => {
    const wall = wallRef.current;
    const handler = () => {
      const isFs = Boolean(document.fullscreenElement);
      setFullscreen(isFs);
      if (typeof document !== "undefined") {
        document.body.dataset.wallFullscreen = isFs ? "true" : "";
      }
    };
    document.addEventListener("fullscreenchange", handler);
    return () => {
      document.removeEventListener("fullscreenchange", handler);
      delete document.body.dataset.wallFullscreen;
      if (document.fullscreenElement === wall) void document.exitFullscreen().catch(() => undefined);
    };
  }, []);

  // ── preset 갱신 ───────────────────────────────────────────────────────
  const handleLayoutChange = useCallback((layout: LiveLayout) => setPresets({ layout }), [setPresets]);
  const handleSortChange = useCallback((sort: LiveSort) => setPresets({ sort }), [setPresets]);
  const handleAutoPlayCountChange = useCallback(
    (autoPlayCount: number) => setPresets({ autoPlayCount }),
    [setPresets],
  );

  // hydration 전에는 SSR-safe 회색 화면(깜빡임 방지)
  if (!hydrated) {
    return (
      <div className="space-y-3">
        <LiveToolbar
          layout={4}
          onLayoutChange={() => {}}
          sort="risk"
          onSortChange={() => {}}
          autoPlayCount={4}
          onAutoPlayCountChange={() => {}}
          cameraCount={cameras.length}
          maxAutoPlay={MAX_AUTO_PLAY_DEFAULT}
          fullscreen={false}
          onToggleFullscreen={() => {}}
          focusCameraId={null}
          onExitFocus={() => {}}
          hasCustomOrder={false}
          onResetOrder={() => {}}
          page={0}
          pageCount={1}
          onPageChange={() => {}}
        />
        <div className="grid aspect-video grid-cols-2 grid-rows-2 place-items-center gap-2 rounded-lg border border-dashed text-sm text-muted-foreground">
          관제 월 준비 중…
        </div>
      </div>
    );
  }

  const compactDensity = !focusCameraId && presets.layout >= TILE_DENSITY_COMPACT_FROM;
  const incidentCamera = incident?.camera_id ? cameras.find((camera) => camera.id === incident.camera_id) : undefined;
  const incidentSummary = incidentEvent?.description ?? incidentEvent?.event_type ?? incident?.source_event_type ?? "사건 정보를 불러오지 못했습니다.";

  return (
    <div ref={wallRef} className="flex h-full flex-col gap-3">
      <p className="sr-only" role="status" aria-live="polite">{reorderAnnouncement}</p>
      <LiveToolbar
        layout={presets.layout}
        onLayoutChange={handleLayoutChange}
        sort={presets.sort}
        onSortChange={handleSortChange}
        autoPlayCount={presets.autoPlayCount}
        onAutoPlayCountChange={handleAutoPlayCountChange}
        cameraCount={sortedCameras.length}
        maxAutoPlay={MAX_AUTO_PLAY_DEFAULT}
        fullscreen={fullscreen}
        onToggleFullscreen={toggleFullscreen}
        focusCameraId={focusCameraId}
        onExitFocus={() => setFocusCameraId(null)}
        hasCustomOrder={presets.order.length > 0}
        onResetOrder={() => setPresets({ order: [] })}
        page={safePage}
        pageCount={pageCount}
        onPageChange={setPage}
      />

      {incidentEventId && (
        <section aria-label="현재 확인 중인 사건" className="grid gap-4 rounded-lg border border-primary/30 bg-primary/5 p-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div>
            <div className="flex flex-wrap items-center gap-2"><AlertTriangle className="size-4 text-primary" aria-hidden="true" /><p className="text-sm font-semibold">사건 확인 중</p>{incident && <IncidentStatusBadge status={incident.status} />}{incidentEvent?.alert_level && <Badge variant={incidentEvent.alert_level === "critical" || incidentEvent.alert_level === "high" ? "destructive" : "outline"}>{incidentEvent.alert_level}</Badge>}</div>
            <p className="mt-2 text-sm text-muted-foreground">{incidentSummary}</p>
            <Link className="mt-3 inline-flex min-h-11 items-center gap-1.5 text-sm font-medium text-primary underline-offset-4 hover:underline" href={`/incidents/${encodeURIComponent(incidentEventId)}`}><ArrowLeft className="size-4" aria-hidden="true" /> 사건 상세로 돌아가기</Link>
          </div>
          <CameraOperationalStatus camera={incidentCamera} telemetry={incident?.camera_id ? liveTelemetry[incident.camera_id] : undefined} className="sm:grid-cols-3 lg:grid-cols-1" />
        </section>
      )}
      {telemetrySource.stale && (
        <div
          role="status"
          className={cn(
            "rounded-md border px-3 py-2 text-xs",
            telemetrySource.status === "error"
              ? "border-destructive/50 bg-destructive/10 text-destructive"
              : "border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-300",
          )}
        >
          {telemetrySource.status === "error"
            ? "텔레메트리를 가져오지 못했습니다. 마지막 카메라 상태는 최신 정보가 아닐 수 있습니다."
            : telemetrySource.status === "unavailable"
              ? "텔레메트리 소스를 사용할 수 없습니다. 엔진과 분석 파이프라인 상태를 확인하세요."
              : "텔레메트리 갱신이 지연되고 있습니다. 마지막 카메라 상태는 최신 정보가 아닐 수 있습니다."}
        </div>
      )}

      {sortedCameras.length === 0 ? (
        <div className="grid h-full place-items-center rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
          재생할 운영 카메라가 없습니다. 카메라를 등록하고 활성화하세요.
        </div>
      ) : (
        <div
          className={cn(
            "grid gap-2",
            focusCameraId ? "grid-cols-1 grid-rows-1" : GRID_CLASS[presets.layout],
            fullscreen && "min-h-0 flex-1",
          )}
        >
          {displayedCameras.map((camera, index) => {
            const id = camera.id!;
            return (
              <LiveTile
                key={id}
                camera={camera}
                telemetry={liveTelemetry[id]}
                latestEvent={liveLatestEvents[id]}
                active={isTileActive(id)}
                setActive={(v) => setTileActive(id, v)}
                focus={Boolean(focusCameraId)}
                compactDensity={compactDensity}
                onFocus={handleFocus}
                draggable={canDrag}
                index={index}
                onDragStart={(from) => setDragFromIndex(from)}
                onDragEnter={(to) => setDragToIndex(to)}
                onDragEnd={() => {
                  if (dragFromIndex !== null && dragToIndex !== null) {
                    commitReorder(dragFromIndex, dragToIndex);
                  }
                  setDragFromIndex(null);
                  setDragToIndex(null);
                }}
                onDrop={(to) => {
                  // drop 위치가 확정된 경우 즉시 커밋(dragend 없이 drop만 발생할 수도 있음).
                  if (dragFromIndex !== null && dragFromIndex !== to) {
                    commitReorder(dragFromIndex, to);
                  }
                  setDragFromIndex(null);
                  setDragToIndex(null);
                }}
                canMovePrev={index > 0}
                canMoveNext={index < displayedCameras.length - 1}
                onMove={(direction) => {
                  const from = index;
                  const to = from + direction;
                  if (to < 0 || to >= displayedCameras.length) return;
                  commitReorder(from, to);
                }}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── 정렬 헬퍼 ──────────────────────────────────────────────────────────
// 사용자가 지정한 order가 있으면 그 id를 먼저 두고, 나머지는 sort 기준으로 정렬한다.
function sortCameras(
  list: CameraItem[],
  tele: Record<string, CameraTelemetryItem>,
  events: Record<string, EventItem | undefined>,
  sort: LiveSort,
  order: string[],
): CameraItem[] {
  const byId = new Map(list.map((c) => [c.id!, c]));
  const ordered = order.map((id) => byId.get(id)).filter((c): c is CameraItem => Boolean(c));
  const remaining = list.filter((c) => !order.includes(c.id!));

  const sortFn = (a: CameraItem, b: CameraItem): number => {
    const idA = a.id!, idB = b.id!;
    const isHigh = (c: string) => {
      const e = events[c];
      return Boolean(
        e &&
          (e.alert_level === "critical" || e.alert_level === "high") &&
          !["likely_false_positive", "resolved_false_positive", "suppressed"].includes(e.alert_state ?? ""),
      );
    };
    const recvA = isCameraReceiving(tele[idA]);
    const recvB = isCameraReceiving(tele[idB]);
    switch (sort) {
      case "risk": {
        const scoreA = riskScore(isHigh(idA), recvA);
        const scoreB = riskScore(isHigh(idB), recvB);
        if (scoreA !== scoreB) return scoreB - scoreA;
        return (a.name ?? idA).localeCompare(b.name ?? idB, "ko");
      }
      case "healthy":
        if (recvA !== recvB) return recvA ? -1 : 1;
        return (a.name ?? idA).localeCompare(b.name ?? idB, "ko");
      case "name":
        return (a.name ?? idA).localeCompare(b.name ?? idB, "ko");
      case "default":
      default:
        return 0;
    }
  };
  remaining.sort(sortFn);
  return [...ordered, ...remaining];
}

function riskScore(high: boolean, receiving: boolean): number {
  if (high) return 2;
  if (receiving) return 1;
  return 0;
}
