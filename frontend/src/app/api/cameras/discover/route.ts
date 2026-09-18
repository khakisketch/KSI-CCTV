import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requireRole } from "@/lib/require-role";

// Discovery runs from the engine network, never from the installer browser.
export async function POST(req: NextRequest) {
  const denied = await requireRole(req, "operator");
  if (denied) return denied;
  const body = await req.json().catch(() => ({}));
  const r = await engineWrite("/api/cameras/discover", body);
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, { status: r.ok ? 200 : r.status || 502 });
}
