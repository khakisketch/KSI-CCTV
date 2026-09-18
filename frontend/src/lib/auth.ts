import { readFile, readdir, rename, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";

import {
  passwordHash,
  passwordValidationError,
  prepareDirectory,
  randomHex,
  timingSafeCompare,
  withFileLockMutation,
} from "./auth-primitives";
import { findUserById } from "./users";

export { passwordValidationError };

export const SESSION_COOKIE = "ksi_session";
export type SessionPrincipal = {
  role: "admin" | "operator" | "viewer";
  userId?: string;
};
export type ProtectionMode = "open" | "environment" | "local" | "error";
export type ProtectionStatus = {
  mode: ProtectionMode;
  protected: boolean;
  management: boolean;
};

type LocalAuthState = {
  version: 1;
  passwordHash: string;
  salt: string;
  sessionSecret: string;
};

const PLACEHOLDER = new Set([
  "",
  "change-me",
  "change-me-web-password",
  "replace-with-installer-password",
]);
const DEFAULT_TTL_SEC = 12 * 3600;
const MIN_TTL_SEC = 5 * 60;
const MAX_TTL_SEC = 30 * 24 * 3600;
const MIN_ENVIRONMENT_SECRET_LENGTH = 32;
const MIN_SETUP_TOKEN_LENGTH = 32;
const STATE_FILE = "auth.json";
const STATE_LOCK_FILE = `${STATE_FILE}.lock`;

function environmentPassword(): string {
  return process.env.KSI_CCTV_WEB_PASSWORD ?? "";
}

function isPlaceholder(value: string): boolean {
  return PLACEHOLDER.has(value)
    || value === "change-me-long-random-secret"
    || value === "replace-with-random-session-signing-secret"
    || value.startsWith("replace-with-");
}

function environmentCredential(): { password: string; secret: string } | undefined | "error" {
  const password = environmentPassword();
  if (isPlaceholder(password)) return undefined;
  const secret = process.env.KSI_CCTV_WEB_SECRET ?? "";
  if (passwordValidationError(password) || secret !== secret.trim() || isPlaceholder(secret)
    || secret.length < MIN_ENVIRONMENT_SECRET_LENGTH) return "error";
  return { password, secret };
}

function hasEnvironmentPassword(): boolean {
  return environmentCredential() !== undefined;
}

function authDirectory(): string {
  return process.env.KSI_CCTV_WEB_AUTH_DIR?.trim() || path.join(process.cwd(), "data");
}

function sessionDirectory(): string {
  return process.env.KSI_CCTV_WEB_SESSION_DIR?.trim() || path.join(authDirectory(), "sessions");
}

function statePath(): string {
  return path.join(authDirectory(), STATE_FILE);
}

function stateLockPath(): string {
  return path.join(authDirectory(), STATE_LOCK_FILE);
}

function validBase64(value: unknown, bytes: number): value is string {
  if (typeof value !== "string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) return false;
  return Buffer.from(value, "base64").length === bytes;
}

function parseLocalState(value: string): LocalAuthState | undefined {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    return undefined;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return undefined;
  const record = parsed as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  if (keys.join(",") !== "passwordHash,salt,sessionSecret,version") return undefined;
  if (record.version !== 1 || !validBase64(record.passwordHash, 64) || !validBase64(record.salt, 16)
    || !validBase64(record.sessionSecret, 32)) return undefined;
  return record as LocalAuthState;
}

async function localState(): Promise<LocalAuthState | undefined | "error"> {
  try {
    const state = parseLocalState(await readFile(statePath(), "utf8"));
    return state ?? "error";
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "ENOENT" ? undefined : "error";
  }
}

async function prepareSessionDirectory(): Promise<string> {
  return prepareDirectory(sessionDirectory());
}

async function sha256Hex(msg: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(msg));
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function hmacHex(secret: string, msg: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(msg));
  return Array.from(new Uint8Array(sig)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function sessionPath(token: string): Promise<string> {
  return path.join(sessionDirectory(), await sha256Hex(token));
}

type SessionRecord = {
  exp: number;
  userId?: string;
  // 생성 시점의 사용자 sessionVersion. 토큰 서명 대상이 아니라 세션 파일에만
  // 기록된다(토큰 형식 불변). 누락 시 1로 간주한다(하위호환).
  sv?: number;
};

/**
 * 세션 파일 파서. 현행 JSON `{"exp","userId?"}`와 레거시 평문 epoch를 모두
 * 읽는다(하위호환). 손상된 내용은 undefined — 호출부가 무효로 처리한다.
 */
function parseSessionRecord(raw: string): SessionRecord | undefined {
  if (/^\d+$/.test(raw.trim())) {
    const exp = Number(raw.trim());
    return Number.isSafeInteger(exp) ? { exp } : undefined;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return undefined;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return undefined;
  const record = parsed as Record<string, unknown>;
  if (typeof record.exp !== "number" || !Number.isSafeInteger(record.exp)) return undefined;
  if (record.sv !== undefined
    && (typeof record.sv !== "number" || !Number.isSafeInteger(record.sv) || record.sv < 1)) return undefined;
  if (record.userId === undefined) return { exp: record.exp };
  if (typeof record.userId !== "string" || record.userId.length === 0) return undefined;
  return record.sv === undefined
    ? { exp: record.exp, userId: record.userId }
    : { exp: record.exp, userId: record.userId, sv: record.sv };
}

async function pruneExpiredSessions(now: number): Promise<void> {
  const directory = await prepareSessionDirectory();
  const entries = await readdir(directory).catch(() => [] as string[]);
  await Promise.all(entries.map(async (entry) => {
    if (!/^[a-f0-9]{64}$/.test(entry)) return;
    const file = path.join(directory, entry);
    const exp = parseSessionRecord(await readFile(file, "utf8").catch(() => ""))?.exp ?? NaN;
    if (!Number.isFinite(exp) || exp < now) await unlink(file).catch(() => undefined);
  }));
}

export function verifySetupToken(token: unknown): boolean {
  const expected = process.env.KSI_CCTV_WEB_SETUP_TOKEN ?? "";
  if (expected !== expected.trim() || expected.length < MIN_SETUP_TOKEN_LENGTH || isPlaceholder(expected)) {
    return false;
  }
  if (typeof token !== "string" || token.length > 1024) return false;
  return timingSafeCompare(Buffer.from(token, "utf8"), Buffer.from(expected, "utf8"));
}

async function currentCredential(): Promise<{ mode: "environment" | "local"; password?: string; passwordHash?: Buffer; secret: string } | undefined> {
  const environment = environmentCredential();
  if (environment === "error") return undefined;
  if (environment) return { mode: "environment", ...environment };
  const state = await localState();
  if (!state || state === "error") return undefined;
  return { mode: "local", passwordHash: Buffer.from(state.passwordHash, "base64"), secret: state.sessionSecret };
}

export async function protectionStatus(): Promise<ProtectionStatus> {
  const environment = environmentCredential();
  if (environment === "error") return { mode: "error", protected: true, management: false };
  if (environment) return { mode: "environment", protected: true, management: false };
  const state = await localState();
  if (state === "error") return { mode: "error", protected: true, management: false };
  if (state) return { mode: "local", protected: true, management: true };
  return { mode: "open", protected: false, management: true };
}

export async function setupLocalProtection(password: unknown): Promise<"created" | "exists" | "invalid" | "conflict"> {
  if (passwordValidationError(password)) return "invalid";
  if (hasEnvironmentPassword()) return "exists";
  return withLocalMutation(async () => {
    if (hasEnvironmentPassword()) return "exists";
    if (await localState()) return "exists";
    const salt = randomBytes(16);
    const state: LocalAuthState = {
      version: 1,
      passwordHash: (await passwordHash(password as string, salt)).toString("base64"),
      salt: salt.toString("base64"),
      sessionSecret: randomBytes(32).toString("base64"),
    };
    try {
      await writeFile(statePath(), JSON.stringify(state), { encoding: "utf8", flag: "wx", mode: 0o600 });
      return "created";
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "EEXIST") return "exists";
      throw error;
    }
  });
}

async function withLocalMutation<T>(mutation: () => Promise<T>): Promise<T | "conflict"> {
  return withFileLockMutation(authDirectory(), stateLockPath(), mutation);
}

export async function changeLocalPassword(currentPassword: unknown, newPassword: unknown): Promise<"changed" | "invalid" | "unauthorized" | "unavailable" | "conflict"> {
  if (passwordValidationError(newPassword)) return "invalid";
  return withLocalMutation(async () => {
    const state = await localState();
    if (!state || state === "error") return "unavailable";
    if (!(await verifyLocalPassword(state, currentPassword))) return "unauthorized";
    const salt = randomBytes(16);
    const replacement: LocalAuthState = {
      version: 1,
      passwordHash: (await passwordHash(newPassword as string, salt)).toString("base64"),
      salt: salt.toString("base64"),
      sessionSecret: randomBytes(32).toString("base64"),
    };
    const temporary = `${statePath()}.${randomHex(8)}.tmp`;
    await writeFile(temporary, JSON.stringify(replacement), { encoding: "utf8", flag: "wx", mode: 0o600 });
    await rename(temporary, statePath());
    return "changed";
  });
}

async function verifyLocalPassword(state: LocalAuthState, input: unknown): Promise<boolean> {
  if (typeof input !== "string") return false;
  return timingSafeCompare(await passwordHash(input, Buffer.from(state.salt, "base64")), Buffer.from(state.passwordHash, "base64"));
}

export async function disableLocalProtection(currentPassword: unknown): Promise<"disabled" | "unauthorized" | "unavailable" | "conflict"> {
  return withLocalMutation(async () => {
    const state = await localState();
    if (!state || state === "error") return "unavailable";
    if (!(await verifyLocalPassword(state, currentPassword))) return "unauthorized";
    await unlink(statePath());
    return "disabled";
  });
}

export async function isAuthConfigured(): Promise<boolean> {
  return (await protectionStatus()).protected;
}

/** Compatibility name for server-rendered callers; returns no credential material. */
export async function getAuthStatus(): Promise<ProtectionStatus> {
  return protectionStatus();
}

export function sessionTtlSeconds(env: Record<string, string | undefined> = process.env): number {
  const raw = (env.KSI_CCTV_WEB_SESSION_TTL_SEC ?? "").trim();
  if (!/^\d+$/.test(raw)) return DEFAULT_TTL_SEC;
  const value = Number(raw);
  return value >= MIN_TTL_SEC && value <= MAX_TTL_SEC ? value : DEFAULT_TTL_SEC;
}

export async function verifyPassword(input: string): Promise<boolean> {
  const credential = await currentCredential();
  if (!credential) return false;
  if (credential.mode === "environment") return timingSafeCompare(Buffer.from(input), Buffer.from(credential.password!));
  const state = await localState();
  return !!state && state !== "error" && verifyLocalPassword(state, input);
}

export async function createSession(ttlSec: number = sessionTtlSeconds(), userId?: string): Promise<string> {
  const credential = await currentCredential();
  if (!credential) throw new Error("authentication is not configured");
  const exp = Math.floor(Date.now() / 1000) + ttlSec;
  // 개인 세션은 생성 시점의 sessionVersion을 파일에 함께 기록한다.
  // 조회 실패·누락 시 1(하위호환 기본값)로 기록한다.
  let sv: number | undefined;
  if (userId !== undefined) {
    const user = await findUserById(userId);
    sv = user && user !== "error" ? (user.sessionVersion ?? 1) : 1;
  }
  const body = userId === undefined ? JSON.stringify({ exp }) : JSON.stringify({ exp, userId, sv });
  await pruneExpiredSessions(Math.floor(Date.now() / 1000));
  for (let attempt = 0; attempt < 3; attempt++) {
    const id = randomHex(32);
    const token = `${id}.${exp}.${await hmacHex(credential.secret, `sess:${id}:${exp}`)}`;
    try {
      await writeFile(await sessionPath(token), body, { encoding: "utf8", flag: "wx", mode: 0o600 });
      return token;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    }
  }
  throw new Error("session allocation failed");
}

/** 토큰 서명·만료·세션 파일 일치성을 검증하고 파싱된 레코드를 반환한다. */
async function readValidSessionRecord(token: string | undefined): Promise<SessionRecord | null> {
  if (!token) return null;
  const credential = await currentCredential();
  if (!credential) return null;
  const [id, exp, sig, extra] = token.split(".");
  if (extra !== undefined || !/^[a-f0-9]{64}$/.test(id ?? "") || !/^\d+$/.test(exp ?? "")) return null;
  if (Number(exp) < Math.floor(Date.now() / 1000)) {
    await unlink(await sessionPath(token)).catch(() => undefined);
    return null;
  }
  if (!timingSafeCompare(Buffer.from(sig ?? ""), Buffer.from(await hmacHex(credential.secret, `sess:${id}:${exp}`)))) return null;
  let record: SessionRecord | undefined;
  try {
    record = parseSessionRecord(await readFile(await sessionPath(token), "utf8"));
  } catch {
    return null;
  }
  if (!record || record.exp !== Number(exp)) return null;
  return record;
}

export async function verifySession(token: string | undefined): Promise<boolean> {
  return (await sessionPrincipal(token)) !== null;
}

/**
 * 세션의 행위 주체를 해석한다. userId가 없으면(신규 admin JSON·레거시 평문
 * epoch) `{role:"admin"}`. 개인 세션은 users.json에서 역할·활성 상태·세션
 * 세대를 매번 재조회하므로 비활성·삭제·손상·세대 불일치 시 null(세션 즉시
 * 무효). 비밀번호 재설정·비활성 전환은 sessionVersion을 증가시켜 이전
 * 세션을 회수한다.
 */
export async function sessionPrincipal(token: string | undefined): Promise<SessionPrincipal | null> {
  const record = await readValidSessionRecord(token);
  if (!record) return null;
  if (record.userId === undefined) return { role: "admin" };
  const user = await findUserById(record.userId);
  if (!user || user === "error" || !user.enabled) return null;
  if ((user.sessionVersion ?? 1) !== (record.sv ?? 1)) return null;
  return { role: user.role, userId: record.userId };
}

export async function revokeSession(token: string | undefined): Promise<void> {
  if (!token || !(await currentCredential())) return;
  await unlink(await sessionPath(token)).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== "ENOENT") throw error;
  });
}
