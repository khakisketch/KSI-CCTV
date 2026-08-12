import { NextRequest, NextResponse } from "next/server";

import { engineWrite } from "@/lib/engine";

const ALLOWED = new Set(["test", "apply", "enable", "disable"]);

export async function POST(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const body = (await req.json().catch(() => ({}))) as { action?: string; expected_revision?: number };
  if (!body.action || !ALLOWED.has(body.action)) {
    return NextResponse.json({ detail: "지원하지 않는 카메라 작업입니다." }, { status: 400 });
  }
  const revision = Number.isInteger(body.expected_revision)
    ? `?expected_revision=${body.expected_revision}`
    : "";
  const r = await engineWrite(
    `/api/cameras/${encodeURIComponent(id)}/${body.action}${revision}`,
    {},
  );
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, { status: r.ok ? 200 : r.status || 502 });
}
