import { NextRequest, NextResponse } from "next/server";

import { engineFetch } from "@/lib/engine";

// The browser only receives a same-origin image; engine URL and API key stay server-side.
export async function GET(_req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!/^[A-Za-z0-9._-]+$/.test(id)) {
    return NextResponse.json({ detail: "잘못된 카메라 id" }, { status: 400 });
  }

  const response = await engineFetch(`/api/cameras/${encodeURIComponent(id)}/snapshot`);
  const contentType = response?.headers.get("content-type") ?? "";
  if (!response) {
    return NextResponse.json({ detail: "현재 카메라 스냅샷을 사용할 수 없습니다." }, { status: 503 });
  }
  if (!response.ok) {
    return NextResponse.json(
      { detail: "현재 카메라 스냅샷을 사용할 수 없습니다." },
      { status: response.status },
    );
  }
  if (!contentType.startsWith("image/")) {
    return NextResponse.json({ detail: "잘못된 스냅샷 응답입니다." }, { status: 502 });
  }

  return new NextResponse(await response.arrayBuffer(), {
    headers: { "Content-Type": contentType, "Cache-Control": "no-store" },
  });
}
