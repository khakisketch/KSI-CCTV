import { NextRequest, NextResponse } from "next/server";

import { SESSION_COOKIE, sessionPrincipal } from "./auth";
import { Role, roleAtLeast } from "./roles";

/**
 * 변경 핸들러용 2차 방어심층 게이트(RBAC R4). 1차 게이트는 proxy.ts.
 * 세션 쿠키 → `sessionPrincipal`로 매 요청 재해석한다.
 * 미인증 → 401 JSON, 역할 미달 → 403 JSON, 충족 → null(계속 진행).
 */
export async function requireRole(req: NextRequest, min: Role): Promise<NextResponse | null> {
  const principal = await sessionPrincipal(req.cookies.get(SESSION_COOKIE)?.value);
  if (!principal) {
    return NextResponse.json(
      { detail: "인증이 필요합니다." },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }
  if (!roleAtLeast(principal.role, min)) {
    return NextResponse.json(
      { detail: "이 리소스에 접근할 권한이 없습니다." },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }
  return null;
}
