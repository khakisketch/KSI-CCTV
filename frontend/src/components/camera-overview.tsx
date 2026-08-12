"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";

import { isCameraReceiving, type CameraItem, type CameraTelemetryItem } from "@/lib/engine";

export function CameraOverview({
  cameras,
  telemetry,
}: {
  cameras: CameraItem[];
  telemetry: Record<string, CameraTelemetryItem>;
}) {
  const [revision, setRevision] = useState(0);
  const [failed, setFailed] = useState<Set<string>>(new Set());

  useEffect(() => {
    const timer = setInterval(() => {
      setFailed(new Set());
      setRevision(Date.now());
    }, 8000);
    return () => clearInterval(timer);
  }, []);

  if (cameras.length === 0) {
    return (
      <div className="grid min-h-56 place-items-center rounded-lg border border-dashed text-sm text-muted-foreground">
        운영 중인 관리형 카메라가 없습니다.
      </div>
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {cameras.slice(0, 4).map((camera) => {
        const id = camera.id!;
        const status = telemetry[id];
        const healthy = isCameraReceiving(status);
        return (
          <Link
            key={id}
            href={`/cameras/${encodeURIComponent(id)}`}
            className="group relative aspect-video overflow-hidden rounded-lg bg-black ring-1 ring-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {!failed.has(id) ? (
              <Image
                unoptimized
                fill
                sizes="(max-width: 640px) 100vw, 50vw"
                src={`/api/cameras/${encodeURIComponent(id)}/snapshot?t=${revision}`}
                alt={`${camera.name ?? id} 현재 화면`}
                className="object-cover opacity-90 transition-opacity group-hover:opacity-100"
                onError={() => setFailed((current) => new Set(current).add(id))}
              />
            ) : (
              <div className="absolute inset-0 grid place-items-center bg-muted text-xs text-muted-foreground">
                현재 프레임을 불러올 수 없습니다
              </div>
            )}
            <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/80 to-transparent p-3 text-xs text-white">
              <span className="flex items-center gap-1.5 font-medium">
                <span className={`size-2 rounded-full ${healthy ? "bg-emerald-400" : "bg-amber-400"}`} />
                {healthy ? "정상 수신" : "상태 확인"}
              </span>
              {typeof status?.fps === "number" && <span className="tabular-nums">{status.fps.toFixed(1)} FPS</span>}
            </div>
            <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/60 to-transparent px-3 pt-8 pb-3 text-white">
              <div className="font-medium">{camera.name ?? id}</div>
              <div className="mt-0.5 text-xs text-white/70">{camera.location ?? "위치 미지정"}</div>
            </div>
          </Link>
        );
      })}
    </div>
  );
}
