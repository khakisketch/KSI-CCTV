import { NextResponse } from "next/server";

import { engineFetch } from "@/lib/engine";

export async function GET() {
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
