import { chmod, lstat, mkdir, readFile, readdir, rename, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { randomBytes, scrypt as scryptCallback, timingSafeEqual } from "node:crypto";
import { promisify } from "node:util";

export const SESSION_COOKIE = "ksi_session";
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

type MutationLock = {
  version: 1;
  owner: string;
  createdAt: number;
};

const scrypt = promisify(scryptCallback);
const PLACEHOLDER = new Set([
  "",
  "change-me",
  "change-me-web-password",
  "replace-with-installer-password",
]);
const DEFAULT_TTL_SEC = 12 * 3600;
const MIN_TTL_SEC = 5 * 60;
const MAX_TTL_SEC = 30 * 24 * 3600;
const MIN_PASSWORD_LENGTH = 12;
const MAX_PASSWORD_LENGTH = 1024;
const MIN_ENVIRONMENT_SECRET_LENGTH = 32;
const MIN_SETUP_TOKEN_LENGTH = 32;
const STATE_FILE = "auth.json";
const STATE_LOCK_FILE = `${STATE_FILE}.lock`;
const MUTATION_LOCK_TTL_MS = 5 * 60_000;

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

async function prepareDirectory(directory: string): Promise<string> {
  await mkdir(directory, { recursive: true, mode: 0o700 });
  await chmod(directory, 0o700);
  return directory;
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

function randomHex(bytes: number): string {
  return randomBytes(bytes).toString("hex");
}

async function pruneExpiredSessions(now: number): Promise<void> {
  const directory = await prepareSessionDirectory();
  const entries = await readdir(directory).catch(() => [] as string[]);
  await Promise.all(entries.map(async (entry) => {
    if (!/^[a-f0-9]{64}$/.test(entry)) return;
    const file = path.join(directory, entry);
    const exp = Number(await readFile(file, "utf8").catch(() => "0"));
    if (!Number.isFinite(exp) || exp < now) await unlink(file).catch(() => undefined);
  }));
}

function equal(a: Buffer, b: Buffer): boolean {
  return a.length === b.length && timingSafeEqual(a, b);
}

export function verifySetupToken(token: unknown): boolean {
  const expected = process.env.KSI_CCTV_WEB_SETUP_TOKEN ?? "";
  if (expected !== expected.trim() || expected.length < MIN_SETUP_TOKEN_LENGTH || isPlaceholder(expected)) {
    return false;
  }
  if (typeof token !== "string" || token.length > 1024) return false;
  return equal(Buffer.from(token, "utf8"), Buffer.from(expected, "utf8"));
}

async function passwordHash(password: string, salt: Buffer): Promise<Buffer> {
  return Buffer.from((await scrypt(password, salt, 64)) as Uint8Array);
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

export function passwordValidationError(password: unknown): string | undefined {
  if (typeof password !== "string" || !password.trim()) return "비밀번호를 입력하세요.";
  if (password !== password.trim()) return "비밀번호 앞뒤에는 공백을 사용할 수 없습니다.";
  if (password.length < MIN_PASSWORD_LENGTH) return `비밀번호는 ${MIN_PASSWORD_LENGTH}자 이상이어야 합니다.`;
  if (password.length > MAX_PASSWORD_LENGTH) return `비밀번호는 ${MAX_PASSWORD_LENGTH}자 이하여야 합니다.`;
  return undefined;
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
  await prepareDirectory(authDirectory());
  const lock: MutationLock = { version: 1, owner: randomHex(16), createdAt: Date.now() };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      await writeFile(stateLockPath(), JSON.stringify(lock), { encoding: "utf8", flag: "wx", mode: 0o600 });
      break;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      if (attempt || !(await clearStaleMutationLock())) return "conflict";
    }
  }
  try {
    return await mutation();
  } finally {
    const owner = parseMutationLock(await readFile(stateLockPath(), "utf8").catch(() => ""));
    if (owner?.owner === lock.owner) await unlink(stateLockPath()).catch(() => undefined);
  }
}

function parseMutationLock(value: string): MutationLock | undefined {
  try {
    const record = JSON.parse(value) as Record<string, unknown>;
    return record.version === 1 && typeof record.owner === "string" && /^[a-f0-9]{32}$/.test(record.owner)
      && typeof record.createdAt === "number" && Number.isFinite(record.createdAt)
      ? record as MutationLock
      : undefined;
  } catch {
    return undefined;
  }
}

async function clearStaleMutationLock(): Promise<boolean> {
  const lockPath = stateLockPath();
  let raw: string;
  let modifiedAt: number;
  try {
    [raw, modifiedAt] = await Promise.all([
      readFile(lockPath, "utf8"),
      lstat(lockPath).then((stat) => stat.mtimeMs),
    ]);
  } catch {
    return false;
  }
  const createdAt = parseMutationLock(raw)?.createdAt ?? modifiedAt;
  if (Date.now() - createdAt < MUTATION_LOCK_TTL_MS) return false;
  if ((await readFile(lockPath, "utf8").catch(() => "")) !== raw) return false;
  try {
    await unlink(lockPath);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "ENOENT";
  }
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
  return equal(await passwordHash(input, Buffer.from(state.salt, "base64")), Buffer.from(state.passwordHash, "base64"));
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
  if (credential.mode === "environment") return equal(Buffer.from(input), Buffer.from(credential.password!));
  const state = await localState();
  return !!state && state !== "error" && verifyLocalPassword(state, input);
}

export async function createSession(ttlSec: number = sessionTtlSeconds()): Promise<string> {
  const credential = await currentCredential();
  if (!credential) throw new Error("authentication is not configured");
  const exp = Math.floor(Date.now() / 1000) + ttlSec;
  await pruneExpiredSessions(Math.floor(Date.now() / 1000));
  for (let attempt = 0; attempt < 3; attempt++) {
    const id = randomHex(32);
    const token = `${id}.${exp}.${await hmacHex(credential.secret, `sess:${id}:${exp}`)}`;
    try {
      await writeFile(await sessionPath(token), String(exp), { encoding: "utf8", flag: "wx", mode: 0o600 });
      return token;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    }
  }
  throw new Error("session allocation failed");
}

export async function verifySession(token: string | undefined): Promise<boolean> {
  if (!token) return false;
  const credential = await currentCredential();
  if (!credential) return false;
  const [id, exp, sig, extra] = token.split(".");
  if (extra !== undefined || !/^[a-f0-9]{64}$/.test(id ?? "") || !/^\d+$/.test(exp ?? "")) return false;
  if (Number(exp) < Math.floor(Date.now() / 1000)) {
    await unlink(await sessionPath(token)).catch(() => undefined);
    return false;
  }
  if (!equal(Buffer.from(sig ?? ""), Buffer.from(await hmacHex(credential.secret, `sess:${id}:${exp}`)))) return false;
  try {
    return (await readFile(await sessionPath(token), "utf8")) === exp;
  } catch {
    return false;
  }
}

export async function revokeSession(token: string | undefined): Promise<void> {
  if (!token || !(await currentCredential())) return;
  await unlink(await sessionPath(token)).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== "ENOENT") throw error;
  });
}
