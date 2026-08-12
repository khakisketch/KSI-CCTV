"use client";

import Image from "next/image";
import { useEffect, useState } from "react";

import { CameraLive } from "@/components/camera-live";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { CameraItem, CameraTelemetryItem, EventItem } from "@/lib/engine";
import { getVerificationPresentation } from "@/lib/event-verification";
import { isCameraReceiving } from "@/lib/engine";
import { cn } from "@/lib/utils";

interface LiveTileProps {
  camera: CameraItem;
  telemetry?: CameraTelemetryItem;
  latestEvent?: EventItem;
  active: boolean;            // wall가 결정하는 자동재생 슬롯 배정 여부
  setActive: (v: boolean) => void; // wall가 소유한 on/off 상태 setter
  focus: boolean;             // 1칸 확대 중인지
  compactDensity: boolean;    // 9/16분할처럼 작은 타일 — 메타데이터 최소화
  onFocus: (id: string) => void;
  draggable?: boolean;        // drag-to-reorder 가능 여부(default=false)
  index?: number;             // 현재 타일 인덱스(DnD/재배열용)
  onDragStart?: (index: number) => void;
  onDragEnter?: (index: number) => void;
  onDragEnd?: () => void;
  onDrop?: (index: number) => void;
  // 키보드/터치 접근성 보조 컨트롤: sort=default 모드에서만 노출.
  // wall는 이동이 가능할 때만 canReorder=true로 준다(index가 첫/끝이면 한쪽 방향 숨김).
  canMovePrev?: boolean;
  canMoveNext?: boolean;
  onMove?: (direction: -1 | 1) => void;
}

// 라이브 월의 단일 타일. active 상태에서만 CameraLive(=CameraPlayer+HLS)를 마운트하고,
// idle 상태에서는 최근 스냅샷만 보여 0 대역폭을 유지한다.
export function LiveTile({
  camera,
  telemetry,
  latestEvent,
  active,
  setActive,
  focus,
  compactDensity,
  onFocus,
  draggable = false,
  index,
  onDragStart,
  onDragEnter,
  onDragEnd,
  onDrop,
  canMovePrev = false,
  canMoveNext = false,
  onMove,
}: LiveTileProps) {
  const id = camera.id!;
  const healthy = isCameraReceiving(telemetry);
  const receiveState = cameraReceiveState(telemetry, healthy);
  const hasHighRisk = Boolean(
    latestEvent &&
      (latestEvent.alert_level === "critical" || latestEvent.alert_level === "high") &&
      !isDeemphasizedAlert(latestEvent),
  );
  const verification = latestEvent ? getVerificationPresentation(latestEvent) : null;
  const trackingAvailable = (camera.enabled_events ?? []).some(
    (event) => event === "line_crossing" || event === "loitering",
  );

  // idle 상태일 때 8초마다 최신 스냅샷 갱신(CameraOverview 패턴과 동일).
  const [revision, setRevision] = useState(() => Date.now());
  const [snapshotFailed, setSnapshotFailed] = useState(false);
  useEffect(() => {
    if (active) return;
    const timer = setInterval(() => {
      setSnapshotFailed(false);
      setRevision(Date.now());
    }, 8000);
    return () => clearInterval(timer);
  }, [active]);

  const fps = telemetry?.fps;
  const fpsLabel = typeof fps === "number" ? `${fps.toFixed(1)} FPS` : null;
  const lastFrame = telemetry?.seconds_since_last_frame;
  const lastFrameLabel = typeof lastFrame === "number" ? `최근 프레임 ${lastFrame.toFixed(1)}초 전` : null;

  // DnD 중인 자신 타일의 흐림 피드백 — wall이 넘겨준 onDragStart 시점에만 활성.
  const [selfDragging, setSelfDragging] = useState(false);
  const draggingId = selfDragging ? id : null;

  return (
    <div
      draggable={draggable}
      onDragStart={(e) => {
        if (!draggable || index === undefined) return;
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", String(index));
        setSelfDragging(true);
        onDragStart?.(index);
      }}
      onDragEnter={(e) => {
        if (!draggable || index === undefined) return;
        e.preventDefault();
        onDragEnter?.(index);
      }}
      onDragOver={(e) => {
        if (!draggable) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
      }}
      onDrop={(e) => {
        if (!draggable || index === undefined) return;
        e.preventDefault();
        onDrop?.(index);
      }}
      onDragEnd={() => {
        if (!draggable) return;
        setSelfDragging(false);
        onDragEnd?.();
      }}
      className={cn(
        "group relative flex aspect-video w-full flex-col overflow-hidden rounded-md bg-black ring-1 ring-white/10 transition",
        // 고위험 이벤트가 있으면 빨강 테두리/빛으로 시선 유도
        hasHighRisk ? "ring-2 ring-red-500/80 shadow-[0_0_0_2px_rgb(239_68_68_/_0.35)]" : "",
        focus ? "ring-2 ring-primary" : "",
        draggable ? "cursor-grab active:cursor-grabbing" : "",
        draggingId === id ? "opacity-40 ring-2 ring-primary/60" : "",
      )}
    >
      {/* 영상/스냅샷 영역 */}
      {active ? (
        <div className="relative aspect-video w-full flex-1">
          <CameraLive
            cameraId={id}
            enabled
            trackingEnabled={trackingAvailable}
            controlledOn={active}
            hideChrome                 // wall가 자체 컨트롤을 렌더
            initialOn
          />
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setActive(true)}
          className="relative aspect-video w-full flex-1 cursor-pointer overflow-hidden bg-black"
          aria-label={`${camera.name ?? id} 라이브 보기`}
        >
          {!snapshotFailed ? (
            <Image
              unoptimized
              fill
              sizes="(max-width: 768px) 100vw, 50vw"
              src={`/api/cameras/${encodeURIComponent(id)}/snapshot?t=${revision}`}
              alt={`${camera.name ?? id} 최근 프레임`}
              className="object-cover opacity-90 transition group-hover:opacity-100"
              onError={() => setSnapshotFailed(true)}
            />
          ) : (
            <div className="absolute inset-0 grid place-items-center bg-muted text-xs text-muted-foreground">
              현재 프레임을 불러올 수 없습니다
            </div>
          )}
          <div className="absolute inset-0 grid place-items-center bg-black/30 opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100">
            <span className="inline-flex items-center rounded-full bg-black/80 px-3 py-1 text-xs text-white ring-1 ring-white/20">
              ▶ 라이브 보기
            </span>
          </div>
        </button>
      )}

      {/* 상단 오버레이: 수신 상태 + FPS + 이벤트 배지 */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-2 p-2">
        <div className="flex items-center gap-1.5 rounded bg-black/65 px-2 py-0.5 text-[0.7rem] text-white backdrop-blur-sm">
          <span className={cn("size-2 rounded-full", receiveState.dotClass)} />
          {receiveState.label}
        </div>
        <div className="flex items-center gap-1.5">
          {hasHighRisk && (
            <Badge variant="destructive" className="px-1.5 py-0.5 text-[0.7rem]">
              {latestEvent?.alert_level === "critical" ? "긴급" : "고위험"}
            </Badge>
          )}
          {verification?.requiresAttention && (
            <Badge variant="destructive" className="px-1.5 py-0.5 text-[0.7rem]">
              {verification.label}
            </Badge>
          )}
          {fpsLabel && (
            <span className="rounded bg-black/65 px-1.5 py-0.5 text-[0.7rem] tabular-nums text-white backdrop-blur-sm">
              {fpsLabel}
            </span>
          )}
        </div>
      </div>

      {/* 하단 오버레이: 카메라명/위치 + 컨트롤 */}
      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/60 to-transparent p-2 pt-6">
        <div className="flex items-end justify-between gap-2">
          <div className="min-w-0 text-white">
            <div className={cn("truncate font-medium", compactDensity ? "text-xs" : "text-sm")}>
              {camera.name ?? id}
            </div>
            {!compactDensity && camera.location && (
              <div className="truncate text-[0.7rem] text-white/70">{camera.location}</div>
            )}
          </div>
          <div className="pointer-events-auto flex items-center gap-1.5">
            {/* 순서 이동(키보드/터치 접근): sort=default 모드에서만 노출 */}
            {draggable && !focus && (
              <>
                <Button
                  size="xs"
                  variant="ghost"
                  className="px-1.5 text-white hover:bg-white/10 hover:text-white disabled:opacity-30"
                  disabled={!canMovePrev}
                  onClick={() => onMove?.(-1)}
                  aria-label="이전 위치로 이동"
                  title="이전 위치로 이동"
                >
                  ←
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  className="px-1.5 text-white hover:bg-white/10 hover:text-white disabled:opacity-30"
                  disabled={!canMoveNext}
                  onClick={() => onMove?.(1)}
                  aria-label="다음 위치로 이동"
                  title="다음 위치로 이동"
                >
                  →
                </Button>
              </>
            )}
            <Button
              size="xs"
              variant="ghost"
              className="text-white hover:bg-white/10 hover:text-white"
              onClick={() => setActive(false)}
              aria-label="라이브 중지"
            >
              중지
            </Button>
            <Button
              size="xs"
              variant="ghost"
              className="text-white hover:bg-white/10 hover:text-white"
              onClick={() => onFocus(id)}
              aria-label={focus ? "그리드로 돌아가기" : "이 카메라 확대"}
            >
              {focus ? "축소" : "확대"}
            </Button>
          </div>
        </div>
      </div>

      {/* 최근 프레임 시각 — compactDensity에서는 숨김 */}
      {lastFrameLabel && !compactDensity && (
        <div className="pointer-events-none absolute inset-x-2 top-8 text-[0.65rem] text-white/55">
          {lastFrameLabel}
        </div>
      )}
    </div>
  );
}

function isDeemphasizedAlert(event: EventItem): boolean {
  if (event.verification_state === "unverified" || event.vlm_unconfirmed) return false;
  return ["likely_false_positive", "resolved_false_positive", "suppressed"].includes(event.alert_state ?? "");
}

function cameraReceiveState(telemetry: CameraTelemetryItem | undefined, healthy: boolean) {
  if (!telemetry) return { label: "상태 미확인", dotClass: "bg-white/70" };
  if (telemetry.health_state === "disconnected" || !telemetry.connected) {
    return { label: "연결 끊김", dotClass: "bg-red-400" };
  }
  if (telemetry.health_state === "stale" || telemetry.is_stale) {
    return { label: "영상 지연", dotClass: "bg-warning" };
  }
  if (telemetry.health_state === "initializing") {
    return { label: "첫 프레임 대기", dotClass: "bg-warning" };
  }
  return healthy
    ? { label: "정상 수신", dotClass: "bg-success" }
    : { label: "상태 확인 필요", dotClass: "bg-warning" };
}
