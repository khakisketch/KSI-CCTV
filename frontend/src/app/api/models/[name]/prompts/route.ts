import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requestActor, requireRole } from "@/lib/require-role";

// 브라우저 → 이 라우트(서버) → 엔진 API. API 키는 서버에만 존재한다.
export async function POST(req: NextRequest, ctx: { params: Promise<{ name: string }> }) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const { name } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const actor = await requestActor(req);
  const r = await engineWrite(`/api/models/${encodeURIComponent(name)}/prompts`, body, "POST", 8000, actor);
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, {
    status: r.ok ? 200 : r.status || 502,
  });
}
