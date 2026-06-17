import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";

// 카메라 탐지 프로파일 저장 프록시(서버 키 부착). 세션 게이트 뒤.
export async function PUT(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const r = await engineWrite(`/api/cameras/${encodeURIComponent(id)}/profile`, body, "PUT");
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, {
    status: r.ok ? 200 : r.status || 502,
  });
}
