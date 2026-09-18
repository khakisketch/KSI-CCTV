import { NextRequest, NextResponse } from "next/server";

import { SESSION_COOKIE, sessionPrincipal } from "./auth";
import { Role, roleAtLeast } from "./roles";
import { findUserById } from "./users";

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

/**
 * 엔진 쓰기 호출용 행위 주체 해석(RBAC R5b). `X-KSI-Actor` 헤더 값으로 사용한다.
 * admin/레거시 세션 → "admin", 개인 세션 → users.json의 username,
 * 미인증·비활성·손상 → "system".
 */
export async function requestActor(req: NextRequest): Promise<string> {
  const principal = await sessionPrincipal(req.cookies.get(SESSION_COOKIE)?.value);
  if (!principal) return "system";
  if (principal.userId === undefined) return "admin";
  const user = await findUserById(principal.userId);
  if (!user || user === "error" || !user.enabled) return "system";
  return user.username;
}
