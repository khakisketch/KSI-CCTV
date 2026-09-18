import { chmod, readFile } from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";

import {
  passwordHash,
  passwordValidationError,
  prepareDirectory,
  randomHex,
  timingSafeCompare,
  withFileLockMutation,
  writeFileAtomic,
} from "./auth-primitives";

export type UserRole = "operator" | "viewer";

export type User = {
  id: string;
  username: string;
  role: UserRole;
  passwordHash: string;
  salt: string;
  enabled: boolean;
  createdAt: number;
  updatedAt: number;
};

type UsersFile = {
  version: 1;
  users: User[];
};

const USERS_FILE = "users.json";
const USERS_LOCK_FILE = `${USERS_FILE}.lock`;
const USERNAME_PATTERN = /^[A-Za-z0-9._-]{1,64}$/;

function usersDirectory(): string {
  return process.env.KSI_CCTV_WEB_AUTH_DIR?.trim() || path.join(process.cwd(), "data");
}

function usersPath(): string {
  return path.join(usersDirectory(), USERS_FILE);
}

function usersLockPath(): string {
  return path.join(usersDirectory(), USERS_LOCK_FILE);
}

function validBase64(value: unknown, bytes: number): value is string {
  if (typeof value !== "string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) return false;
  return Buffer.from(value, "base64").length === bytes;
}

function validRole(role: unknown): role is UserRole {
  return role === "operator" || role === "viewer";
}

function validUsername(username: unknown): username is string {
  return typeof username === "string" && USERNAME_PATTERN.test(username);
}

function validUser(record: unknown): record is User {
  if (!record || typeof record !== "object" || Array.isArray(record)) return false;
  const candidate = record as Record<string, unknown>;
  return typeof candidate.id === "string" && candidate.id.length > 0
    && validUsername(candidate.username)
    && validRole(candidate.role)
    && validBase64(candidate.passwordHash, 64)
    && validBase64(candidate.salt, 16)
    && typeof candidate.enabled === "boolean"
    && typeof candidate.createdAt === "number" && Number.isFinite(candidate.createdAt)
    && typeof candidate.updatedAt === "number" && Number.isFinite(candidate.updatedAt);
}

function parseUsersFile(value: string): UsersFile | undefined {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    return undefined;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return undefined;
  const record = parsed as Record<string, unknown>;
  if (record.version !== 1 || !Array.isArray(record.users)) return undefined;
  if (!(record.users as unknown[]).every(validUser)) return undefined;
  return record as UsersFile;
}

async function readUsersFile(): Promise<UsersFile | undefined | "error"> {
  try {
    const parsed = parseUsersFile(await readFile(usersPath(), "utf8"));
    return parsed ?? "error";
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "ENOENT" ? undefined : "error";
  }
}

async function persistUsers(users: User[]): Promise<void> {
  await prepareDirectory(usersDirectory());
  const file = usersPath();
  await writeFileAtomic(file, JSON.stringify({ version: 1, users } satisfies UsersFile));
  await chmod(file, 0o600);
}

export async function listUsers(): Promise<User[]> {
  const state = await readUsersFile();
  return state === undefined || state === "error" ? [] : state.users;
}

export async function findUserByUsername(username: string): Promise<User | undefined | "error"> {
  if (!validUsername(username)) return undefined;
  const state = await readUsersFile();
  if (state === undefined) return undefined;
  if (state === "error") return "error";
  return state.users.find((user) => user.username === username);
}

export async function createUser(
  username: string,
  role: string,
  password: string,
): Promise<"created" | "exists" | "invalid" | "conflict"> {
  if (!validUsername(username) || !validRole(role) || passwordValidationError(password)) return "invalid";
  return withFileLockMutation(usersDirectory(), usersLockPath(), async () => {
    const state = await readUsersFile();
    if (state === "error") return "conflict";
    const users = state?.users ?? [];
    if (users.some((user) => user.username === username)) return "exists";
    const salt = randomBytes(16);
    const now = Date.now();
    users.push({
      id: randomHex(16),
      username,
      role,
      passwordHash: (await passwordHash(password, salt)).toString("base64"),
      salt: salt.toString("base64"),
      enabled: true,
      createdAt: now,
      updatedAt: now,
    });
    await persistUsers(users);
    return "created";
  });
}

export async function updateUser(
  id: string,
  patch: { role?: string; enabled?: boolean },
): Promise<"updated" | "invalid" | "missing" | "conflict"> {
  if (typeof id !== "string" || !patch || typeof patch !== "object") return "invalid";
  const { role, enabled } = patch;
  if (role === undefined && enabled === undefined) return "invalid";
  if (role !== undefined && !validRole(role)) return "invalid";
  if (enabled !== undefined && typeof enabled !== "boolean") return "invalid";
  return withFileLockMutation(usersDirectory(), usersLockPath(), async () => {
    const state = await readUsersFile();
    if (state === "error") return "conflict";
    const users = state?.users ?? [];
    const target = users.find((user) => user.id === id);
    if (!target) return "missing";
    if (role !== undefined) target.role = role;
    if (enabled !== undefined) target.enabled = enabled;
    target.updatedAt = Date.now();
    await persistUsers(users);
    return "updated";
  });
}

export async function resetUserPassword(
  id: string,
  password: string,
): Promise<"updated" | "invalid" | "missing" | "conflict"> {
  if (typeof id !== "string" || passwordValidationError(password)) return "invalid";
  return withFileLockMutation(usersDirectory(), usersLockPath(), async () => {
    const state = await readUsersFile();
    if (state === "error") return "conflict";
    const users = state?.users ?? [];
    const target = users.find((user) => user.id === id);
    if (!target) return "missing";
    const salt = randomBytes(16);
    target.passwordHash = (await passwordHash(password, salt)).toString("base64");
    target.salt = salt.toString("base64");
    target.updatedAt = Date.now();
    await persistUsers(users);
    return "updated";
  });
}

export async function deleteUser(id: string): Promise<"deleted" | "missing" | "conflict"> {
  if (typeof id !== "string") return "missing";
  return withFileLockMutation(usersDirectory(), usersLockPath(), async () => {
    const state = await readUsersFile();
    if (state === "error") return "conflict";
    const users = state?.users ?? [];
    if (!users.some((user) => user.id === id)) return "missing";
    await persistUsers(users.filter((user) => user.id !== id));
    return "deleted";
  });
}

export async function verifyUserPassword(user: User, password: unknown): Promise<boolean> {
  if (typeof password !== "string" || !validUser(user)) return false;
  return timingSafeCompare(
    await passwordHash(password, Buffer.from(user.salt, "base64")),
    Buffer.from(user.passwordHash, "base64"),
  );
}

export async function findUserById(id: string): Promise<User | undefined | "error"> {
  if (typeof id !== "string" || id.length === 0) return undefined;
  const state = await readUsersFile();
  if (state === undefined) return undefined;
  if (state === "error") return "error";
  return state.users.find((user) => user.id === id);
}

export async function roleForUserId(id: string): Promise<UserRole | undefined> {
  if (typeof id !== "string") return undefined;
  const state = await readUsersFile();
  if (!state || state === "error") return undefined;
  const target = state.users.find((user) => user.id === id);
  return target && target.enabled ? target.role : undefined;
}
