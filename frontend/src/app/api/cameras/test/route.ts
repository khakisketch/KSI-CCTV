import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  // Some cameras do not emit an IDR frame immediately. Keep ordinary writes
  // bounded at 8s, but allow first-frame validation enough time for a slow GOP.
  const r = await engineWrite("/api/cameras/test", body, "POST", 20_000);
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, { status: r.ok ? 200 : r.status || 502 });
}
