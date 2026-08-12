"use client";

import Link from "next/link";
import { useState } from "react";

import { CommissioningStatus } from "@/components/commissioning-status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { CommissioningCamera, CommissioningData } from "@/lib/engine";

const CHECKS: Array<{ key: keyof CommissioningCamera["checks"]; label: string; requiresAi?: boolean; requiresEvents?: boolean }> = [
  { key: "live_verified", label: "라이브 영상 확인" }, { key: "reconnect_verified", label: "재연결 확인" },
  { key: "detection_verified", label: "감지 확인", requiresAi: true }, { key: "alert_verified", label: "알림 확인", requiresEvents: true },
];

export function CommissioningDashboard({ initial }: { initial: CommissioningData }) {
  const [data, setData] = useState(initial); const [message, setMessage] = useState(""); const [saving, setSaving] = useState<string | null>(null);
  async function saveCamera(camera: CommissioningCamera, changes: Partial<CommissioningCamera["checks"]>) {
    setSaving(camera.id); setMessage("");
    const response = await fetch(`/api/commissioning/cameras/${encodeURIComponent(camera.id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...camera.checks, ...changes, expected_revision: data.revision }) });
    const next = await response.json().catch(() => ({})); setSaving(null);
    if (!response.ok) { setMessage(response.status === 409 ? "다른 변경사항이 있습니다. 페이지를 새로고침하세요." : next.detail ?? "저장하지 못했습니다."); return; }
    setData(next.revision !== undefined ? next : data); setMessage("카메라 확인 내용을 저장했습니다.");
  }
  async function completion(action: "complete" | "reopen") {
    const response = await fetch(`/api/commissioning/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: data.revision }) });
    const next = await response.json().catch(() => ({}));
    if (!response.ok) { setMessage(next.detail ?? "작업을 완료하지 못했습니다."); return; }
    setData(next.revision !== undefined ? next : data); setMessage(action === "complete" ? "설치 완료로 처리했습니다." : "설치 진행을 다시 열었습니다.");
  }
  return <div className="space-y-6">
    {message && <p role="alert" className="text-sm text-muted-foreground">{message}</p>}
    <Card><CardContent className="py-5"><div className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-sm text-muted-foreground">설치 진행률</p><p className="text-3xl font-semibold">{data.progress.percent}%</p><p className="text-sm text-muted-foreground">{data.progress.passed} / {data.progress.total} 항목 완료</p></div><div className="flex gap-2"><Link href="/diagnostics" className="inline-flex min-h-11 items-center rounded-md border border-input px-4 text-sm font-medium">진단 보기</Link><Button className="min-h-11" disabled={!data.ready_to_complete} onClick={() => completion(data.completed_at ? "reopen" : "complete")}>{data.completed_at ? "설치 진행 다시 열기" : "설치 완료"}</Button></div></div><div className="mt-3 h-2 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary" style={{ width: `${data.progress.percent}%` }} /></div>{!data.ready_to_complete && <p className="mt-2 text-xs text-muted-foreground">모든 필수 확인 항목을 통과하면 설치를 완료할 수 있습니다.</p>}</CardContent></Card>
    <Card><CardHeader><CardTitle className="text-base">사전 점검</CardTitle></CardHeader><CardContent className="space-y-3">{data.checks.map((check) => <div key={check.id} className="flex items-start justify-between gap-3 border-b pb-3 last:border-0"><div><p className="font-medium">{check.label}</p><p className="text-sm text-muted-foreground">{check.summary}</p>{check.remediation && <p className="mt-1 text-sm text-muted-foreground">조치: {check.remediation}</p>}</div><CommissioningStatus status={check.status} /></div>)}</CardContent></Card>
    <div className="space-y-4">{data.cameras.map((camera) => <CameraChecklist key={camera.id} camera={camera} saving={saving === camera.id} onSave={saveCamera} />)}</div>
  </div>;
}

function CameraChecklist({ camera, saving, onSave }: { camera: CommissioningCamera; saving: boolean; onSave: (camera: CommissioningCamera, changes: Partial<CommissioningCamera["checks"]>) => void }) {
  const [checks, setChecks] = useState(camera.checks); const [notes, setNotes] = useState(camera.checks.notes ?? ""); const hasAi = camera.ai_events.length > 0; const hasEvents = camera.enabled_events.length > 0;
  return <Card><CardHeader><div className="flex items-start justify-between gap-3"><div><CardTitle className="text-base">{camera.name}</CardTitle><p className="mt-1 text-sm text-muted-foreground">{camera.location || "위치 미지정"} · {camera.ready ? "운영 준비" : "확인 필요"}</p></div><div className="flex gap-3 text-sm"><Link className="underline" href={`/cameras/${encodeURIComponent(camera.id)}`}>연결 관리</Link><Link className="underline" href={`/cameras#camera-${camera.id}`}>설정 진단</Link></div></div></CardHeader><CardContent className="space-y-3">{CHECKS.map(({ key, label, requiresAi, requiresEvents }) => { const disabled = Boolean((requiresAi && !hasAi) || (requiresEvents && !hasEvents)); return <label key={key} className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" checked={Boolean(checks[key])} disabled={disabled} onChange={(e) => setChecks((old) => ({ ...old, [key]: e.target.checked }))} />{label}{disabled && <span className="text-xs text-muted-foreground">{requiresAi ? "AI 이벤트를 먼저 설정해야 합니다." : "이벤트를 먼저 설정해야 합니다."}</span>}</label>; })}<label className="grid gap-1.5 text-sm">설치 메모<textarea className="min-h-20 rounded-md border bg-background px-3 py-2" value={notes} onChange={(e) => setNotes(e.target.value)} /></label><Button className="min-h-11" disabled={saving} onClick={() => onSave(camera, { ...checks, notes })}>{saving ? "저장 중" : "확인 내용 저장"}</Button></CardContent></Card>;
}
