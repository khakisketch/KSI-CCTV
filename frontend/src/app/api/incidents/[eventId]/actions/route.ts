import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requestActor, requireRole } from "@/lib/require-role";

export async function POST(req: NextRequest, context: { params: Promise<{ eventId: string }> }) {
  const denied = await requireRole(req, "operator");
  if (denied) return denied;
  const { eventId } = await context.params;
  const body = await req.json().catch(() => ({}));
  const actor = await requestActor(req);
  const response = await engineWrite(
    `/api/incidents/${encodeURIComponent(eventId)}/actions`,
    body,
    "POST",
    8000,
    actor,
  );
  return NextResponse.json(response.data ?? { detail: "엔진 연결 실패" }, {
    status: response.ok ? 200 : response.status || 502,
  });
}
