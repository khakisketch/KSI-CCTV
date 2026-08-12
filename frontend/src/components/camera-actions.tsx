"use client";

import { startTransition, useState } from "react";
import { LoaderCircle, Power, RefreshCw, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { CameraZoneEditor } from "@/components/camera-zone-editor";
import type { CameraZone } from "@/lib/cameras";

export function CameraActions({ cameraId, cameraName, enabled, zones = [], revision }: { cameraId: string; cameraName?: string; enabled: boolean; zones?: CameraZone[]; revision?: number }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  async function action(name: "test" | "apply" | "enable" | "disable") {
    setBusy(name);
    setMessage("");
    setFailed(false);
    try {
      const response = await fetch(`/api/cameras/${encodeURIComponent(cameraId)}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: name, expected_revision: revision }),
      });
      const data = (await response.json().catch(() => ({}))) as {
        detail?: string;
        message?: string;
        code?: string;
        ok?: boolean;
        connected?: boolean;
        applied?: boolean;
        apply_error?: string | null;
      };
      if (!response.ok) throw new Error(data.detail ?? "작업을 완료하지 못했습니다.");
      if (name === "test" && !(data.ok ?? data.connected)) {
        throw new Error(data.detail ?? data.message ?? "영상 첫 프레임을 받지 못했습니다.");
      }
      if (name !== "test" && data.applied !== true) {
        throw new Error(data.detail ?? data.apply_error ?? "설정은 저장됐지만 실행 환경에 적용되지 않았습니다.");
      }
      setMessage(name === "test" ? `영상 연결 확인 완료: ${data.code ?? "첫 프레임 수신"}` : "변경 후 실행 상태까지 확인했습니다.");
      if (name !== "test") startTransition(() => router.refresh());
    } catch (error) {
      setFailed(true);
      setMessage(error instanceof Error ? error.message : "작업 중 오류가 발생했습니다.");
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!window.confirm(`${cameraName ?? cameraId} 카메라와 저장된 영역 설정을 삭제하시겠습니까?`)) return;
    setBusy("delete");
    setFailed(false);
    try {
      const query = typeof revision === "number" ? `?expected_revision=${revision}` : "";
      const response = await fetch(`/api/cameras/${encodeURIComponent(cameraId)}${query}`, { method: "DELETE" });
      if (!response.ok) {
        const data = (await response.json().catch(() => ({}))) as { detail?: string };
        throw new Error(data.detail ?? "삭제할 수 없습니다.");
      }
      startTransition(() => router.refresh());
    } catch (error) {
      setFailed(true);
      setMessage(error instanceof Error ? error.message : "삭제 중 오류가 발생했습니다.");
      setBusy(null);
    }
  }

  return (
    <div className="space-y-2 border-t pt-3">
      <div className="grid grid-cols-2 gap-2">
        <Button className="min-h-11" variant="outline" disabled={busy !== null} onClick={() => action("test")}>
          {busy === "test" ? <LoaderCircle className="animate-spin" /> : <RefreshCw />} 연결 검사
        </Button>
        <Button className="min-h-11" variant="outline" disabled={busy !== null} onClick={() => action(enabled ? "disable" : "enable")}>
          {busy === (enabled ? "disable" : "enable") ? <LoaderCircle className="animate-spin" /> : <Power />} {enabled ? "비활성화" : "활성화"}
        </Button>
        {enabled ? <Button className="min-h-11" variant="outline" disabled={busy !== null} onClick={() => action("apply")}>
          <RefreshCw /> 설정 재적용
        </Button> : <p className="col-span-2 rounded-lg border border-muted p-3 text-xs text-muted-foreground">비활성 카메라는 설정을 재적용할 수 없습니다. 활성화한 후 적용하세요.</p>}
        <Button className="min-h-11" variant="destructive" disabled={busy !== null} onClick={remove}>
          {busy === "delete" ? <LoaderCircle className="animate-spin" /> : <Trash2 />} 삭제
        </Button>
      </div>
      <CameraZoneEditor cameraId={cameraId} initialZones={zones} initialRevision={revision} />
      {message && <p role={failed ? "alert" : "status"} className={`text-xs ${failed ? "text-destructive" : "text-foreground"}`}>{message}</p>}
    </div>
  );
}
