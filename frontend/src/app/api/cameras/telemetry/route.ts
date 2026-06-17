import { NextResponse } from "next/server";

import { engineGet } from "@/lib/engine";

// 카메라 라이브 텔레메트리 프록시(서버 키 부착). 세션 게이트 뒤.
export const dynamic = "force-dynamic";

export async function GET() {
  const data = await engineGet("/api/cameras/telemetry");
  return NextResponse.json(data ?? { connected: false, cameras: {} });
}
