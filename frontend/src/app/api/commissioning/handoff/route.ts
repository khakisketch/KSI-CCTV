import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";

export async function PUT(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  const result = await engineWrite("/api/commissioning/handoff", body, "PUT");
  return NextResponse.json(
    result.data ?? { detail: "인수 확인을 저장하지 못했습니다." },
    { status: result.status || 502 },
  );
}
