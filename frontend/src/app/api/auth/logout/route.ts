import { NextRequest, NextResponse } from "next/server";

import { protectionStatus, revokeSession, SESSION_COOKIE } from "@/lib/auth";

export async function POST(req: NextRequest) {
  if ((await protectionStatus()).mode === "open") return NextResponse.json({ ok: true });
  let revoked = true;
  try {
    await revokeSession(req.cookies.get(SESSION_COOKIE)?.value);
  } catch {
    revoked = false;
  }
  const res = NextResponse.json(
    revoked ? { ok: true } : { detail: "세션을 서버에서 폐기하지 못했습니다." },
    { status: revoked ? 200 : 503 },
  );
  const trustProxy = process.env.KSI_CCTV_TRUST_PROXY?.toLowerCase() === "true";
  const forwardedProto = trustProxy ? req.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() : undefined;
  const secureCookie = process.env.KSI_CCTV_WEB_COOKIE_SECURE
    ? process.env.KSI_CCTV_WEB_COOKIE_SECURE.toLowerCase() !== "false"
    : forwardedProto === "https" || req.nextUrl.protocol === "https:";
  res.cookies.set(SESSION_COOKIE, "", { httpOnly: true, sameSite: "lax", secure: secureCookie, path: "/", maxAge: 0 });
  return res;
}
