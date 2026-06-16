"use client";

import { ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function LoginPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        router.push("/");
        router.refresh();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.detail ?? "로그인 실패");
      }
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
            관제 대시보드 접근을 위해 비밀번호를 입력하세요.
          </p>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-3">
            <Input
              type="password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="비밀번호"
              aria-label="비밀번호"
            />
            {error && (
              <p className="text-sm text-destructive" style={{ wordBreak: "keep-all" }}>
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={busy || !password}>
              {busy ? "확인 중…" : "로그인"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
