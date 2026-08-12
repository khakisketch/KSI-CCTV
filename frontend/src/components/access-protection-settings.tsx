"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type AuthMode = "open" | "environment" | "local" | "error";
type Action = "setup" | "change" | "disable";

export function AccessProtectionSettings({ initialMode }: { initialMode: AuthMode }) {
  const router = useRouter();
  const [mode, setMode] = useState(initialMode);
  const [busy, setBusy] = useState<Action | null>(null);
  const [message, setMessage] = useState("");

  async function submit(event: React.FormEvent<HTMLFormElement>, action: Action) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const currentPassword = String(form.get("currentPassword") ?? "");
    const newPassword = String(form.get("newPassword") ?? "");
    const confirmation = String(form.get("confirmation") ?? "");
    const setupToken = String(form.get("setupToken") ?? "");
    if (action !== "disable" && (!newPassword || newPassword !== confirmation)) {
      setMessage("새 비밀번호와 확인이 일치해야 합니다.");
      return;
    }
    if (action !== "setup" && !currentPassword) {
      setMessage("현재 비밀번호를 입력하세요.");
      return;
    }
    setBusy(action);
    setMessage("");
    try {
      const response = await fetch("/api/auth/protection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, currentPassword, newPassword, ...(action === "setup" ? { setupToken } : {}) }),
      });
      const data = await response.json().catch(() => ({})) as { detail?: string; mode?: AuthMode };
      if (!response.ok) {
        setMessage(response.status === 409 ? "접근 보호 상태가 다른 작업으로 변경되었습니다. 화면을 새로 고칩니다." : data.detail ?? "접근 보호 설정을 저장하지 못했습니다.");
        if (response.status === 409) router.refresh();
        return;
      }
      setMode(data.mode ?? (action === "disable" ? "open" : "local"));
      setMessage(action === "disable" ? "접근 보호를 해제했습니다." : "접근 보호 비밀번호를 저장했습니다.");
      formElement.reset();
      router.refresh();
    } catch {
      setMessage("서버에 연결할 수 없습니다. 네트워크 연결을 확인한 뒤 다시 시도하세요.");
    } finally {
      setBusy(null);
    }
  }

  const passwordFields = (current: boolean) => (
    <>
      <input name="username" autoComplete="username" className="sr-only" tabIndex={-1} aria-hidden="true" />
      {current && <Input className="min-h-11" name="currentPassword" type="password" autoComplete="current-password" placeholder="현재 비밀번호" aria-label="현재 비밀번호" />}
      <Input className="min-h-11" name="newPassword" type="password" autoComplete="new-password" placeholder="새 비밀번호" aria-label="새 비밀번호" />
      <Input className="min-h-11" name="confirmation" type="password" autoComplete="new-password" placeholder="새 비밀번호 확인" aria-label="새 비밀번호 확인" />
    </>
  );

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2"><CardTitle className="text-base">접근 보호</CardTitle><Badge variant={mode === "open" ? "warning" : mode === "error" ? "destructive" : "success"}>{mode === "open" ? "꺼짐" : mode === "environment" ? "외부 관리" : mode === "local" ? "켜짐" : "복구 필요"}</Badge></div>
      </CardHeader>
      <CardContent className="space-y-4">
        {mode === "open" && <form onSubmit={(event) => submit(event, "setup")} className="space-y-3"><p className="text-sm leading-6 text-foreground">접근 보호가 꺼져 있습니다. 초기 설정은 원격 사용자의 관리자 선점을 막기 위해 서비스 엔지니어가 제공한 설정 토큰이 필요합니다.</p><Input className="min-h-11" name="setupToken" type="password" autoComplete="off" placeholder="초기 설정 토큰" aria-label="초기 설정 토큰" />{passwordFields(false)}<Button className="min-h-11" type="submit" disabled={busy !== null}>{busy === "setup" ? "저장 중…" : "접근 보호 켜기"}</Button></form>}
        {mode === "local" && <div className="grid gap-6 lg:grid-cols-2"><form onSubmit={(event) => submit(event, "change")} className="space-y-3"><p className="text-sm text-muted-foreground">비밀번호를 변경하려면 현재 비밀번호가 필요합니다.</p>{passwordFields(true)}<Button className="min-h-11" type="submit" disabled={busy !== null}>{busy === "change" ? "변경 중…" : "비밀번호 변경"}</Button></form><form onSubmit={(event) => submit(event, "disable")} className="space-y-3 rounded-lg border border-warning/40 p-4"><p className="text-sm text-foreground">보호를 해제하면 이 네트워크의 모든 사용자가 카메라와 설정을 변경할 수 있습니다.</p><input name="username" autoComplete="username" className="sr-only" tabIndex={-1} aria-hidden="true" /><Input className="min-h-11" name="currentPassword" type="password" autoComplete="current-password" placeholder="현재 비밀번호" aria-label="보호 해제 현재 비밀번호" /><Button className="min-h-11" type="submit" variant="outline" disabled={busy !== null}>{busy === "disable" ? "해제 중…" : "접근 보호 해제"}</Button></form></div>}
        {mode === "environment" && <p className="text-sm leading-6 text-muted-foreground">접근 보호는 환경 변수로 외부에서 관리됩니다. 이 화면에서는 비밀번호를 변경하거나 보호를 해제할 수 없습니다.</p>}
        {mode === "error" && <p className="text-sm leading-6 text-destructive">접근 보호 상태를 확인할 수 없습니다. 보호된 접근을 유지하고 있으므로 서버의 설정 파일 및 저장소 권한을 확인한 뒤 다시 시도하세요.</p>}
        {message && <p role="status" className="text-sm" style={{ wordBreak: "keep-all" }}>{message}</p>}
      </CardContent>
    </Card>
  );
}
