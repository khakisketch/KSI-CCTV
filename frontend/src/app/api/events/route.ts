import { NextRequest, NextResponse } from "next/server";

import { engineGet } from "@/lib/engine";

export async function GET(request: NextRequest) {
  const response = await engineGet<Record<string, unknown>>(
    `/api/events${request.nextUrl.search}`,
    { includeStatus: true },
  );
  return NextResponse.json(response.data ?? { detail: "엔진 연결 실패" }, {
    status: response.ok ? 200 : response.status || 502,
  });
}
