import { NextResponse } from "next/server";

import { engineGet } from "@/lib/engine";

export async function GET(_request: Request, context: { params: Promise<{ eventId: string }> }) {
  const { eventId } = await context.params;
  const response = await engineGet<Record<string, unknown>>(
    `/api/incidents/${encodeURIComponent(eventId)}`,
    { includeStatus: true },
  );
  return NextResponse.json(response.data ?? { detail: "엔진 연결 실패" }, {
    status: response.ok ? 200 : response.status || 502,
  });
}
