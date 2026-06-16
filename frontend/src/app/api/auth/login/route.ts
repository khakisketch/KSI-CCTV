import { NextRequest, NextResponse } from "next/server";

import { SESSION_COOKIE, createSession, isAuthConfigured, verifyPassword } from "@/lib/auth";

export async function POST(req: NextRequest) {
  if (!isAuthConfigured()) {
    return NextResponse.json(
      { detail: "서버에 비밀번호가 설정되지 않았습니다(KSI_CCTV_WEB_PASSWORD)." },
      { status: 503 },
    );
  }
  const body = await req.json().catch(() => ({}));
  const pw = typeof body?.password === "string" ? body.password : "";
  if (!(await verifyPassword(pw))) {
    return NextResponse.json({ detail: "비밀번호가 올바르지 않습니다." }, { status: 401 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, await createSession(), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 12 * 3600,
  });
  return res;
}
