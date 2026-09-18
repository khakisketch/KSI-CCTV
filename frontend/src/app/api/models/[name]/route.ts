import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requireRole } from "@/lib/require-role";

// 브라우저 → 이 라우트(서버) → 엔진 DELETE /api/models/{name}. API 키는 서버에만.
// 엔진이 업로드 모델만 삭제 허용(기본 모델 보호). 세션 게이트(middleware) 뒤.
export async function DELETE(req: NextRequest, ctx: { params: Promise<{ name: string }> }) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const { name } = await ctx.params;
  const r = await engineWrite(`/api/models/${encodeURIComponent(name)}`, null, "DELETE");
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, {
    status: r.ok ? 200 : r.status || 502,
  });
}
