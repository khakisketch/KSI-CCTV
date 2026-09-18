import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requireRole } from "@/lib/require-role";

export async function PUT(req: NextRequest) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const result = await engineWrite("/api/commissioning/site", await req.json().catch(() => ({})), "PUT");
  return NextResponse.json(result.data ?? { detail: "엔진 연결 실패" }, { status: result.ok ? 200 : result.status || 502 });
}
