import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";

export async function PUT(req: NextRequest, ctx: { params: Promise<{ name: string }> }) {
  const { name } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const r = await engineWrite(`/api/models/${encodeURIComponent(name)}/enabled`, body, "PUT");
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, {
    status: r.ok ? 200 : r.status || 502,
  });
}
