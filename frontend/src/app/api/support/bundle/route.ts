import { NextRequest, NextResponse } from "next/server";

import { requestActor, requireRole } from "@/lib/require-role";

const ENGINE = (process.env.KSI_CCTV_ENGINE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const KEY = process.env.KSI_CCTV_API_KEY ?? "";

export async function GET(req: NextRequest) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const actor = await requestActor(req);
  let response: Response | null = null;
  try {
    response = await fetch(`${ENGINE}/api/support/bundle`, {
      headers: {
        ...(KEY ? { "X-API-Key": KEY } : {}),
        "X-KSI-Actor": actor,
      },
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
  } catch {
    response = null;
  }
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
