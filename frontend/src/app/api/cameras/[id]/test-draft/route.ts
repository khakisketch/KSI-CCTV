import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requestActor, requireRole } from "@/lib/require-role";

export async function POST(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = await requireRole(req, "operator");
  if (denied) return denied;
  const { id } = await context.params;
  const body = await req.json().catch(() => ({}));
  const actor = await requestActor(req);
  const r = await engineWrite(
    `/api/cameras/${encodeURIComponent(id)}/test-draft`,
    body,
    "POST",
    20_000,
    actor,
  );
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, { status: r.status || 502 });
}
