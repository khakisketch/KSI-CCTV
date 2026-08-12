import { NextResponse } from "next/server";

import { engineGet } from "@/lib/engine";

export async function GET() {
  const result = await engineGet("/api/video-sources/capabilities", { includeStatus: true });
  return NextResponse.json(result.data ?? { detail: "엔진 연결 실패" }, { status: result.ok ? result.status : result.status || 502 });
}
