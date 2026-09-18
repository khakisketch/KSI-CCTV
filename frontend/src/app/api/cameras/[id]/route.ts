import { NextRequest, NextResponse } from "next/server";

import { engineGet, engineWrite } from "@/lib/engine";
import { requestActor, requireRole } from "@/lib/require-role";

function revisionQuery(req: NextRequest): string {
  const expectedRevision = req.nextUrl.searchParams.get("expected_revision");
  return expectedRevision && /^\d+$/.test(expectedRevision)
    ? `?expected_revision=${expectedRevision}`
    : "";
}

export async function GET(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const r = await engineGet(`/api/cameras/${encodeURIComponent(id)}`, { includeStatus: true });
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, { status: r.status || 502 });
}

export async function PUT(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = await requireRole(req, "operator");
  if (denied) return denied;
  const { id } = await context.params;
  const body = await req.json().catch(() => ({}));
  const actor = await requestActor(req);
  const r = await engineWrite(`/api/cameras/${encodeURIComponent(id)}${revisionQuery(req)}`, body, "PUT", 8000, actor);
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, { status: r.status || 502 });
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = await requireRole(req, "operator");
  if (denied) return denied;
  const { id } = await context.params;
  const actor = await requestActor(req);
  const r = await engineWrite(`/api/cameras/${encodeURIComponent(id)}${revisionQuery(req)}`, {}, "DELETE", 8000, actor);
  return NextResponse.json(r.data ?? { detail: "엔진 연결 실패" }, { status: r.status || 502 });
}
