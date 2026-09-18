import { NextRequest, NextResponse } from "next/server";

import { requireRole } from "@/lib/require-role";
import { deleteUser, findUserById, updateUser, type User } from "@/lib/users";

const NO_STORE = { "Cache-Control": "no-store" };

function toPublicUser(user: User) {
  return {
    id: user.id,
    username: user.username,
    role: user.role,
    enabled: user.enabled,
    createdAt: user.createdAt,
    updatedAt: user.updatedAt,
  };
}

// 관리자 전용 역할·활성 상태 변경. body {role?, enabled?}.
export async function PATCH(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const { id } = await context.params;
  const body = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  const patch: { role?: string; enabled?: boolean } = {};
  if (body?.role !== undefined) patch.role = typeof body.role === "string" ? body.role : "";
  if (body?.enabled !== undefined) patch.enabled = body.enabled as boolean;
  const result = await updateUser(id, patch);
  if (result === "updated") {
    const found = await findUserById(id);
    if (!found || found === "error") {
      return NextResponse.json(
        { detail: "사용자 저장소에 접근할 수 없습니다. 잠시 후 다시 시도하세요." },
        { status: 409, headers: NO_STORE },
      );
    }
    return NextResponse.json({ user: toPublicUser(found) }, { headers: NO_STORE });
  }
  if (result === "missing") {
    return NextResponse.json({ detail: "사용자를 찾을 수 없습니다." }, { status: 404, headers: NO_STORE });
  }
  if (result === "conflict") {
    return NextResponse.json(
      { detail: "사용자 저장소에 접근할 수 없습니다. 잠시 후 다시 시도하세요." },
      { status: 409, headers: NO_STORE },
    );
  }
  return NextResponse.json(
    { detail: "역할 또는 활성화 상태를 확인하세요." },
    { status: 400, headers: NO_STORE },
  );
}

// 관리자 전용 사용자 삭제.
export async function DELETE(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = await requireRole(req, "admin");
  if (denied) return denied;
  const { id } = await context.params;
  const result = await deleteUser(id);
  if (result === "deleted") {
    return NextResponse.json({ detail: "사용자를 삭제했습니다." }, { headers: NO_STORE });
  }
  if (result === "missing") {
    return NextResponse.json({ detail: "사용자를 찾을 수 없습니다." }, { status: 404, headers: NO_STORE });
  }
  return NextResponse.json(
    { detail: "사용자 저장소에 접근할 수 없습니다. 잠시 후 다시 시도하세요." },
    { status: 409, headers: NO_STORE },
  );
}
