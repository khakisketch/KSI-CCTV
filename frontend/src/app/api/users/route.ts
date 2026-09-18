import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requestActor, requireRole } from "@/lib/require-role";
import { createUser, findUserByUsername, listUsers, type User } from "@/lib/users";

const NO_STORE = { "Cache-Control": "no-store" };

export type PublicUser = {
  id: string;
  username: string;
  role: "operator" | "viewer";
  enabled: boolean;
  createdAt: number;
  updatedAt: number;
};

export function toPublicUser(user: User): PublicUser {
  return {
    id: user.id,
    username: user.username,
    role: user.role,
    enabled: user.enabled,
    createdAt: user.createdAt,
    updatedAt: user.updatedAt,
  };
}

// 사용자 관리 감사 — best-effort: 실패해도 사용자 작업을 막지 않되 실패를 경고 로그로 남긴다.
async function auditUserEvent(
  event: string,
  fields: Record<string, unknown>,
  actor: string,
): Promise<void> {
  try {
    const result = await engineWrite("/api/audit/entry", { event, fields }, "POST", 8000, actor);
    if (!result.ok) {
      console.warn(`[audit] ${event} failed: status=${result.status}`);
    }
  } catch (error) {
    // 감사 실패 무시 — 기존 응답 유지 (민감정보 미기록: 이벤트명·사유만)
    console.warn(`[audit] ${event} failed: error=${error instanceof Error ? error.message : "unknown"}`);
  }
}

// 관리자 전용 사용자 목록. 비밀번호 해시·솔트는 절대 응답에 포함하지 않는다.
export async function GET(req: NextRequest) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const users = (await listUsers()).map(toPublicUser);
  return NextResponse.json({ users }, { headers: NO_STORE });
}

// 관리자 전용 사용자 생성. body {username, role, password}.
export async function POST(req: NextRequest) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const body = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  const username = typeof body?.username === "string" ? body.username : "";
  const role = typeof body?.role === "string" ? body.role : "";
  const password = typeof body?.password === "string" ? body.password : "";
  const result = await createUser(username, role, password);
  if (result === "created") {
    const found = await findUserByUsername(username);
    if (!found || found === "error") {
      return NextResponse.json(
        { detail: "사용자 저장소에 접근할 수 없습니다. 잠시 후 다시 시도하세요." },
        { status: 409, headers: NO_STORE },
      );
    }
    const actor = await requestActor(req);
    await auditUserEvent("user_created", { username: found.username, role: found.role }, actor);
    return NextResponse.json({ user: toPublicUser(found) }, { status: 201, headers: NO_STORE });
  }
  if (result === "exists") {
    return NextResponse.json(
      { detail: "이미 존재하는 사용자 이름입니다." },
      { status: 409, headers: NO_STORE },
    );
  }
  if (result === "conflict") {
    return NextResponse.json(
      { detail: "사용자 저장소에 접근할 수 없습니다. 잠시 후 다시 시도하세요." },
      { status: 409, headers: NO_STORE },
    );
  }
  return NextResponse.json(
    { detail: "사용자 이름·역할·비밀번호를 확인하세요." },
    { status: 400, headers: NO_STORE },
  );
}
