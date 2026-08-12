import { NextRequest, NextResponse } from "next/server";

// 캡처 이미지 same-origin 프록시. <img>는 커스텀 헤더(X-API-Key)를 못 보내므로 서버가 키를
// 부착해 엔진 /api/captures/{date}/{camera}/{name}에서 받아 그대로 흘려보낸다. .enc 복호화·경로
// 검증은 엔진이 수행한다. API 키 미노출, 세션 게이트(middleware) 뒤.

export const dynamic = "force-dynamic";

const ENGINE = (process.env.KSI_CCTV_ENGINE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const KEY = process.env.KSI_CCTV_API_KEY ?? "";

export async function GET(_req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const target = path.map(encodeURIComponent).join("/");
  let upstream: Response;
  try {
    upstream = await fetch(`${ENGINE}/api/captures/${target}`, {
      headers: { "X-API-Key": KEY },
      cache: "no-store",
    });
  } catch {
    return new NextResponse(null, { status: 502 });
  }
  if (!upstream.ok || !upstream.body) {
    return new NextResponse(null, { status: upstream.status || 404 });
  }
  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "image/jpeg",
      "cache-control": "no-store",
    },
  });
}
