import { NextRequest, NextResponse } from "next/server";

import { getAuthStatus, SESSION_COOKIE, verifySession } from "@/lib/auth";
import {
  DEVELOPMENT_AUTH_BYPASS_HEADER,
  developmentAuthBypassState,
} from "@/lib/auth-mode";

// 모든 라우트를 게이트(로그인 페이지·인증 API·정적 제외). 미인증·미설정 → /login.
export async function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const isMutation = !["GET", "HEAD", "OPTIONS"].includes(req.method);
  const origin = req.headers.get("origin");
  // Next may internally normalize a loopback request's URL host. A browser's
  // same-origin Fetch Metadata signal is safe fallback for that normalization.
  const sameOriginFetch = req.headers.get("sec-fetch-site") === "same-origin";
  if (
    pathname.startsWith("/api/")
    && isMutation
    && origin
    && origin !== req.nextUrl.origin
    && !sameOriginFetch
  ) {
    return NextResponse.json(
      { detail: "교차 출처 변경 요청은 허용되지 않습니다." },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }
  const bypass = developmentAuthBypassState();
  if (bypass.status === "invalid") {
    return NextResponse.json(
      { detail: bypass.reason },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
  if (bypass.status === "active") {
    if (
      req.headers.get(DEVELOPMENT_AUTH_BYPASS_HEADER)
      !== process.env.KSI_CCTV_DEV_AUTH_BYPASS_TOKEN
    ) {
      return NextResponse.json(
        { detail: "개발 인증 생략은 보호된 로컬 launcher 요청에만 사용할 수 있습니다." },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }
    if (pathname === "/login") {
      const url = req.nextUrl.clone();
      url.pathname = "/";
      return NextResponse.redirect(url);
    }
    return NextResponse.next();
  }
  if (
    pathname.startsWith("/login") ||
    pathname.startsWith("/api/auth") ||
    pathname.startsWith("/_next") ||
    pathname === "/favicon.ico"
  ) {
    const { mode } = await getAuthStatus();
    if (pathname.startsWith("/login") && mode === "open") {
      const url = req.nextUrl.clone();
      url.pathname = "/";
      return NextResponse.redirect(url);
    }
    return NextResponse.next();
  }
  const { mode } = await getAuthStatus();
  if (mode === "open") return NextResponse.next();
  if (mode === "error") {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    return NextResponse.redirect(url);
  }
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (await verifySession(token)) return NextResponse.next();
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
