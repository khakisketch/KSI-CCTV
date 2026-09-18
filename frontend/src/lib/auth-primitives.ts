import { chmod, lstat, mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { randomBytes, scrypt as scryptCallback, timingSafeEqual } from "node:crypto";
import { promisify } from "node:util";

const scrypt = promisify(scryptCallback);

export const MIN_PASSWORD_LENGTH = 12;
export const MAX_PASSWORD_LENGTH = 1024;
export const MUTATION_LOCK_TTL_MS = 5 * 60_000;

export type MutationLock = {
  version: 1;
  owner: string;
  createdAt: number;
};

export function passwordValidationError(password: unknown): string | undefined {
  if (typeof password !== "string" || !password.trim()) return "비밀번호를 입력하세요.";
  if (password !== password.trim()) return "비밀번호 앞뒤에는 공백을 사용할 수 없습니다.";
  if (password.length < MIN_PASSWORD_LENGTH) return `비밀번호는 ${MIN_PASSWORD_LENGTH}자 이상이어야 합니다.`;
  if (password.length > MAX_PASSWORD_LENGTH) return `비밀번호는 ${MAX_PASSWORD_LENGTH}자 이하여야 합니다.`;
  return undefined;
}

export async function passwordHash(password: string, salt: Buffer): Promise<Buffer> {
  return Buffer.from((await scrypt(password, salt, 64)) as Uint8Array);
}

export function timingSafeCompare(a: Buffer, b: Buffer): boolean {
  return a.length === b.length && timingSafeEqual(a, b);
}

export function randomHex(bytes: number): string {
  return randomBytes(bytes).toString("hex");
}

export async function prepareDirectory(directory: string): Promise<string> {
  await mkdir(directory, { recursive: true, mode: 0o700 });
  await chmod(directory, 0o700);
  return directory;
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

async function clearStaleMutationLock(lockPath: string): Promise<boolean> {
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

export async function withFileLockMutation<T>(
  directory: string,
  lockPath: string,
  mutation: () => Promise<T>,
): Promise<T | "conflict"> {
  await prepareDirectory(directory);
  const lock: MutationLock = { version: 1, owner: randomHex(16), createdAt: Date.now() };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      await writeFile(lockPath, JSON.stringify(lock), { encoding: "utf8", flag: "wx", mode: 0o600 });
      break;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      if (attempt || !(await clearStaleMutationLock(lockPath))) return "conflict";
    }
  }
  try {
    return await mutation();
  } finally {
    const owner = parseMutationLock(await readFile(lockPath, "utf8").catch(() => ""));
    if (owner?.owner === lock.owner) await unlink(lockPath).catch(() => undefined);
  }
}

export async function writeFileAtomic(filePath: string, content: string): Promise<void> {
  const temporary = `${filePath}.${randomHex(8)}.tmp`;
  await writeFile(temporary, content, { encoding: "utf8", flag: "wx", mode: 0o600 });
  await chmod(temporary, 0o600);
  await rename(temporary, filePath);
  await chmod(filePath, 0o600);
}
