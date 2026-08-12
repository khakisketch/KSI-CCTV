import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";

export async function POST(req: NextRequest, context: { params: Promise<{ eventId: string }> }) {
  const { eventId } = await context.params;
  const body = await req.json().catch(() => ({}));
  const response = await engineWrite(
    `/api/incidents/${encodeURIComponent(eventId)}/actions`,
    body,
  );
  return NextResponse.json(response.data ?? { detail: "엔진 연결 실패" }, {
    status: response.ok ? 200 : response.status || 502,
  });
}
