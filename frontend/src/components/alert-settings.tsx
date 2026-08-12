"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AlertPreferences } from "@/lib/engine";

const LEVELS = ["low", "medium", "high", "critical"] as const;
const LEVEL_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  critical: "destructive",
  high: "destructive",
  medium: "default",
  low: "secondary",
};

// 알림 설정 편집 — 이메일 임계값 + 이벤트별 발송 on/off. 저장은 서버 라우트 경유(키 미노출).
// SMTP 자격증명은 env 전용이라 여기서 다루지 않는다.
export function AlertSettings({
  initial,
  events,
}: {
  initial: AlertPreferences;
  events: { name: string; alert_level: string }[];
}) {
  const [threshold, setThreshold] = useState(initial.email_alert_level_threshold);
  const [perEvent, setPerEvent] = useState<Record<string, boolean>>(
    initial.per_event_email_enabled ?? {},
  );
  const [busy, setBusy] = useState(false);

  const isOn = (n: string) => perEvent[n] ?? true;
  const enabledCount = events.filter((e) => isOn(e.name)).length;

  async function save() {
    setBusy(true);
    try {
      const body = {
        email_alert_level_threshold: threshold,
        per_event_email_enabled: Object.fromEntries(events.map((e) => [e.name, isOn(e.name)])),
      };
      const r = await fetch("/api/alert-preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = (await r.json().catch(() => ({}))) as { detail?: string };
      if (r.ok) {
        toast.success("알림 설정을 저장했습니다 (파이프라인 재시작 후 적용).");
      } else {
        toast.error(d.detail ?? "저장 실패");
      }
    } catch {
      toast.error("저장 요청을 보낼 수 없습니다. 네트워크 연결을 확인한 뒤 다시 저장하세요.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <p className="text-sm font-medium">이메일 발송 임계값</p>
        <div className="flex flex-wrap gap-2">
          {LEVELS.map((lv) => (
            <Button
              key={lv}
              size="sm"
              variant={threshold === lv ? "default" : "outline"}
              disabled={busy}
              onClick={() => setThreshold(lv)}
            >
              {lv}
            </Button>
          ))}
        </div>
        <p className="text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
          <code>{threshold}</code> 이상 등급의 이벤트만 이메일을 발송합니다. (SMTP 계정은 환경변수로 설정)
        </p>
      </div>

      {events.length > 0 && (
        <div className="space-y-2 border-t pt-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">이벤트별 이메일 발송</p>
            <Badge variant="secondary">
              {enabledCount}/{events.length} 발송
            </Badge>
          </div>
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {events.map((e) => (
              <label
                key={e.name}
                className="flex items-center gap-2 text-sm"
                style={{ wordBreak: "keep-all" }}
              >
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-primary"
                  checked={isOn(e.name)}
                  disabled={busy}
                  onChange={() => setPerEvent((p) => ({ ...p, [e.name]: !isOn(e.name) }))}
                />
                <span>{e.name}</span>
                {e.alert_level && (
                  <Badge variant={LEVEL_VARIANT[e.alert_level] ?? "outline"} className="ml-auto">
                    {e.alert_level}
                  </Badge>
                )}
              </label>
            ))}
          </div>
        </div>
      )}

      <Button onClick={save} disabled={busy}>
        {busy ? "저장 중…" : "알림 설정 저장"}
      </Button>
    </div>
  );
}
