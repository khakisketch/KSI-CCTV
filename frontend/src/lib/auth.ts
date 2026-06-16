/**
 * 프론트 자체 인증 — HMAC 서명 세션 쿠키(Edge/Node 공용 Web Crypto).
 *
 * 서버 환경변수만 사용(브라우저 미노출):
 *  - KSI_CCTV_WEB_PASSWORD: 로그인 비밀번호(미설정/'change-me'면 fail-closed)
 *  - KSI_CCTV_WEB_SECRET:   토큰 서명 시크릿(미설정 시 비밀번호로 파생 — 별도 설정 권장)
 */

export const SESSION_COOKIE = "ksi_session";
const PLACEHOLDER = new Set(["", "change-me"]);
const DEFAULT_TTL_SEC = 12 * 3600;

function password(): string {
  return (process.env.KSI_CCTV_WEB_PASSWORD ?? "").trim();
}
function secret(): string {
  return (process.env.KSI_CCTV_WEB_SECRET ?? "").trim() || password();
}

/** 비밀번호가 설정되어 로그인이 가능한 상태인가(미설정이면 fail-closed). */
export function isAuthConfigured(): boolean {
  return !PLACEHOLDER.has(password());
}

async function hmacHex(msg: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret()),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(msg));
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** 입력 비밀번호 검증(timing-safe, 동일 길이 HMAC 비교). */
export async function verifyPassword(input: string): Promise<boolean> {
  if (!isAuthConfigured()) return false;
  return constantTimeEqual(await hmacHex(`pw:${input}`), await hmacHex(`pw:${password()}`));
}

/** 서명 세션 토큰 발급: "<exp>.<hmac(exp)>". */
export async function createSession(ttlSec: number = DEFAULT_TTL_SEC): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + ttlSec;
  return `${exp}.${await hmacHex(`sess:${exp}`)}`;
}

/** 세션 쿠키 검증(서명 + 만료). */
export async function verifySession(token: string | undefined): Promise<boolean> {
  if (!token || !isAuthConfigured()) return false;
  const dot = token.indexOf(".");
  if (dot < 0) return false;
  const exp = token.slice(0, dot);
  const sig = token.slice(dot + 1);
  if (!/^\d+$/.test(exp)) return false;
  if (Number(exp) < Math.floor(Date.now() / 1000)) return false;
  return constantTimeEqual(sig, await hmacHex(`sess:${exp}`));
}
