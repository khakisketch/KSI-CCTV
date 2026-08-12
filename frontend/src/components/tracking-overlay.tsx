"use client";

import { useEffect, useState } from "react";

import type { TrackingSnapshot } from "@/lib/engine";

export function TrackingOverlay({ cameraId }: { cameraId: string }) {
  const [snapshot, setSnapshot] = useState<TrackingSnapshot | null>(null);

  useEffect(() => {
    let active = true;
    let refreshing = false;
    const controller = new AbortController();

    async function refresh() {
      if (refreshing) return;
      refreshing = true;
      try {
        const response = await fetch(`/api/cameras/${encodeURIComponent(cameraId)}/tracking`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) return;
        const next = await response.json() as TrackingSnapshot;
        if (active) setSnapshot(next);
      } catch {
        // A transient overlay failure must not interrupt live video playback.
      } finally {
        refreshing = false;
      }
    }

    void refresh();
    const timer = window.setInterval(refresh, 1000);
    return () => {
      active = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [cameraId]);

  if (!snapshot) return null;
  const width = Math.max(1, snapshot.frame.width);
  const height = Math.max(1, snapshot.frame.height);
  const xy = (point: [number, number]) => [point[0] * width, point[1] * height] as const;
  const markerId = `tracking-arrow-${cameraId.replaceAll(/[^A-Za-z0-9_-]/g, "-")}`;

  return (
    <div className="pointer-events-none absolute inset-0" aria-label="Tracking overlay">
      <svg className="size-full" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
        <defs>
          <marker id={markerId} markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker>
        </defs>
        {snapshot.zones.map((zone) => {
          const points = zone.points.map((item) => xy(item));
          if (zone.type === "line" && points.length === 2) {
            const [start, end] = points;
            const length = Math.hypot(end[0] - start[0], end[1] - start[1]);
            const sign = zone.direction === "negative" ? -1 : 1;
            const midX = (start[0] + end[0]) / 2;
            const midY = (start[1] + end[1]) / 2;
            const arrowLength = Math.min(width, height) * 0.1;
            const dx = length ? (-((end[1] - start[1]) / length)) * arrowLength * sign : 0;
            const dy = length ? (((end[0] - start[0]) / length)) * arrowLength * sign : 0;
            return <g key={zone.id} className="text-amber-400">
              <line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]} stroke="currentColor" strokeWidth="3" />
              <line x1={midX} y1={midY} x2={midX + dx} y2={midY + dy} stroke="currentColor" strokeWidth="3" markerStart={zone.direction === "any" ? `url(#${markerId})` : undefined} markerEnd={`url(#${markerId})`} />
            </g>;
          }
          if (zone.type === "polygon" && points.length >= 3) {
            return <polygon key={zone.id} points={points.map((item) => item.join(",")).join(" ")} fill="rgb(251 191 36 / 0.12)" stroke="rgb(251 191 36)" strokeWidth="3" />;
          }
          return null;
        })}
        {snapshot.tracks.map((track) => {
          const [x1, y1] = xy([track.bbox[0], track.bbox[1]]);
          const [x2, y2] = xy([track.bbox[2], track.bbox[3]]);
          const trajectory = track.trajectory.map((item) => xy(item));
          return <g key={track.track_id} className="text-emerald-400">
            {trajectory.length > 1 && <polyline points={trajectory.map((item) => item.join(",")).join(" ")} fill="none" stroke="currentColor" strokeWidth="2" opacity="0.75" />}
            <rect x={x1} y={y1} width={Math.max(1, x2 - x1)} height={Math.max(1, y2 - y1)} fill="none" stroke="currentColor" strokeWidth="3" />
            <text x={x1} y={Math.max(14, y1 - 5)} fill="currentColor" fontSize="14" fontWeight="600">#{track.track_id} {track.class_name} {Math.round(track.confidence * 100)}%</text>
          </g>;
        })}
      </svg>
      {snapshot.stale && <span className="absolute right-2 top-2 rounded bg-black/70 px-2 py-1 text-[0.65rem] text-amber-300">Tracking 지연</span>}
    </div>
  );
}
