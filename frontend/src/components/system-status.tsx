"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { AiRuntimeStatus } from "@/components/ai-runtime-status";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { SystemInfo } from "@/lib/engine";

function pct(n?: number | null): string {
  return typeof n === "number" ? `${n.toFixed(1)}%` : "—";
}
function clamp(n?: number | null): number {
  return typeof n === "number" ? Math.max(0, Math.min(100, n)) : 0;
}
function tone(n?: number | null): string {
  if (typeof n !== "number") return "bg-muted-foreground/30";
  return n < 70 ? "bg-emerald-500" : n < 90 ? "bg-amber-500" : "bg-red-500";
}

// 시스템 리소스(CPU/메모리/디스크/GPU) + 헬스 컴포넌트. 10초 폴링. 대시보드에 표시.
export function SystemStatus() {
  const [info, setInfo] = useState<SystemInfo | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/system", { cache: "no-store" });
      setInfo((await r.json()) as SystemInfo);
    } catch {
      /* keep last */
    }
  }, []);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const r = await fetch("/api/system", { cache: "no-store" });
        if (active) setInfo((await r.json()) as SystemInfo);
      } catch {
        /* keep last */
      }
    };
    load();
    const t = setInterval(refresh, 10000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, [refresh]);

  if (!info) return null;
  const m = info.metrics ?? {};
  const comps = info.health?.components ?? [];
  const rows = [
    { label: "CPU", v: m.cpu_percent, sub: undefined as string | undefined },
    {
      label: "메모리",
      v: m.memory_percent,
      sub: m.memory_total_mb
        ? `${Math.round(m.memory_used_mb ?? 0)} / ${Math.round(m.memory_total_mb)} MB`
        : undefined,
    },
    {
      label: "디스크",
      v: m.disk_percent,
      sub: m.disk_total_gb
        ? `${(m.disk_used_gb ?? 0).toFixed(1)} / ${m.disk_total_gb.toFixed(1)} GB`
        : undefined,
    },
    {
      label: "GPU",
      v: m.gpu_percent ?? undefined,
      sub:
        m.gpu_percent == null
          ? "감지 안 됨"
          : m.gpu_memory_percent != null
            ? `VRAM ${m.gpu_memory_percent.toFixed(0)}%`
            : undefined,
    },
  ];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">시스템 상태</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {rows.map((r) => (
            <div key={r.label} className="space-y-1">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">{r.label}</span>
                <span className="font-medium tabular-nums">{pct(r.v)}</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                <div className={`h-full ${tone(r.v)}`} style={{ width: `${clamp(r.v)}%` }} />
              </div>
              {r.sub && <div className="text-xs text-muted-foreground">{r.sub}</div>}
            </div>
          ))}
        </div>
        {comps.length > 0 && (
          <div className="flex flex-wrap gap-2 border-t pt-3">
            {comps.map((c) => (
              <Badge key={c.name} variant={c.healthy ? "secondary" : "destructive"} title={c.message}>
                {c.healthy ? "●" : "○"} {c.name}
              </Badge>
            ))}
          </div>
        )}
        <AiRuntimeStatus runtime={info.ai_runtime} />
      </CardContent>
    </Card>
  );
}
