"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

type Ev = { name: string; alert_level?: string };

// 카메라 탐지 프로파일 편집 — 도메인 선택 + 활성 이벤트 토글. config 저장(재시작 후 적용).
// 도메인/이벤트 목록은 정책(/api/policies)에서 받는다. 쓰기는 서버 라우트 경유(키 미노출).
export function CameraSettings({
  cameraId,
  initialDomain,
  initialEvents,
  policyProfiles,
}: {
  cameraId: string;
  initialDomain?: string;
  initialEvents: string[];
  policyProfiles: Record<string, Ev[]>;
}) {
  const domains = Object.keys(policyProfiles);
  const [open, setOpen] = useState(false);
  const [domain, setDomain] = useState(
    initialDomain && domains.includes(initialDomain) ? initialDomain : (domains[0] ?? ""),
  );
  const [events, setEvents] = useState<string[]>(initialEvents);
  const [busy, setBusy] = useState(false);

  const domainEvents = policyProfiles[domain] ?? [];
  const isOn = (n: string) => events.includes(n);

  function changeDomain(d: string) {
    setDomain(d);
    const valid = new Set((policyProfiles[d] ?? []).map((e) => e.name));
    setEvents((prev) => prev.filter((x) => valid.has(x)));
  }

  async function save() {
    setBusy(true);
    try {
      const enabled_events = events.filter((n) => domainEvents.some((e) => e.name === n));
      const r = await fetch(`/api/cameras/${encodeURIComponent(cameraId)}/profile`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain, enabled_events }),
      });
      const d = (await r.json().catch(() => ({}))) as { detail?: string };
      if (r.ok) toast.success("카메라 설정을 저장했습니다 (재시작 후 적용).");
      else toast.error(d.detail ?? "저장 실패");
    } finally {
      setBusy(false);
    }
  }

  if (domains.length === 0) return null;
  if (!open) {
    return (
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
        설정 편집
      </Button>
    );
  }

  return (
    <div className="space-y-2 border-t pt-3">
      <div className="space-y-1">
        <p className="text-xs font-medium">도메인 프로파일</p>
        <div className="flex flex-wrap gap-1.5">
          {domains.map((d) => (
            <Button
              key={d}
              size="sm"
              variant={domain === d ? "default" : "outline"}
              disabled={busy}
              onClick={() => changeDomain(d)}
            >
              {d}
            </Button>
          ))}
        </div>
      </div>
      <div className="space-y-1">
        <p className="text-xs font-medium">활성 이벤트</p>
        <div className="grid grid-cols-1 gap-1">
          {domainEvents.map((e) => (
            <label key={e.name} className="flex items-center gap-2 text-xs" style={{ wordBreak: "keep-all" }}>
              <input
                type="checkbox"
                className="h-3.5 w-3.5 accent-primary"
                checked={isOn(e.name)}
                disabled={busy}
                onChange={() => setEvents((s) => (isOn(e.name) ? s.filter((x) => x !== e.name) : [...s, e.name]))}
              />
              <span>{e.name}</span>
              {e.alert_level && (
                <Badge variant="outline" className="ml-auto">
                  {e.alert_level}
                </Badge>
              )}
            </label>
          ))}
        </div>
      </div>
      <div className="flex gap-2">
        <Button size="sm" disabled={busy} onClick={save}>
          저장
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setOpen(false)}>
          닫기
        </Button>
      </div>
    </div>
  );
}
