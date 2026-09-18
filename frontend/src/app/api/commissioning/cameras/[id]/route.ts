import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requestActor, requireRole } from "@/lib/require-role";

export async function PUT(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const { id } = await context.params;
  const actor = await requestActor(req);
  const result = await engineWrite(`/api/commissioning/cameras/${encodeURIComponent(id)}`, await req.json().catch(() => ({})), "PUT", 8000, actor);
  return NextResponse.json(result.data ?? { detail: "엔진 연결 실패" }, { status: result.ok ? 200 : result.status || 502 });
}
