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
    let active = true;
    const load = async () => {
      try {
        const r = await fetch("/api/control/status", { cache: "no-store" });
        if (active) setSt((await r.json()) as Status);
      } catch {
        if (active) setSt({ connected: false, running: false });
      }
    };
    load();
    const t = setInterval(refresh, 5000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, [refresh]);

  async function act(action: "start" | "stop") {
    if (action === "stop" && !window.confirm("모든 카메라의 AI 감지와 사건 알림이 중지됩니다. 분석 서비스를 중지하시겠습니까?")) return;
    setBusy(true);
    try {
      const r = await fetch(`/api/control/${action}`, { method: "POST" });
      const d = (await r.json().catch(() => ({}))) as { detail?: string };
      if (r.ok) {
        toast.success(action === "start" ? "분석 서비스를 시작했습니다." : "분석 서비스를 중지했습니다.");
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
        분석 서비스 제어에 연결되지 않았습니다. 진단 화면에서 서비스 상태를 확인하세요.
      </span>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant={st.running ? "default" : "outline"}>{st.running ? "실행 중" : "중지됨"}</Badge>
      <Button className="min-h-11" size="sm" disabled={busy || st.running} onClick={() => act("start")}>
        시작
      </Button>
      <Button className="min-h-11" size="sm" variant="outline" disabled={busy || !st.running} onClick={() => act("stop")}>
        중지
      </Button>
    </div>
  );
}
