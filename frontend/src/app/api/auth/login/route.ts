import { NextRequest, NextResponse } from "next/server";

import {
  SESSION_COOKIE,
  createSession,
  protectionStatus,
  sessionTtlSeconds,
  verifyPassword,
} from "@/lib/auth";
import { passwordHash } from "@/lib/auth-primitives";
import { loginRateLimiter, loginSource } from "@/lib/login-rate-limit";
import { findUserByUsername, verifyUserPassword } from "@/lib/users";

// Timing-channel mitigation: fixed 16-byte salt so missing/disabled/corrupted
// usernames still pay one scrypt verification before the identical 401.
const DUMMY_SALT = Buffer.from("0123456789abcdef", "utf8");
// CPU 고갈 방지: scrypt에 투입하기 전 비밀번호 길이를 제한한다. 초과 시에도
// 경로별 동일 401과 rate-limit 소비는 유지한다.
const MAX_LOGIN_PASSWORD_LENGTH = 1024;

export async function POST(req: NextRequest) {
  const trustProxy = process.env.KSI_CCTV_TRUST_PROXY?.toLowerCase() === "true";
  const source = loginSource(req.headers, trustProxy);
  const limit = loginRateLimiter.check(source);
  if (!limit.allowed) {
    return NextResponse.json(
      { detail: "로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요." },
      { status: 429, headers: { "Retry-After": String(limit.retryAfterSeconds) } },
    );
  }
  const protection = await protectionStatus();
  if (protection.mode === "open") {
    return NextResponse.json(
      { detail: "보호가 비활성화된 공개 콘솔에서는 로그인할 수 없습니다." },
      { status: 409 },
    );
  }
  if (protection.mode === "error") return NextResponse.json({ detail: "인증 상태를 읽을 수 없습니다." }, { status: 503 });
  const body = await req.json().catch(() => ({}));
  const username = typeof body?.username === "string" ? body.username.trim() : "";
  const pw = typeof body?.password === "string" ? body.password : "";
  if (pw.length > MAX_LOGIN_PASSWORD_LENGTH) {
    loginRateLimiter.consume(source);
    return NextResponse.json(
      { detail: username ? "사용자명 또는 비밀번호가 올바르지 않습니다." : "비밀번호가 올바르지 않습니다." },
      { status: 401 },
    );
  }
  let userId: string | undefined;
  if (username) {
    const candidate = await findUserByUsername(username);
    const user = candidate && candidate !== "error" && candidate.enabled ? candidate : undefined;
    if (!user) {
      await passwordHash(pw, DUMMY_SALT);
      loginRateLimiter.consume(source);
      return NextResponse.json({ detail: "사용자명 또는 비밀번호가 올바르지 않습니다." }, { status: 401 });
    }
    if (!(await verifyUserPassword(user, pw))) {
      loginRateLimiter.consume(source);
      return NextResponse.json({ detail: "사용자명 또는 비밀번호가 올바르지 않습니다." }, { status: 401 });
    }
    userId = user.id;
  } else if (!(await verifyPassword(pw))) {
    loginRateLimiter.consume(source);
    return NextResponse.json({ detail: "비밀번호가 올바르지 않습니다." }, { status: 401 });
  }
  const ttlSec = sessionTtlSeconds();
  let token: string;
  try {
    token = await createSession(ttlSec, userId);
  } catch {
    return NextResponse.json(
      { detail: "안전한 로그인 세션을 만들지 못했습니다. 저장소 상태를 확인하세요." },
      { status: 503 },
    );
  }
  const res = NextResponse.json({ ok: true });
  const forwardedProto = trustProxy
    ? req.headers.get("x-forwarded-proto")?.split(",")[0]?.trim()
    : undefined;
  const secureCookie = process.env.KSI_CCTV_WEB_COOKIE_SECURE
    ? process.env.KSI_CCTV_WEB_COOKIE_SECURE.toLowerCase() !== "false"
    : forwardedProto === "https" || req.nextUrl.protocol === "https:";
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: secureCookie,
    path: "/",
    maxAge: ttlSec,
  });
  return res;
}
