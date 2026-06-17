import { NextRequest, NextResponse } from "next/server";

import { engineGet, engineWrite } from "@/lib/engine";

// 파이프라인 제어 프록시. 브라우저 → 이 라우트(서버, 키 부착) → 엔진 /api/control/{status,start,stop}.
// 제어는 데몬(src/run.py)에 mount된 파이프라인에서만 실제 동작(standalone API는 connected=false/503).
// 세션 게이트(middleware) 뒤, API 키 미노출.

export async function GET(_req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (action !== "status") {
    return NextResponse.json({ detail: "허용되지 않은 액션" }, { status: 405 });
  }
  const data = await engineGet<{ connected: boolean; running: boolean }>("/api/control/status");
  return NextResponse.json(data ?? { connected: false, running: false });
}

export async function POST(_req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (action !== "start" && action !== "stop") {
    return NextResponse.json({ detail: "허용되지 않은 액션" }, { status: 405 });
  }
  const r = await engineWrite(`/api/control/${action}`, {}, "POST");
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, {
    status: r.ok ? 200 : r.status || 502,
  });
}
