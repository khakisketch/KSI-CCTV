"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type ReportCheck = { id?: string; label?: string; status?: string; summary?: string; remediation?: string };
type ReportCamera = {
  id?: string;
  name?: string;
  location?: string;
  health_state?: string;
  fps?: number;
  enabled_events?: string[];
  zone_count?: number;
  ready?: boolean;
  checks?: Record<string, boolean | string | null>;
};
type SafeReport = {
  revision?: number;
  generated_at?: string;
  completed_at?: string | null;
  ready?: boolean;
  site?: { name?: string; location?: string; timezone?: string; installer?: string; expected_camera_count?: number };
  progress?: { passed?: number; total?: number; percent?: number };
  checks?: ReportCheck[];
  cameras?: ReportCamera[];
  handoff?: { customer_name?: string; notes?: string; accepted_at?: string | null };
};

export function HandoffReport({ report }: { report: Record<string, unknown> }) {
  const safeReport = redact(report) as SafeReport;
  const checks = safeReport.checks ?? [];
  const cameras = safeReport.cameras ?? [];
  const failed = checks.filter((check) => check.status === "fail");
  const [revision, setRevision] = useState(safeReport.revision ?? 0);
  const [handoff, setHandoff] = useState(safeReport.handoff ?? {});
  const [customerName, setCustomerName] = useState("");
  const [notes, setNotes] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  function download() {
    const blob = new Blob([JSON.stringify({ ...safeReport, revision, handoff }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "commissioning-report.json";
    link.click();
    URL.revokeObjectURL(url);
  }

  async function acceptHandoff() {
    if (!customerName.trim() || !confirmed) {
      setMessage("고객 담당자 이름과 확인 동의가 필요합니다.");
      return;
    }
    setSaving(true);
    setMessage("");
    const response = await fetch("/api/commissioning/handoff", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        customer_name: customerName.trim(),
        notes,
        expected_revision: revision,
      }),
    });
    const result = await response.json().catch(() => ({}));
    setSaving(false);
    if (!response.ok) {
      setMessage(response.status === 409 ? "설치 기록이 변경되었습니다. 페이지를 새로고침하세요." : result.detail ?? "인수 확인을 저장하지 못했습니다.");
      return;
    }
    setRevision(result.revision ?? revision);
    setHandoff(result.handoff ?? {});
    setMessage("현장 인수 확인을 기록했습니다.");
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-2 print:hidden">
        <Button className="min-h-11" onClick={() => window.print()}>인쇄</Button>
        <Button variant="outline" className="min-h-11" onClick={download}>JSON 내려받기</Button>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div><CardTitle>{safeReport.site?.name || "현장 미설정"}</CardTitle><p className="mt-1 text-sm text-muted-foreground">{safeReport.site?.location || "위치 미지정"}</p></div>
          <Badge variant={safeReport.ready ? "default" : "destructive"}>{safeReport.ready ? "인수 가능" : "미완료"}</Badge>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <Summary label="설치 담당자" value={safeReport.site?.installer || "미입력"} />
          <Summary label="예정 카메라" value={`${safeReport.site?.expected_camera_count ?? 0}대`} />
          <Summary label="준비 진행률" value={`${safeReport.progress?.percent ?? 0}%`} />
          <Summary label="보고서 생성" value={formatDate(safeReport.generated_at)} />
        </CardContent>
      </Card>

      {failed.length > 0 && (
        <Card className="border-destructive/40">
          <CardHeader><CardTitle className="text-base">인수 전 해결 항목</CardTitle></CardHeader>
          <CardContent className="space-y-3">{failed.map((check) => <div key={check.id} className="border-b pb-3 last:border-0"><p className="font-medium">{check.label}</p><p className="text-sm text-muted-foreground">{check.summary}</p>{check.remediation && <p className="mt-1 text-sm">조치: {check.remediation}</p>}</div>)}</CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle className="text-base">시스템 검사 결과</CardTitle></CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">{checks.map((check) => <div key={check.id} className="rounded-lg border p-3"><div className="flex items-center justify-between gap-2"><p className="font-medium">{check.label}</p><Badge variant={check.status === "pass" ? "secondary" : "destructive"}>{check.status === "pass" ? "통과" : check.status === "warning" ? "경고" : "실패"}</Badge></div><p className="mt-1 text-sm text-muted-foreground">{check.summary}</p></div>)}</CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">카메라 인수시험</CardTitle></CardHeader>
        <CardContent className="space-y-3">{cameras.length === 0 ? <p className="text-sm text-muted-foreground">등록된 카메라가 없습니다.</p> : cameras.map((camera) => <div key={camera.id} className="rounded-lg border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><div><p className="font-medium">{camera.name ?? camera.id}</p><p className="text-xs text-muted-foreground">{camera.location || "위치 미지정"}</p></div><Badge variant={camera.ready ? "default" : "destructive"}>{camera.ready ? "완료" : "확인 필요"}</Badge></div><div className="mt-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4"><Summary label="영상 상태" value={camera.health_state === "receiving" ? "정상 수신" : camera.health_state || "정보 없음"} /><Summary label="수신 속도" value={typeof camera.fps === "number" ? `${camera.fps.toFixed(1)} FPS` : "정보 없음"} /><Summary label="감지 항목" value={`${camera.enabled_events?.length ?? 0}개`} /><Summary label="영역" value={`${camera.zone_count ?? 0}개`} /></div></div>)}</CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">현장 인수 확인 기록</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          {handoff.accepted_at ? <div className="grid gap-3 text-sm sm:grid-cols-3"><Summary label="고객 담당자" value={handoff.customer_name || "미입력"} /><Summary label="확인 시각" value={formatDate(handoff.accepted_at)} /><Summary label="메모" value={handoff.notes || "없음"} /></div> : safeReport.completed_at ? <><label className="grid gap-1.5 text-sm font-medium">고객 담당자 이름<input className="min-h-11 rounded-md border bg-background px-3" value={customerName} onChange={(event) => setCustomerName(event.target.value)} /></label><label className="grid gap-1.5 text-sm font-medium">인수 메모 (선택)<textarea className="min-h-20 rounded-md border bg-background px-3 py-2" value={notes} onChange={(event) => setNotes(event.target.value)} /></label><label className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />설치 결과와 미해결 항목을 고객 담당자와 함께 확인했습니다.</label><Button className="min-h-11 print:hidden" disabled={saving} onClick={acceptHandoff}>{saving ? "저장 중" : "인수 확인 기록"}</Button></> : <p className="text-sm text-muted-foreground">설치 진행에서 필수 검사를 완료한 뒤 인수 확인을 기록할 수 있습니다.</p>}
          {message && <p role="status" className="text-sm text-muted-foreground">{message}</p>}
        </CardContent>
      </Card>

      <Card className="print:border-0 print:shadow-none">
        <CardHeader><CardTitle className="text-base">현장 확인</CardTitle></CardHeader>
        <CardContent className="grid gap-8 pt-4 sm:grid-cols-2"><Signature label="설치 담당자 서명" /><Signature label="고객 담당자 서명" /></CardContent>
      </Card>
    </div>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return <div><p className="text-xs text-muted-foreground">{label}</p><p className="mt-0.5 font-medium">{value}</p></div>;
}

function Signature({ label }: { label: string }) {
  return <div><p className="text-sm text-muted-foreground">{label}</p><div className="mt-10 border-b" /><p className="mt-2 text-xs text-muted-foreground">이름 / 서명 / 날짜</p></div>;
}

function formatDate(value?: string | null): string {
  if (!value) return "미완료";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ko-KR");
}

function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).flatMap(([key, child]) =>
    /url|uri|password|secret|token|credential|api.?key/i.test(key) ? [] : [[key, redact(child)]],
  ));
}
