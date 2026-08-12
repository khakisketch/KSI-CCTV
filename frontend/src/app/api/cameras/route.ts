import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";

// The API key is attached only here, inside the Next server process.
export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  const r = await engineWrite("/api/cameras", body);
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, { status: r.ok ? 201 : r.status || 502 });
}
