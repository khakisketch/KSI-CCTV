import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";

export async function PUT(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const result = await engineWrite(`/api/commissioning/cameras/${encodeURIComponent(id)}`, await req.json().catch(() => ({})), "PUT");
  return NextResponse.json(result.data ?? { detail: "엔진 연결 실패" }, { status: result.ok ? 200 : result.status || 502 });
}
