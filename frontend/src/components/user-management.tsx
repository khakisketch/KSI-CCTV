"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export type ManagedUser = {
  id: string;
  username: string;
  role: "operator" | "viewer";
  enabled: boolean;
  createdAt: number;
  updatedAt: number;
};

const ROLE_LABEL: Record<ManagedUser["role"], string> = {
  operator: "관제원",
  viewer: "뷰어",
};

const SELECT_CLASS =
  "h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base outline-none transition-colors focus-visible:border-ring disabled:pointer-events-none disabled:opacity-50 md:text-sm";

type ApiUserPayload = { user?: ManagedUser; detail?: string };

async function readPayload(response: Response): Promise<ApiUserPayload> {
  return (await response.json().catch(() => ({}))) as ApiUserPayload;
}

// 관리자 전용 사용자 관리 — 목록·생성·역할 변경·활성 전환·비밀번호 재설정·삭제.
// 모든 변경은 서버 라우트(`/api/users/*`, admin 게이트) 경유.
export function UserManagement({ initialUsers }: { initialUsers: ManagedUser[] }) {
  const router = useRouter();
  const [users, setUsers] = useState(initialUsers);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [resetPasswords, setResetPasswords] = useState<Record<string, string>>({});

  async function runAction(key: string, action: () => Promise<string | null>) {
    setBusy(key);
    setMessage("");
    try {
      const notice = await action();
      if (notice) setMessage(notice);
      router.refresh();
    } catch {
      setMessage("서버에 연결할 수 없습니다. 네트워크 연결을 확인한 뒤 다시 시도하세요.");
    } finally {
      setBusy(null);
    }
  }

  async function submitCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const username = String(form.get("username") ?? "");
    const role = String(form.get("role") ?? "");
    const password = String(form.get("password") ?? "");
    if (!username) {
      setMessage("사용자 이름을 입력하세요.");
      return;
    }
    if (!password) {
      setMessage("비밀번호를 입력하세요.");
      return;
    }
    setBusy("create");
    setMessage("");
    try {
      const response = await fetch("/api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, role, password }),
      });
      const data = await readPayload(response);
      if (!response.ok || !data.user) {
        setMessage(data.detail ?? "사용자를 추가하지 못했습니다.");
        return;
      }
      setUsers((prev) => [...prev, data.user as ManagedUser]);
      setMessage(`사용자 ${data.user.username}을(를) 추가했습니다.`);
      formElement.reset();
      router.refresh();
    } catch {
      setMessage("서버에 연결할 수 없습니다. 네트워크 연결을 확인한 뒤 다시 시도하세요.");
    } finally {
      setBusy(null);
    }
  }

  function changeRole(target: ManagedUser, role: string) {
    if (role !== "operator" && role !== "viewer") return;
    if (role === target.role) return;
    void runAction(`role-${target.id}`, async () => {
      const response = await fetch(`/api/users/${target.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role }),
      });
      const data = await readPayload(response);
      if (!response.ok || !data.user) return data.detail ?? "역할을 변경하지 못했습니다.";
      setUsers((prev) => prev.map((user) => (user.id === target.id ? data.user as ManagedUser : user)));
      return `사용자 ${target.username}의 역할을 ${ROLE_LABEL[data.user.role]}(으)로 변경했습니다.`;
    });
  }

  function toggleEnabled(target: ManagedUser) {
    void runAction(`enabled-${target.id}`, async () => {
      const response = await fetch(`/api/users/${target.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !target.enabled }),
      });
      const data = await readPayload(response);
      if (!response.ok || !data.user) return data.detail ?? "상태를 변경하지 못했습니다.";
      setUsers((prev) => prev.map((user) => (user.id === target.id ? data.user as ManagedUser : user)));
      return data.user.enabled
        ? `사용자 ${target.username}을(를) 다시 활성화했습니다.`
        : `사용자 ${target.username}을(를) 비활성화했습니다.`;
    });
  }

  function resetPassword(target: ManagedUser) {
    const password = resetPasswords[target.id] ?? "";
    if (!password) {
      setMessage("재설정할 비밀번호를 입력하세요.");
      return;
    }
    void runAction(`reset-${target.id}`, async () => {
      const response = await fetch(`/api/users/${target.id}/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      const data = await readPayload(response);
      if (!response.ok) return data.detail ?? "비밀번호를 재설정하지 못했습니다.";
      setResetPasswords((prev) => ({ ...prev, [target.id]: "" }));
      return `사용자 ${target.username}의 비밀번호를 재설정했습니다.`;
    });
  }

  function removeUser(target: ManagedUser) {
    if (!window.confirm(`사용자 ${target.username}을(를) 삭제하시겠습니까?`)) return;
    void runAction(`delete-${target.id}`, async () => {
      const response = await fetch(`/api/users/${target.id}`, { method: "DELETE" });
      const data = await readPayload(response);
      if (!response.ok) return data.detail ?? "사용자를 삭제하지 못했습니다.";
      setUsers((prev) => prev.filter((user) => user.id !== target.id));
      return `사용자 ${target.username}을(를) 삭제했습니다.`;
    });
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-base">사용자 관리</CardTitle>
          <Badge variant="outline">관리자 전용</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={submitCreate} className="grid gap-2 sm:grid-cols-[1fr_140px_1fr_auto]">
          <Input className="min-h-11" name="username" autoComplete="off" placeholder="새 사용자 이름" aria-label="새 사용자 이름" />
          <select className={SELECT_CLASS} name="role" defaultValue="operator" aria-label="새 사용자 역할">
            <option value="operator">관제원</option>
            <option value="viewer">뷰어</option>
          </select>
          <Input className="min-h-11" name="password" type="password" autoComplete="new-password" placeholder="새 사용자 비밀번호 (12자 이상)" aria-label="새 사용자 비밀번호" />
          <Button className="min-h-11" type="submit" disabled={busy !== null}>
            {busy === "create" ? "추가 중…" : "사용자 추가"}
          </Button>
        </form>

        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th scope="col" className="py-2 pr-2 font-medium">사용자명</th>
              <th scope="col" className="py-2 pr-2 font-medium">역할</th>
              <th scope="col" className="py-2 pr-2 font-medium">상태</th>
              <th scope="col" className="py-2 pr-2 font-medium">생성일</th>
              <th scope="col" className="py-2 font-medium">관리</th>
            </tr>
          </thead>
          <tbody>
            {users.map((target) => (
              <tr key={target.id} className="border-b last:border-0">
                <td className="py-2 pr-2 font-medium">{target.username}</td>
                <td className="py-2 pr-2">
                  <select
                    className={SELECT_CLASS}
                    value={target.role}
                    disabled={busy !== null}
                    onChange={(event) => changeRole(target, event.target.value)}
                    aria-label={`역할 변경: ${target.username}`}
                  >
                    <option value="operator">관제원</option>
                    <option value="viewer">뷰어</option>
                  </select>
                </td>
                <td className="py-2 pr-2">
                  <Badge variant={target.enabled ? "success" : "secondary"}>
                    {target.enabled ? "활성" : "비활성"}
                  </Badge>
                </td>
                <td className="py-2 pr-2 tabular-nums">
                  {new Date(target.createdAt).toLocaleDateString("ko-KR")}
                </td>
                <td className="py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy !== null}
                      onClick={() => toggleEnabled(target)}
                      aria-label={`${target.enabled ? "비활성화" : "재활성화"}: ${target.username}`}
                    >
                      {busy === `enabled-${target.id}` ? "처리 중…" : target.enabled ? "비활성화" : "재활성화"}
                    </Button>
                    <div className="flex items-center gap-1">
                      <Input
                        className="h-8 w-32"
                        type="password"
                        autoComplete="new-password"
                        placeholder="새 비밀번호"
                        aria-label={`새 비밀번호: ${target.username}`}
                        value={resetPasswords[target.id] ?? ""}
                        onChange={(event) => setResetPasswords((prev) => ({ ...prev, [target.id]: event.target.value }))}
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy !== null}
                        onClick={() => resetPassword(target)}
                        aria-label={`비밀번호 재설정: ${target.username}`}
                      >
                        {busy === `reset-${target.id}` ? "처리 중…" : "재설정"}
                      </Button>
                    </div>
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy !== null}
                      onClick={() => removeUser(target)}
                      aria-label={`삭제: ${target.username}`}
                    >
                      삭제
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {users.length === 0 && (
          <p className="py-4 text-center text-sm text-muted-foreground">등록된 관제원·뷰어 계정이 없습니다.</p>
        )}

        {message && <p role="status" className="text-sm" style={{ wordBreak: "keep-all" }}>{message}</p>}
      </CardContent>
    </Card>
  );
}
