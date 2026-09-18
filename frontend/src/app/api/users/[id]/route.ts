import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requestActor, requireRole } from "@/lib/require-role";
import { deleteUser, findUserById, updateUser, type User } from "@/lib/users";

const NO_STORE = { "Cache-Control": "no-store" };

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

function toPublicUser(user: User) {
  return {
    id: user.id,
    username: user.username,
    role: user.role,
    enabled: user.enabled,
    createdAt: user.createdAt,
    updatedAt: user.updatedAt,
  };
}

// 관리자 전용 역할·활성 상태 변경. body {role?, enabled?}.
export async function PATCH(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const { id } = await context.params;
  const body = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  const patch: { role?: string; enabled?: boolean } = {};
  if (body?.role !== undefined) patch.role = typeof body.role === "string" ? body.role : "";
  if (body?.enabled !== undefined) patch.enabled = body.enabled as boolean;
  const result = await updateUser(id, patch);
  if (result === "updated") {
    const found = await findUserById(id);
    if (!found || found === "error") {
      return NextResponse.json(
        { detail: "사용자 저장소에 접근할 수 없습니다. 잠시 후 다시 시도하세요." },
        { status: 409, headers: NO_STORE },
      );
    }
    const changes: { role?: string; enabled?: boolean } = {};
    if (body?.role !== undefined) changes.role = found.role;
    if (body?.enabled !== undefined) changes.enabled = found.enabled;
    const actor = await requestActor(req);
    await auditUserEvent("user_updated", { username: found.username, changes }, actor);
    return NextResponse.json({ user: toPublicUser(found) }, { headers: NO_STORE });
  }
  if (result === "missing") {
    return NextResponse.json({ detail: "사용자를 찾을 수 없습니다." }, { status: 404, headers: NO_STORE });
  }
  if (result === "conflict") {
    return NextResponse.json(
      { detail: "사용자 저장소에 접근할 수 없습니다. 잠시 후 다시 시도하세요." },
      { status: 409, headers: NO_STORE },
    );
  }
  return NextResponse.json(
    { detail: "역할 또는 활성화 상태를 확인하세요." },
    { status: 400, headers: NO_STORE },
  );
}

// 관리자 전용 사용자 삭제.
export async function DELETE(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const { id } = await context.params;
  const existing = await findUserById(id);
  const existingUsername = existing && existing !== "error" ? existing.username : undefined;
  const result = await deleteUser(id);
  if (result === "deleted") {
    if (existingUsername) {
      const actor = await requestActor(req);
      await auditUserEvent("user_deleted", { username: existingUsername }, actor);
    }
    return NextResponse.json({ detail: "사용자를 삭제했습니다." }, { headers: NO_STORE });
  }
  if (result === "missing") {
    return NextResponse.json({ detail: "사용자를 찾을 수 없습니다." }, { status: 404, headers: NO_STORE });
  }
  return NextResponse.json(
    { detail: "사용자 저장소에 접근할 수 없습니다. 잠시 후 다시 시도하세요." },
    { status: 409, headers: NO_STORE },
  );
}
