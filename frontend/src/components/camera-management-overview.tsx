"use client";

import { Camera, MapPin, Radio, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type ReactNode, useCallback, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { CameraTelemetryProvider, useCameraTelemetry } from "@/components/camera-telemetry";
import { isCameraReceiving, type CameraItem } from "@/lib/engine";
import { cn } from "@/lib/utils";

type Filter = "all" | "active" | "inactive" | "unhealthy" | "managed" | "unmanaged";

const FILTERS: Array<{ value: Filter; label: string }> = [
  { value: "all", label: "전체" },
  { value: "active", label: "활성" },
  { value: "inactive", label: "비활성" },
  { value: "unhealthy", label: "상태 확인" },
  { value: "managed", label: "관리형" },
  { value: "unmanaged", label: "미관리" },
];

function getHealth(camera: CameraItem, telemetry: ReturnType<typeof useCameraTelemetry>) {
  const status = camera.id ? telemetry?.cameras?.[camera.id] : undefined;
  const healthy = Boolean(telemetry && !telemetry.stale && isCameraReceiving(status));
  const unavailable = !telemetry || telemetry.status !== "ok" || telemetry.stale || !status;
  return {
    healthy,
    label: healthy ? "정상 수신" : unavailable ? "상태 확인 필요" : "연결 확인 필요",
  };
}

function CameraMasterDetail({ cameras, onboarding }: { cameras: CameraItem[]; onboarding?: ReactNode }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const telemetry = useCameraTelemetry();
  const [filter, setFilter] = useState<Filter>("all");

  const visible = useMemo(
    () =>
      cameras.filter((camera) => {
        const h = getHealth(camera, telemetry);
        if (filter === "active") return Boolean(camera.enabled);
        if (filter === "inactive") return !camera.enabled;
        if (filter === "unhealthy") return !h.healthy;
        if (filter === "managed") return Boolean(camera.managed);
        if (filter === "unmanaged") return !camera.managed;
        return true;
      }),
    [cameras, filter, telemetry],
  );

  const selectedCameraId = searchParams.get("camera") ?? visible[0]?.id ?? null;
  const selectedCamera = useMemo(
    () => visible.find((c) => c.id === selectedCameraId) ?? visible[0] ?? null,
    [visible, selectedCameraId],
  );

  const selectCamera = useCallback(
    (id: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("camera", id);
      router.push(`/cameras?${params.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  return (
    <div data-workspace className="flex h-full flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-4 py-2.5">
        {FILTERS.map((item) => (
          <button
            key={item.value}
            type="button"
            aria-pressed={filter === item.value}
            className={cn(
              "min-h-9 shrink-0 rounded-md border px-3 text-xs font-medium transition-colors",
              filter === item.value
                ? "border-primary bg-primary text-primary-foreground"
                : "bg-background hover:bg-muted",
            )}
            onClick={() => setFilter(item.value)}
          >
            {item.label}
          </button>
        ))}
        <span className="ml-auto text-xs text-muted-foreground">{visible.length}대</span>
        {onboarding}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {visible.length === 0 ? (
            <div className="flex flex-1 items-center justify-center p-8">
              <p className="text-sm text-muted-foreground">선택한 조건의 카메라가 없습니다.</p>
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto">
              <ul role="listbox" aria-label="카메라 목록" className="divide-y">
                {visible.map((camera) => {
                  const isSelected = camera.id === selectedCamera?.id;
                  const h = getHealth(camera, telemetry);
                  return (
                    <li key={camera.id} className="flex items-center gap-2">
                      <button
                        type="button"
                        role="option"
                        aria-selected={isSelected}
                        onClick={() => camera.id && selectCamera(camera.id)}
                        className={cn(
                          "flex w-full items-center gap-3 px-4 py-3 text-left transition-colors",
                          isSelected ? "bg-accent/50" : "hover:bg-muted/50",
                        )}
                      >
                        <Camera
                          className={cn(
                            "size-4 shrink-0",
                            h.healthy ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground",
                          )}
                          aria-hidden="true"
                        />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium truncate">{camera.name ?? camera.id ?? "카메라"}</p>
                          <p className="text-xs text-muted-foreground truncate">{camera.location ?? "위치 미지정"}</p>
                        </div>
                        <div className="flex shrink-0 items-center gap-1.5">
                          <Badge variant={h.healthy ? "success" : "warning"} className="text-[10px]">
                            {h.label}
                          </Badge>
                          <Badge variant={camera.enabled ? "default" : "outline"} className="text-[10px]">
                            {camera.enabled ? "활성" : "비활성"}
                          </Badge>
                        </div>
                      </button>
                      {camera.id && camera.managed && (
                        <Link
                          href={`/cameras/${encodeURIComponent(camera.id)}`}
                          className="mr-3 inline-flex min-h-9 shrink-0 items-center rounded-md border border-border px-2 text-xs font-medium hover:bg-muted lg:hidden"
                        >
                          관리
                        </Link>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>

        <div className="hidden w-[360px] shrink-0 flex-col border-l lg:flex">
          {selectedCamera ? (
            <CameraInspector camera={selectedCamera} telemetry={telemetry} />
          ) : (
            <div className="flex flex-1 items-center justify-center p-4">
              <p className="text-sm text-muted-foreground">카메라를 선택하세요.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function CameraInspector({
  camera,
  telemetry,
}: {
  camera: CameraItem;
  telemetry: ReturnType<typeof useCameraTelemetry>;
}) {
  const health = getHealth(camera, telemetry);
  const teleStatus = camera.id ? telemetry?.cameras?.[camera.id] : undefined;
  const zoneCount = camera.zones?.length ?? 0;
  const eventCount = camera.enabled_events?.length ?? 0;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="shrink-0 border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <Badge variant={camera.enabled ? "default" : "outline"}>{camera.enabled ? "활성" : "비활성"}</Badge>
          <Badge variant={camera.managed ? "secondary" : "outline"}>{camera.managed ? "관리형" : "미관리"}</Badge>
        </div>
        <h3 className="mt-2 text-base font-semibold">{camera.name ?? camera.id ?? "카메라"}</h3>
        <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
          <MapPin className="size-3" aria-hidden="true" />
          {camera.location ?? "위치 미지정"}
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <Radio
              className={cn(
                "size-4 shrink-0",
                health.healthy ? "text-emerald-600 dark:text-emerald-400" : "text-amber-700 dark:text-amber-300",
              )}
              aria-hidden="true"
            />
            <div>
              <p className="text-sm font-medium">{health.label}</p>
              {teleStatus && (
                <p className="text-xs text-muted-foreground">
                  {teleStatus.fps ? `${teleStatus.fps.toFixed(1)} FPS` : "FPS —"}
                  {teleStatus.health_state ? ` · ${teleStatus.health_state}` : ""}
                </p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <ShieldAlert className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium">감지 {eventCount}개 {eventCount ? "활성" : "미설정"}</p>
              {camera.enabled_events?.length ? (
                <p className="text-xs text-muted-foreground">{camera.enabled_events.join(", ")}</p>
              ) : null}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <MapPin className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium">영역 {zoneCount}개 {zoneCount ? "설정" : "미설정"}</p>
            </div>
          </div>
          {camera.resolution && (
            <div className="text-xs text-muted-foreground">
              해상도 {camera.resolution[0]}×{camera.resolution[1]}
              {camera.fps ? ` · ${camera.fps} FPS` : ""}
            </div>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t px-4 py-3">
        {camera.id && camera.managed ? (
          <Link
            href={`/cameras/${encodeURIComponent(camera.id)}`}
            className="inline-flex min-h-11 w-full items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            관리
          </Link>
        ) : (
          <p className="text-center text-xs text-muted-foreground">관리형 등록 필요</p>
        )}
      </div>
    </div>
  );
}

export function CameraManagementOverview({ cameras, onboarding }: { cameras: CameraItem[]; onboarding?: ReactNode }) {
  return (
    <CameraTelemetryProvider>
      <CameraMasterDetail cameras={cameras} onboarding={onboarding} />
    </CameraTelemetryProvider>
  );
}
