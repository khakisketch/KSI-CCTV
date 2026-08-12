"use client";

import Link from "next/link";
import { LoaderCircle, RefreshCw } from "lucide-react";
import { startTransition, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { redactCameraMessage, type CameraZone } from "@/lib/cameras";
import type { CameraItem } from "@/lib/engine";

type StreamTest = { ok?: boolean; connected?: boolean; detail?: string; message?: string };
type SaveResult = { applied?: boolean; apply_error?: string | null; revision?: number };

function sourceSummary(camera: CameraItem): string {
  return `RTSP 스트림${camera.credential_configured ? " · 자격 증명 설정됨" : " · 인증 없음"}`;
}

export function CameraConnectionEditor({ camera, revision }: { camera: CameraItem; revision: number }) {
  const router = useRouter();
  const [name, setName] = useState(camera.name ?? "");
  const [location, setLocation] = useState(camera.location ?? "");
  const [rtspUrl, setRtspUrl] = useState(camera.stream_url ?? "");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [clearCredentials, setClearCredentials] = useState(false);
  const [enabled, setEnabled] = useState(Boolean(camera.enabled));
  const [tested, setTested] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  const invalidateTest = () => { setTested(false); setMessage(""); };

  async function testStream() {
    setTesting(true); setFailed(false); setMessage(""); setTested(false);
    try {
      const response = await fetch(`/api/cameras/${encodeURIComponent(camera.id ?? "")}/test-draft`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rtsp_url: rtspUrl,
          ...(clearCredentials ? { clear_credentials: true } : {}),
          ...(!clearCredentials && username ? { username } : {}),
          ...(!clearCredentials && password ? { password } : {}),
        }),
      });
      const result = (await response.json().catch(() => ({}))) as StreamTest;
      if (!response.ok || !(result.ok ?? result.connected)) throw new Error(result.detail ?? result.message ?? "영상 첫 프레임을 받지 못했습니다.");
      setTested(true); setMessage("현재 연결 정보로 영상 수신을 확인했습니다.");
    } catch (error) {
      setFailed(true); setMessage(redactCameraMessage(error instanceof Error ? error.message : "스트림 검사에 실패했습니다.", username, password));
    } finally { setTesting(false); }
  }

  async function save() {
    if (!tested) { setFailed(true); setMessage("저장 전에 현재 연결 정보의 스트림 검사를 성공시켜야 합니다."); return; }
    setSaving(true); setFailed(false); setMessage("");
    try {
      const payload = {
        name, location, rtsp_url: rtspUrl, enabled, profile: camera.profile ?? camera.detection_type ?? "general",
        enabled_events: camera.enabled_events ?? [], zones: (camera.zones ?? []) as CameraZone[], fps: camera.fps ?? 10,
        resolution: camera.resolution ?? null,
        ...(clearCredentials ? { clear_credentials: true } : {}),
        ...(!clearCredentials && username ? { username } : {}),
        ...(!clearCredentials && password ? { password } : {}),
      };
      const response = await fetch(`/api/cameras/${encodeURIComponent(camera.id ?? "")}?expected_revision=${revision}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const result = (await response.json().catch(() => ({}))) as SaveResult & { detail?: string };
      if (!response.ok) {
        if (response.status === 409) throw new Error("다른 변경사항이 저장되었습니다. 최신 설정을 불러온 뒤 다시 시도하세요.");
        throw new Error(result.detail ?? "연결 정보를 저장하지 못했습니다.");
      }
      setPassword(""); setUsername(""); setClearCredentials(false);
      if (result.applied === false) {
        setFailed(true); setMessage(`설정은 저장됐지만 실행 환경 적용에 실패했습니다: ${result.apply_error ?? "서비스 상태를 확인하고 재적용하세요."}`);
      } else {
        setMessage("연결 정보를 저장하고 실행 환경 적용까지 확인했습니다.");
      }
      startTransition(() => router.refresh());
    } catch (error) {
      setFailed(true); setMessage(redactCameraMessage(error instanceof Error ? error.message : "저장 중 오류가 발생했습니다.", username, password));
    } finally { setSaving(false); }
  }

  return <div className="mx-auto max-w-3xl space-y-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-2xl font-semibold tracking-tight">카메라 연결 관리</h1><p className="mt-1 text-sm text-muted-foreground">{sourceSummary(camera)}</p></div><Link className="inline-flex min-h-11 items-center rounded-lg border px-3 text-sm font-medium hover:bg-muted" href="/cameras">카메라 목록</Link></div>
    <Card><CardHeader><CardTitle className="text-base">현재 설정</CardTitle></CardHeader><CardContent><dl className="grid gap-3 text-sm sm:grid-cols-2"><div><dt className="text-muted-foreground">RTSP 주소</dt><dd className="break-all font-medium">{camera.stream_url}</dd></div><div><dt className="text-muted-foreground">자격 증명</dt><dd>{camera.credential_configured ? "설정됨" : "설정 안 됨"}</dd></div><div><dt className="text-muted-foreground">영상</dt><dd>{camera.resolution ? `${camera.resolution[0]} x ${camera.resolution[1]}` : "자동"} · {camera.fps ?? "-"} FPS</dd></div><div><dt className="text-muted-foreground">감지 및 영역</dt><dd>{camera.enabled_events?.join(", ") || "이벤트 없음"} · 영역 {camera.zones?.length ?? 0}개</dd></div></dl></CardContent></Card>
    <Card><CardHeader><CardTitle className="text-base">연결 정보 변경</CardTitle></CardHeader><CardContent className="space-y-4"><label className="grid gap-1.5 text-sm font-medium">카메라 이름<Input className="min-h-11" value={name} onChange={(event) => setName(event.target.value)} /></label><label className="grid gap-1.5 text-sm font-medium">위치<Input className="min-h-11" value={location} onChange={(event) => setLocation(event.target.value)} /></label><label className="grid gap-1.5 text-sm font-medium">RTSP 주소<Input className="min-h-11" value={rtspUrl} onChange={(event) => { setRtspUrl(event.target.value); invalidateTest(); }} /></label><p className="text-sm text-muted-foreground">기존 비밀번호는 표시하지 않습니다. 같은 장비에서는 입력한 항목만 교체되고 빈 항목은 유지됩니다. 장비나 포트를 바꾸면 기존 값 전체를 다시 입력하거나 인증 정보를 제거해야 합니다.</p><label className="grid gap-1.5 text-sm font-medium">교체 사용자 이름 (선택)<Input autoComplete="username" className="min-h-11" disabled={clearCredentials} value={username} onChange={(event) => { setUsername(event.target.value); invalidateTest(); }} /></label><label className="grid gap-1.5 text-sm font-medium">교체 비밀번호 (선택)<Input autoComplete="new-password" className="min-h-11" disabled={clearCredentials} type="password" value={password} onChange={(event) => { setPassword(event.target.value); invalidateTest(); }} /></label>{camera.credential_configured && <label className="flex min-h-11 items-center gap-3 text-sm font-medium"><input className="size-5" type="checkbox" checked={clearCredentials} onChange={(event) => { setClearCredentials(event.target.checked); setUsername(""); setPassword(""); invalidateTest(); }} />저장된 인증 정보 제거</label>}<label className="flex min-h-11 items-center gap-3 text-sm font-medium"><input className="size-5" type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />카메라 활성화</label><div className="flex flex-wrap gap-2 border-t pt-4"><Button className="min-h-11" variant="outline" disabled={testing || saving} onClick={testStream}>{testing && <LoaderCircle className="animate-spin" />}<RefreshCw /> 스트림 검사</Button><Button className="min-h-11" disabled={saving || testing || !tested} onClick={save}>{saving && <LoaderCircle className="animate-spin" />} 변경 저장</Button></div>{!tested && <p className="text-sm text-muted-foreground">저장하려면 현재 연결 정보로 스트림 검사를 성공시켜야 합니다.</p>}{message && <p role={failed ? "alert" : "status"} className={failed ? "text-sm text-destructive" : "text-sm text-foreground"}>{message}</p>}</CardContent></Card>
  </div>;
}
