import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requireRole } from "@/lib/require-role";

export async function POST(req: NextRequest, context: { params: Promise<{ action: string }> }) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const { action } = await context.params;
  if (action !== "complete" && action !== "reopen") return NextResponse.json({ detail: "찾을 수 없는 작업" }, { status: 404 });
  const result = await engineWrite(`/api/commissioning/${action}`, await req.json().catch(() => ({})));
  return NextResponse.json(result.data ?? { detail: "엔진 연결 실패" }, { status: result.ok ? 200 : result.status || 502 });
}
