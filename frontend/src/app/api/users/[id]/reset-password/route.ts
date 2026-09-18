import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requestActor, requireRole } from "@/lib/require-role";
import { findUserById, resetUserPassword } from "@/lib/users";

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

// 관리자 전용 비밀번호 재설정. body {password}.
export async function POST(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const { id } = await context.params;
  const body = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  const password = typeof body?.password === "string" ? body.password : "";
  const result = await resetUserPassword(id, password);
  if (result === "updated") {
    const found = await findUserById(id);
    if (found && found !== "error") {
      const actor = await requestActor(req);
      await auditUserEvent("user_password_reset", { username: found.username }, actor);
    }
    return NextResponse.json({ detail: "비밀번호를 재설정했습니다." }, { headers: NO_STORE });
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
    { detail: "비밀번호를 확인하세요. (12자 이상, 앞뒤 공백 불가)" },
    { status: 400, headers: NO_STORE },
  );
}
