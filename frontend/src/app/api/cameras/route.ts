import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requestActor, requireRole } from "@/lib/require-role";

// The API key is attached only here, inside the Next server process.
export async function POST(req: NextRequest) {
  const denied = await requireRole(req, "operator");
  if (denied) return denied;
  const body = await req.json().catch(() => ({}));
  const actor = await requestActor(req);
  const r = await engineWrite("/api/cameras", body, "POST", 8000, actor);
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, { status: r.ok ? 201 : r.status || 502 });
}
