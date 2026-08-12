import { NextRequest, NextResponse } from "next/server";

import { engineFetch } from "@/lib/engine";

// HLS same-origin 프록시. 브라우저(hls.js) → 이 라우트(서버) → 게이트웨이(MediaMTX).
// MediaMTX는 HLS에 cookieCheck(Secure 쿠키) CSRF 보호를 걸어, 평문 HTTP·교차출처에서는
// 브라우저가 쿠키를 못 보내 하위 재생목록/세그먼트가 401이 된다. 서버는 이 핸드셰이크를
// 직접 수행(Secure 제약 없음)하고 결과만 same-origin으로 전달한다. 세션 게이트(middleware) 뒤.
//
// 핸드셰이크: index.m3u8 첫 요청 → 302(Set-Cookie: cookieCheck) → ?cookieCheck=1 재요청 →
// 200에서 "실제 세션 쿠키"가 발급된다. 따라서 리다이렉트뿐 아니라 최종 200 응답의 Set-Cookie도
// 반드시 수집해야 하위 재생목록/세그먼트 요청이 통과한다(여기서 한 번 틀려 401이 났었다).

export const dynamic = "force-dynamic";

const GW = (process.env.KSI_CCTV_GATEWAY_URL ?? "").replace(/\/+$/, "");
const MAX_JARS = 32;
const JAR_TTL_MS = 5 * 60 * 1000;
type CookieJar = { cookies: Map<string, string>; expiresAt: number; usedAt: number };
const jars = new Map<string, CookieJar>(); // 카메라 id → gateway cookie jar
const SAFE = /^[A-Za-z0-9._-]+$/;

function safeSegment(value: string): boolean {
  return SAFE.test(value) && value !== "." && value !== "..";
}

function jarFor(id: string): Map<string, string> {
  const now = Date.now();
  for (const [key, jar] of jars) {
    if (jar.expiresAt <= now) jars.delete(key);
  }
  let jar = jars.get(id);
  if (!jar) {
    if (jars.size >= MAX_JARS) {
      const oldest = [...jars.entries()].sort(([, a], [, b]) => a.usedAt - b.usedAt)[0];
      if (oldest) jars.delete(oldest[0]);
    }
    jar = { cookies: new Map(), expiresAt: now + JAR_TTL_MS, usedAt: now };
    jars.set(id, jar);
  }
  jar.usedAt = now;
  return jar.cookies;
}

function setCookieList(res: Response): string[] {
  const getSC = (res.headers as unknown as { getSetCookie?: () => string[] }).getSetCookie;
  if (getSC) return getSC.call(res.headers);
  const one = res.headers.get("set-cookie");
  return one ? [one] : [];
}

function absorb(id: string, res: Response): void {
  const j = jarFor(id);
  for (const c of setCookieList(res)) {
    const nv = c.split(";")[0];
    const i = nv.indexOf("=");
    if (i > 0) j.set(nv.slice(0, i).trim(), nv.slice(i + 1).trim());
  }
  const jar = jars.get(id);
  if (jar) jar.expiresAt = Date.now() + JAR_TTL_MS;
}

function cookieHeader(id: string): string | null {
  const j = jarFor(id);
  if (j.size === 0) return null;
  return [...j].map(([k, v]) => `${k}=${v}`).join("; ");
}

function get(url: string, cookie: string | null): Promise<Response> {
  const headers: Record<string, string> = {};
  if (cookie) headers.cookie = cookie;
  return fetch(url, { headers, redirect: "manual", cache: "no-store" });
}

// 302 cookieCheck 체인을 따라가며 모든 응답(최종 200 포함)의 Set-Cookie를 수집한다.
async function follow(url: string, id: string): Promise<Response> {
  const allowedOrigin = new URL(GW).origin;
  let currentUrl = url;
  let res = await get(currentUrl, cookieHeader(id));
  absorb(id, res);
  for (let hops = 0; hops < 4 && res.status >= 300 && res.status < 400; hops++) {
    const loc = res.headers.get("location");
    if (!loc) break;
    const nextUrl = new URL(loc, currentUrl);
    if (nextUrl.origin !== allowedOrigin) {
      return new Response(null, { status: 502 });
    }
    currentUrl = nextUrl.toString();
    res = await get(currentUrl, cookieHeader(id));
    absorb(id, res);
  }
  return res;
}

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ id: string; seg: string[] }> },
) {
  const { id, seg } = await ctx.params;
  if (!GW) return NextResponse.json({ detail: "게이트웨이 미설정" }, { status: 503 });
  if (!safeSegment(id) || seg.length === 0 || seg.some((s) => !safeSegment(s))) {
    return NextResponse.json({ detail: "잘못된 경로" }, { status: 400 });
  }
  const cameraResponse = await engineFetch(`/api/cameras/${encodeURIComponent(id)}`);
  if (!cameraResponse?.ok) {
    jars.delete(id);
    return NextResponse.json({ detail: "등록된 카메라를 찾을 수 없습니다." }, { status: 404 });
  }
  const camera = (await cameraResponse.json().catch(() => ({}))) as {
    camera?: { enabled?: boolean };
  };
  if (!camera.camera?.enabled) {
    jars.delete(id);
    return NextResponse.json({ detail: "비활성 카메라는 재생할 수 없습니다." }, { status: 409 });
  }

  const path = seg.join("/");
  const url = `${GW}/${id}/${path}${new URL(req.url).search}`;

  let res = await follow(url, id);
  // 401(쿠키 만료/부재) → index.m3u8로 핸드셰이크 후 1회 재시도.
  if (res.status === 401) {
    jars.delete(id);
    await follow(`${GW}/${id}/index.m3u8`, id);
    res = await follow(url, id);
  }

  if (!res.ok || !res.body) {
    return NextResponse.json({ detail: "게이트웨이 응답 오류" }, { status: res.status || 502 });
  }
  return new NextResponse(res.body, {
    status: 200,
    headers: {
      "content-type": res.headers.get("content-type") ?? "application/octet-stream",
      "cache-control": "no-store",
    },
  });
}
