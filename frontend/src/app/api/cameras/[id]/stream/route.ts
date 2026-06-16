import { NextRequest, NextResponse } from "next/server";

// 카메라 라이브 스트림 URL 발급. 브라우저는 RTSP를 직접 재생 못 하므로, 영상 게이트웨이
// (MediaMTX)가 재발행하는 HLS를 same-origin 프록시(/api/cameras/{id}/hls/...)를 통해 받는다.
// 게이트웨이 주소·RTSP 자격증명은 서버에만 있고 브라우저로 노출되지 않는다(architecture-target §2).
// 또한 프록시가 MediaMTX의 cookieCheck 핸드셰이크를 서버에서 처리하므로, 평문 HTTP·교차출처에서
// Secure 쿠키가 막히는 문제(브라우저 직접 재생 불가)를 우회한다. 이 라우트는 세션 게이트 뒤에 있다.

const GW = process.env.KSI_CCTV_GATEWAY_URL ?? ""; // 서버에서 도달 가능한 게이트웨이 주소

export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  if (!/^[A-Za-z0-9._-]+$/.test(id)) {
    return NextResponse.json({ detail: "잘못된 카메라 id" }, { status: 400 });
  }
  if (!GW) {
    return NextResponse.json(
      { detail: "영상 게이트웨이가 설정되지 않았습니다(KSI_CCTV_GATEWAY_URL)." },
      { status: 503 },
    );
  }
  // 브라우저에는 same-origin 프록시 경로만 내려준다(게이트웨이 주소 미노출).
  return NextResponse.json({ id, hls: `/api/cameras/${encodeURIComponent(id)}/hls/index.m3u8` });
}
