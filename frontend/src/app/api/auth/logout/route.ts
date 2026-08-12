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
  res.cookies.set(SESSION_COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
  return res;
}
