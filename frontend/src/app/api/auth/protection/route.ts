import { NextRequest, NextResponse } from "next/server";

import {
  SESSION_COOKIE,
  changeLocalPassword,
  createSession,
  disableLocalProtection,
  passwordValidationError,
  protectionStatus,
  sessionTtlSeconds,
  setupLocalProtection,
  verifySetupToken,
  verifySession,
} from "@/lib/auth";
import { loginRateLimiter, loginSource } from "@/lib/login-rate-limit";

function sameOrigin(req: NextRequest): boolean {
  const origin = req.headers.get("origin");
  return origin === req.nextUrl.origin || req.headers.get("sec-fetch-site") === "same-origin";
}

function clearSession(response: NextResponse, req: NextRequest): NextResponse {
  const trustProxy = process.env.KSI_CCTV_TRUST_PROXY?.toLowerCase() === "true";
  const forwardedProto = trustProxy ? req.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() : undefined;
  const secureCookie = process.env.KSI_CCTV_WEB_COOKIE_SECURE
    ? process.env.KSI_CCTV_WEB_COOKIE_SECURE.toLowerCase() !== "false"
    : forwardedProto === "https" || req.nextUrl.protocol === "https:";
  response.cookies.set(SESSION_COOKIE, "", { httpOnly: true, sameSite: "lax", secure: secureCookie, path: "/", maxAge: 0 });
  return response;
}

function sessionResponse(req: NextRequest): Promise<NextResponse> {
  return createSession().then((token) => {
    const response = NextResponse.json({ ok: true, mode: "local" });
    const trustProxy = process.env.KSI_CCTV_TRUST_PROXY?.toLowerCase() === "true";
    const forwardedProto = trustProxy ? req.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() : undefined;
    response.cookies.set(SESSION_COOKIE, token, {
      httpOnly: true, sameSite: "lax", secure: process.env.KSI_CCTV_WEB_COOKIE_SECURE
        ? process.env.KSI_CCTV_WEB_COOKIE_SECURE.toLowerCase() !== "false"
        : forwardedProto === "https" || req.nextUrl.protocol === "https:", path: "/", maxAge: sessionTtlSeconds(),
    });
    return response;
  });
}

export async function GET() {
  return NextResponse.json(await protectionStatus(), { headers: { "Cache-Control": "no-store" } });
}

export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "교차 출처 변경 요청은 허용되지 않습니다." }, { status: 403 });
  const trustProxy = process.env.KSI_CCTV_TRUST_PROXY?.toLowerCase() === "true";
  const source = loginSource(req.headers, trustProxy);
  const limit = loginRateLimiter.check(source);
  if (!limit.allowed) return NextResponse.json({ detail: "요청이 너무 많습니다. 잠시 후 다시 시도하세요." }, { status: 429, headers: { "Retry-After": String(limit.retryAfterSeconds) } });
  const body = await req.json().catch(() => ({}));
  const action = body?.action;
  const status = await protectionStatus();
  if (status.mode === "environment") return NextResponse.json({ detail: "환경 변수로 설정된 보호는 여기서 변경할 수 없습니다." }, { status: 409 });
  if (status.mode === "error") return NextResponse.json({ detail: "인증 상태를 읽을 수 없습니다." }, { status: 503 });
  if (action === "setup") {
    if (status.mode !== "open") return NextResponse.json({ detail: "보호가 이미 설정되어 있습니다." }, { status: 409 });
    if (!verifySetupToken(body?.setupToken)) {
      loginRateLimiter.consume(source);
      return NextResponse.json({ detail: "서비스 엔지니어가 제공한 초기 설정 토큰이 필요합니다." }, { status: 403 });
    }
    const password = body?.password ?? body?.newPassword;
    const error = passwordValidationError(password);
    if (error) return NextResponse.json({ detail: error }, { status: 400 });
    try {
      const result = await setupLocalProtection(password);
      if (result !== "created") return NextResponse.json({ detail: "보호가 이미 설정되어 있습니다." }, { status: 409 });
      return await sessionResponse(req);
    } catch {
      return NextResponse.json({ detail: "인증 상태를 저장하지 못했습니다." }, { status: 503 });
    }
  }
  if (action !== "change" && action !== "disable") return NextResponse.json({ detail: "지원하지 않는 보호 변경 요청입니다." }, { status: 400 });
  if (status.mode !== "local" || !(await verifySession(req.cookies.get(SESSION_COOKIE)?.value))) {
    return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  }
  if (action === "change") {
    const error = passwordValidationError(body?.newPassword);
    if (error) return NextResponse.json({ detail: error }, { status: 400 });
    const result = await changeLocalPassword(body?.currentPassword, body?.newPassword);
    if (result === "unauthorized") {
      loginRateLimiter.consume(source);
      return NextResponse.json({ detail: "현재 비밀번호가 올바르지 않습니다." }, { status: 401 });
    }
    if (result !== "changed") return NextResponse.json({ detail: "보호 상태를 변경하지 못했습니다." }, { status: 409 });
    return clearSession(NextResponse.json({ ok: true, mode: "local" }), req);
  }
  const result = await disableLocalProtection(body?.currentPassword);
  if (result === "unauthorized") {
    loginRateLimiter.consume(source);
    return NextResponse.json({ detail: "현재 비밀번호가 올바르지 않습니다." }, { status: 401 });
  }
  if (result !== "disabled") return NextResponse.json({ detail: "보호 상태를 변경하지 못했습니다." }, { status: 409 });
  return clearSession(NextResponse.json({ ok: true, mode: "open" }), req);
}
