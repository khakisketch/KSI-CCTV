import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptsDirectory = path.dirname(fileURLToPath(import.meta.url));
const resetTool = path.join(scriptsDirectory, "reset-local-auth.mjs");
const frontendRoot = path.resolve(scriptsDirectory, "..");

function run(args, env = {}) {
  return spawnSync(process.execPath, [resetTool, ...args], {
    encoding: "utf8",
    env: { ...process.env, ...env },
  });
}

test("local auth reset requires explicit confirmation", () => {
  const result = run([]);
  assert.equal(result.status, 64);
  assert.match(result.stderr, /--confirm/);
});

test("local auth reset refuses an arbitrary auth directory without exposing its value", () => {
  const result = run(["--confirm"], { KSI_CCTV_WEB_AUTH_DIR: "/tmp/not-auth-state" });
  assert.equal(result.status, 64);
  assert.match(result.stderr, /Refusing to reset an auth directory/);
  assert.doesNotMatch(result.stderr, /not-auth-state/);
});

test("reset implementation removes only canonical auth and session records", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "ksi-local-auth-reset-"));
  const sessions = path.join(directory, "sessions");
  const sessionName = "a".repeat(64);
  try {
    await mkdir(sessions);
    await writeFile(path.join(directory, "auth.json"), "{}", { mode: 0o600 });
    await writeFile(path.join(sessions, sessionName), "1", { mode: 0o600 });
    await writeFile(path.join(sessions, "keep.txt"), "keep", { mode: 0o600 });
    const { resetKnownLocalAuth } = await import(`${pathToFileURL(resetTool).href}?test-reset`);

    assert.equal(await resetKnownLocalAuth(directory), 2);
    await assert.rejects(readFile(path.join(directory, "auth.json"), "utf8"));
    await assert.rejects(readFile(path.join(sessions, sessionName), "utf8"));
    assert.equal(await readFile(path.join(sessions, "keep.txt"), "utf8"), "keep");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("reset refuses a fresh active auth mutation but recovers a stale lock", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "ksi-local-auth-lock-"));
  try {
    await writeFile(path.join(directory, "auth.json.lock"), JSON.stringify({ version: 1, owner: "a".repeat(32), createdAt: Date.now() }), { mode: 0o600 });
    const { resetKnownLocalAuth } = await import(`${pathToFileURL(resetTool).href}?test-lock`);

    await assert.rejects(resetKnownLocalAuth(directory), /mutation is in progress/);
    await writeFile(path.join(directory, "auth.json.lock"), JSON.stringify({ version: 1, owner: "a".repeat(32), createdAt: 0 }), { mode: 0o600 });
    assert.equal(await resetKnownLocalAuth(directory), 0);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("web image and compose declare the fixed persistent owner-only auth directory", async () => {
  const [dockerfile, entrypoint, compose] = await Promise.all([
    readFile(path.join(frontendRoot, "Dockerfile"), "utf8"),
    readFile(path.join(frontendRoot, "docker-entrypoint.sh"), "utf8"),
    readFile(path.join(frontendRoot, "..", "docker-compose.yml"), "utf8"),
  ]);
  assert.match(entrypoint, /KSI_CCTV_WEB_AUTH_DIR=\/var\/lib\/ksi-cctv-web/);
  assert.match(dockerfile, /chmod 0700 \/var\/lib\/ksi-cctv-web/);
  assert.match(dockerfile, /USER nextjs/);
  assert.match(compose, /KSI_CCTV_WEB_AUTH_DIR: \/var\/lib\/ksi-cctv-web/);
  assert.match(compose, /ksi-cctv-web-auth:\/var\/lib\/ksi-cctv-web/);
  assert.match(compose, /^  ksi-cctv-web-auth:$/m);
});
