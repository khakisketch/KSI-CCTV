"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

type Status = { connected: boolean; running: boolean };

// 탐지 파이프라인 시작/중지 제어. 상태는 5초 폴링. 제어는 데몬에 mount된 파이프라인에서만 동작
// (standalone API면 connected=false → 안내). 쓰기는 서버 라우트 경유(키 미노출).
export function PipelineControl() {
  const [st, setSt] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/control/status", { cache: "no-store" });
      setSt((await r.json()) as Status);
    } catch {
      setSt({ connected: false, running: false });
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  async function act(action: "start" | "stop") {
    setBusy(true);
    try {
      const r = await fetch(`/api/control/${action}`, { method: "POST" });
      const d = (await r.json().catch(() => ({}))) as { detail?: string };
      if (r.ok) {
        toast.success(action === "start" ? "파이프라인을 시작했습니다." : "파이프라인을 중지했습니다.");
        refresh();
      } else {
        toast.error(d.detail ?? "제어 실패");
      }
    } finally {
      setBusy(false);
    }
  }

  if (!st) {
    return <span className="text-xs text-muted-foreground">상태 확인 중…</span>;
  }
  if (!st.connected) {
    return (
      <span className="text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
        제어 미연결 — 탐지 데몬(src/run.py)으로 기동 시 여기서 시작/중지할 수 있습니다.
      </span>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant={st.running ? "default" : "outline"}>{st.running ? "실행 중" : "중지됨"}</Badge>
      <Button size="sm" disabled={busy || st.running} onClick={() => act("start")}>
        시작
      </Button>
      <Button size="sm" variant="outline" disabled={busy || !st.running} onClick={() => act("stop")}>
        중지
      </Button>
    </div>
  );
}
