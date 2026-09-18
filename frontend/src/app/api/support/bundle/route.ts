import { NextRequest, NextResponse } from "next/server";

import { engineFetch } from "@/lib/engine";
import { requireRole } from "@/lib/require-role";

export async function GET(req: NextRequest) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const response = await engineFetch("/api/support/bundle");
  if (!response?.ok || !response.body) {
    return NextResponse.json(
      { detail: "지원 자료를 생성하지 못했습니다." },
      { status: response?.status || 502 },
    );
  }
  return new NextResponse(response.body, {
    status: 200,
    headers: {
      "content-type": "application/zip",
      "content-disposition": "attachment; filename=ksi-cctv-support.zip",
      "cache-control": "no-store",
    },
  });
}
