import { NextRequest, NextResponse } from "next/server";

// 이벤트 SSE same-origin 프록시. 브라우저(EventSource) → 이 라우트(서버) → 엔진 /api/events/stream.
// EventSource는 커스텀 헤더(X-API-Key)를 못 보내므로, 서버에서 키를 붙여 스트림을 그대로 흘려보낸다.
// API 키는 서버 전용(브라우저 미노출). 세션 게이트(middleware) 뒤.

export const dynamic = "force-dynamic";

const ENGINE = (process.env.KSI_CCTV_ENGINE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const KEY = process.env.KSI_CCTV_API_KEY ?? "";

export async function GET(req: NextRequest) {
  let upstream: Response;
  try {
    upstream = await fetch(`${ENGINE}/api/events/stream`, {
      headers: { "X-API-Key": KEY, Accept: "text/event-stream" },
      signal: req.signal, // 브라우저가 끊으면 엔진 연결도 함께 종료
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "이벤트 스트림 연결 실패" }, { status: 502 });
  }
  if (!upstream.ok || !upstream.body) {
    return NextResponse.json(
      { detail: "이벤트 스트림 연결 실패" },
      { status: upstream.status || 502 },
    );
  }
  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
