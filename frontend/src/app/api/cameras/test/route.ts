import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requestActor, requireRole } from "@/lib/require-role";

export async function POST(req: NextRequest) {
  const denied = await requireRole(req, "operator");
  if (denied) return denied;
  const body = await req.json().catch(() => ({}));
  // Some cameras do not emit an IDR frame immediately. Keep ordinary writes
  // bounded at 8s, but allow first-frame validation enough time for a slow GOP.
  const actor = await requestActor(req);
  const r = await engineWrite("/api/cameras/test", body, "POST", 20_000, actor);
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, { status: r.ok ? 200 : r.status || 502 });
}
