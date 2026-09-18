import { NextRequest, NextResponse } from "next/server";

import { engineGet } from "@/lib/engine";
import { requireRole } from "@/lib/require-role";

// 시스템 상태 프록시(서버사이드 키 부착). 세션 게이트(middleware) 뒤.
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const data = await engineGet("/api/system");
  return NextResponse.json(data ?? { metrics: {}, health: { components: [] } });
}
