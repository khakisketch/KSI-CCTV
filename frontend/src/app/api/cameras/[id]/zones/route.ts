import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";
import { requireRole } from "@/lib/require-role";

export async function PUT(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = await requireRole(req, "operator");
  if (denied) return denied;
  const { id } = await context.params;
  if (!/^[A-Za-z0-9._-]+$/.test(id)) {
    return NextResponse.json({ detail: "잘못된 카메라 id" }, { status: 400 });
  }
  const body = await req.json().catch(() => ({}));
  const result = await engineWrite(`/api/cameras/${encodeURIComponent(id)}/zones`, body, "PUT");
  return NextResponse.json(result.data ?? { detail: "엔진 연결 실패" }, {
    status: result.ok ? 200 : result.status || 502,
  });
}
