export type Role = "admin" | "operator" | "viewer";

export const ROLE_RANK: Record<Role, number> = {
  admin: 3,
  operator: 2,
  viewer: 1,
};

export function roleAtLeast(role: Role, min: Role): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[min];
}

// 선언 테이블: 공개(`/login*`·`/api/auth/*`) → admin 전용 → operator → viewer(그 외).
// `ADMIN_STAR_PREFIXES`·`ADMIN_SLASH_BASES` 모두 경계(base 또는 base + "/") 매칭이다.
const ADMIN_STAR_PREFIXES = [
  "/api/users",
  "/api/models",
  "/api/system",
  "/api/config",
  "/api/policies",
  "/api/event-catalog",
  "/api/commissioning",
  "/api/video-sources",
  "/api/preflight",
  "/api/audit",
  "/api/alert-preferences",
  "/api/webhooks",
  "/api/settings",
  "/settings",
];

const ADMIN_SLASH_BASES = [
  "/api/support",
  "/api/alerts",
  "/api/control",
];

const CAMERAS_BASE = "/api/cameras";
const ONVIF_BASE = "/api/onvif";
const INCIDENT_ACTIONS_PATTERN = /^\/api\/incidents\/[^/]+\/actions(?:\/|$)/;

const MUTATION_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * 경로·메서드에 요구되는 최소 역할을 반환한다. null은 공개 경로(제한 없음).
 */
export function minRoleForPath(pathname: string, method = "GET"): Role | null {
  if (pathname.startsWith("/login")) return null;
  if (pathname === "/api/auth" || pathname.startsWith("/api/auth/")) return null;
  for (const prefix of ADMIN_STAR_PREFIXES) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) return "admin";
  }
  for (const base of ADMIN_SLASH_BASES) {
    if (pathname === base || pathname.startsWith(`${base}/`)) return "admin";
  }
  const normalizedMethod = method.toUpperCase();
  if (
    (pathname === CAMERAS_BASE || pathname.startsWith(`${CAMERAS_BASE}/`))
    && MUTATION_METHODS.has(normalizedMethod)
  ) {
    return "operator";
  }
  if (INCIDENT_ACTIONS_PATTERN.test(pathname)) return "operator";
  if (pathname === ONVIF_BASE || pathname.startsWith(`${ONVIF_BASE}/`)) return "operator";
  // 미등록 `/api/*` 변경 요청은 fail-closed(향후 라우트 누락 시 viewer 변경 방지).
  // 미등록 GET은 기존대로 viewer(읽기), 그 외 /api/* 비-GET은 admin.
  if (pathname === "/api" || pathname.startsWith("/api/")) {
    if (MUTATION_METHODS.has(normalizedMethod)) return "admin";
    return "viewer";
  }
  return "viewer";
}
