"use client";

import { ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type AuthMode = "open" | "environment" | "local" | "error";

export function LoginForm({ mode }: { mode: AuthMode }) {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const protectedMode = mode === "environment" || mode === "local";

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formPassword = String(new FormData(event.currentTarget).get("password") ?? "");
    const value = (formPassword || password).trim();
    if (!value) {
      setError("비밀번호를 입력하세요.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: value }),
      });
      if (response.ok) {
        router.push("/");
        router.refresh();
        return;
      }
      const data = await response.json().catch(() => ({}));
      setError(data.detail ?? "로그인 실패");
    } catch {
      setError("서버에 연결할 수 없습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="space-y-1">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-6 w-6 text-primary" />
            <CardTitle className="text-xl">KSI-CCTV 로그인</CardTitle>
          </div>
          <p className="text-sm text-muted-foreground" style={{ wordBreak: "keep-all" }}>
            {protectedMode
              ? "관제 대시보드 접근을 위해 비밀번호를 입력하세요."
              : "접근 보호 상태를 확인할 수 없습니다."}
          </p>
        </CardHeader>
        <CardContent>
          {!protectedMode ? (
            <div className="space-y-3 rounded-lg border border-warning/40 bg-warning/10 p-4 text-sm" role="status">
              <p className="font-medium">{mode === "open" ? "접근 보호가 꺼져 있습니다" : "접근 보호 복구 필요"}</p>
              <p className="leading-6 text-muted-foreground">{mode === "open" ? "접근 보호가 꺼진 경우 로그인 없이 콘솔로 이동합니다." : "보호 설정을 읽을 수 없어 콘솔 접근을 차단했습니다. 서버의 접근 보호 설정과 저장소 상태를 확인한 뒤 다시 시도하세요."}</p>
            </div>
          ) : (
            <form onSubmit={submit} className="space-y-3">
              <input name="username" autoComplete="username" className="sr-only" tabIndex={-1} aria-hidden="true" />
              <Input
                type="password"
                name="password"
                autoComplete="current-password"
                autoFocus
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="비밀번호"
                aria-label="비밀번호"
              />
              {error && <p className="text-sm text-destructive" role="alert" style={{ wordBreak: "keep-all" }}>{error}</p>}
              <Button type="submit" className="min-h-11 w-full" disabled={busy}>{busy ? "확인 중…" : "로그인"}</Button>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
