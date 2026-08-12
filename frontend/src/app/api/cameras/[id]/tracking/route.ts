import { NextResponse } from "next/server";

import { engineGet } from "@/lib/engine";

export const dynamic = "force-dynamic";

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!/^[A-Za-z0-9._-]+$/.test(id)) {
    return NextResponse.json({ detail: "잘못된 카메라 id" }, { status: 400 });
  }
  const result = await engineGet<{ detail?: string }>(
    `/api/cameras/${encodeURIComponent(id)}/tracking`,
    { includeStatus: true },
  );
  return NextResponse.json(result.data ?? { detail: "Tracking 정보를 가져오지 못했습니다." }, {
    status: result.status || 502,
  });
}
