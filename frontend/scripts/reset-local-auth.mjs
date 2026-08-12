#!/usr/bin/env node
import { randomBytes } from "node:crypto";
import { lstat, readFile, readdir, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const AUTH_DIRECTORY = "/var/lib/ksi-cctv-web";
export const LOCAL_AUTH_STATE = "auth.json";
export const LOCAL_AUTH_LOCK = `${LOCAL_AUTH_STATE}.lock`;
export const SESSIONS_DIRECTORY = "sessions";
const SESSION_RECORD = /^[a-f0-9]{64}$/;
const MUTATION_LOCK_TTL_MS = 5 * 60_000;

async function removeFileIfPresent(file) {
  try {
    const stat = await lstat(file);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`${path.basename(file)} is not a regular file`);
    await unlink(file);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

function lockCreatedAt(raw, modifiedAt) {
  try {
    const value = JSON.parse(raw);
    return value?.version === 1 && typeof value.createdAt === "number" && Number.isFinite(value.createdAt)
      ? value.createdAt
      : modifiedAt;
  } catch {
    return modifiedAt;
  }
}

async function clearStaleLock(lockPath) {
  let raw;
  let modifiedAt;
  try {
    [raw, modifiedAt] = await Promise.all([readFile(lockPath, "utf8"), lstat(lockPath).then((stat) => stat.mtimeMs)]);
  } catch {
    return false;
  }
  if (Date.now() - lockCreatedAt(raw, modifiedAt) < MUTATION_LOCK_TTL_MS) return false;
  if ((await readFile(lockPath, "utf8").catch(() => "")) !== raw) return false;
  try {
    await unlink(lockPath);
    return true;
  } catch (error) {
    return error?.code === "ENOENT";
  }
}

async function acquireResetLock(authDirectory) {
  const lockPath = path.join(authDirectory, LOCAL_AUTH_LOCK);
  const lock = { version: 1, owner: randomBytes(16).toString("hex"), createdAt: Date.now() };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      await writeFile(lockPath, JSON.stringify(lock), { encoding: "utf8", flag: "wx", mode: 0o600 });
      return { lockPath, lock };
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      if (attempt || !(await clearStaleLock(lockPath))) throw new Error("local auth mutation is in progress; retry after it finishes");
    }
  }
  throw new Error("local auth mutation lock could not be acquired");
}

async function releaseResetLock(lockPath, lock) {
  try {
    const owner = JSON.parse(await readFile(lockPath, "utf8"));
    if (owner?.owner === lock.owner) await unlink(lockPath).catch(() => undefined);
  } catch {
    // A newer lock or an interrupted reset must not be removed by this process.
  }
}

/** Remove only the fixed auth state and hash-named session records below one known state directory. */
export async function resetKnownLocalAuth(authDirectory) {
  try {
    const stat = await lstat(authDirectory);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error("auth directory is not a directory");
  } catch (error) {
    if (error?.code === "ENOENT") return 0;
    throw error;
  }
  const { lockPath, lock } = await acquireResetLock(authDirectory);
  try {
    let removed = Number(await removeFileIfPresent(path.join(authDirectory, LOCAL_AUTH_STATE)));
    const sessionsPath = path.join(authDirectory, SESSIONS_DIRECTORY);
    try {
      const sessionsStat = await lstat(sessionsPath);
      if (!sessionsStat.isDirectory() || sessionsStat.isSymbolicLink()) throw new Error("sessions is not a directory");
    } catch (error) {
      if (error?.code === "ENOENT") return removed;
      throw error;
    }
    for (const entry of await readdir(sessionsPath, { withFileTypes: true })) {
      if (SESSION_RECORD.test(entry.name) && entry.isFile()) {
        removed += Number(await removeFileIfPresent(path.join(sessionsPath, entry.name)));
      }
    }
    return removed;
  } finally {
    await releaseResetLock(lockPath, lock);
  }
}

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exitCode = 64;
}

async function main() {
  if (process.env.KSI_CCTV_WEB_AUTH_DIR && process.env.KSI_CCTV_WEB_AUTH_DIR !== AUTH_DIRECTORY) {
    fail("Refusing to reset an auth directory other than /var/lib/ksi-cctv-web.");
  } else if (process.argv.length !== 3 || process.argv[2] !== "--confirm") {
    fail("Usage: ksi-cctv-reset-local-auth --confirm");
  } else {
    try {
      const removed = await resetKnownLocalAuth(AUTH_DIRECTORY);
      process.stdout.write(`Reset local web authentication state (${removed} record${removed === 1 ? "" : "s"} removed).\n`);
      process.stdout.write("When no environment password is configured, the console is open again.\n");
    } catch (error) {
      process.stderr.write(`Could not reset local web authentication state: ${error instanceof Error ? error.message : "unknown error"}\n`);
      process.exitCode = 1;
    }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) void main();
