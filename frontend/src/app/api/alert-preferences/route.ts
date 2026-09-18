import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requestActor, requireRole } from "@/lib/require-role";

// 알림 환경설정 저장 프록시. 브라우저 → 이 라우트(서버, 키 부착) → 엔진 PUT /api/alert-preferences.
// 세션 게이트(middleware) 뒤, API 키 미노출.
export async function PUT(req: NextRequest) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const body = await req.json().catch(() => ({}));
  const actor = await requestActor(req);
  const r = await engineWrite("/api/alert-preferences", body, "PUT", 8000, actor);
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, {
    status: r.ok ? 200 : r.status || 502,
  });
}
