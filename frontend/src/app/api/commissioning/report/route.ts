import { NextRequest, NextResponse } from "next/server";

import { engineGet } from "@/lib/engine";
import { requireRole } from "@/lib/require-role";

export async function GET(req: NextRequest) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const result = await engineGet("/api/commissioning/report", { includeStatus: true });
  return NextResponse.json(result.data ?? { detail: "엔진 연결 실패" }, {
    status: result.ok ? result.status : result.status || 502,
    headers: { "Cache-Control": "private, no-store" },
  });
}
