import { NextRequest, NextResponse } from "next/server";

// 모델 업로드 multipart 프록시. 브라우저 → 이 라우트(서버) → 엔진 /api/models/upload.
// 멀티파트 본문(파일 포함)을 버퍼링 없이 스트리밍 전달하고 X-API-Key를 서버에서 부착한다.
// API 키는 브라우저 미노출. 세션 게이트(middleware) 뒤.

export const dynamic = "force-dynamic";

const ENGINE = (process.env.KSI_CCTV_ENGINE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const KEY = process.env.KSI_CCTV_API_KEY ?? "";

export async function POST(req: NextRequest) {
  const contentType = req.headers.get("content-type") ?? "";
  const contentLength = req.headers.get("content-length");
  let upstream: Response;
  try {
    upstream = await fetch(`${ENGINE}/api/models/upload`, {
      method: "POST",
      headers: {
        "X-API-Key": KEY,
        "content-type": contentType,
        ...(contentLength ? { "content-length": contentLength } : {}),
      },
      body: req.body, // 멀티파트 스트림 그대로 전달(파일 버퍼링 없음)
      duplex: "half", // 스트림 요청 본문 전송 시 필수(undici)
      cache: "no-store",
    } as RequestInit & { duplex: "half" });
  } catch {
    return NextResponse.json({ detail: "엔진 연결 실패" }, { status: 502 });
  }
  const data = await upstream.json().catch(() => ({ detail: "업로드 처리 오류" }));
  return NextResponse.json(data, { status: upstream.status });
}
