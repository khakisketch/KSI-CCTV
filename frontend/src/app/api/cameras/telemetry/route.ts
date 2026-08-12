import { NextResponse } from "next/server";

import { engineGet } from "@/lib/engine";

// 카메라 라이브 텔레메트리 프록시(서버 키 부착). 세션 게이트 뒤.
export const dynamic = "force-dynamic";

export async function GET() {
  const data = await engineGet("/api/cameras/telemetry");
  // 엔진 미응답도 UI가 source-level unavailable로 구분할 수 있는 동일 계약을 반환한다.
  return NextResponse.json(data ?? {
    connected: false,
    telemetry_status: "unavailable",
    generated_at: new Date().toISOString(),
    cameras: {},
  });
}
