import { NextRequest, NextResponse } from "next/server";

import { requireRole } from "@/lib/require-role";
import { resetUserPassword } from "@/lib/users";

const NO_STORE = { "Cache-Control": "no-store" };

// 관리자 전용 비밀번호 재설정. body {password}.
export async function POST(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const { id } = await context.params;
  const body = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  const password = typeof body?.password === "string" ? body.password : "";
  const result = await resetUserPassword(id, password);
  if (result === "updated") {
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
