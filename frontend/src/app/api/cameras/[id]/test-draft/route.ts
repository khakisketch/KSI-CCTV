import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";

export async function POST(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const body = await req.json().catch(() => ({}));
  const r = await engineWrite(
    `/api/cameras/${encodeURIComponent(id)}/test-draft`,
    body,
    "POST",
    20_000,
  );
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, { status: r.status || 502 });
}
