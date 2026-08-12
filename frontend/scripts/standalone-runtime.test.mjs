import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { lstat, readlink, symlink } from "node:fs/promises";
import { createServer, request as httpRequest } from "node:http";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function ensureLink(linkPath, target) {
  try {
    const stat = await lstat(linkPath);
    assert(stat.isSymbolicLink());
    assert.equal(await readlink(linkPath), target);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    await symlink(target, linkPath, "dir");
  }
}

async function unusedPort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert(address && typeof address === "object");
  await new Promise((resolve) => server.close(resolve));
  return address.port;
}

function request(port, pathname) {
  return new Promise((resolve, reject) => {
    const req = httpRequest({ hostname: "127.0.0.1", port, path: pathname }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => resolve({
        status: response.statusCode,
        headers: response.headers,
        body: Buffer.concat(chunks).toString("utf8"),
      }));
    });
    req.setTimeout(15_000, () => req.destroy(new Error("request timed out")));
    req.on("error", reject);
    req.end();
  });
}

async function waitForResponse(child, output, port, pathname) {
  const deadline = Date.now() + 30_000;
  let lastError;
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(`standalone server exited early:\n${output()}`);
    }
    try {
      return await request(port, pathname);
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  throw new Error(`standalone server did not respond: ${lastError}\n${output()}`);
}

async function stopProcess(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((resolve) => child.once("exit", resolve));
  try {
    if (process.platform === "win32") child.kill("SIGTERM");
    else process.kill(-child.pid, "SIGTERM");
  } catch {
    return;
  }
  await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 3_000))]);
  if (child.exitCode === null && child.signalCode === null) {
    if (process.platform === "win32") child.kill("SIGKILL");
    else process.kill(-child.pid, "SIGKILL");
    await exited;
  }
}

test("guarded npm start serves the standalone build with authentication", { timeout: 60_000 }, async () => {
  const port = await unusedPort();
  const alternateBuildDir = process.env.KSI_CCTV_TEST_STANDALONE_BUILD_DIR;
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (key.startsWith("KSI_CCTV_") || key.startsWith("CCTV_CAM_")) delete env[key];
  }
  Object.assign(env, {
    KSI_CCTV_API_KEY: "a".repeat(64),
    KSI_CCTV_ENGINE_URL: "http://127.0.0.1:9",
    KSI_CCTV_WEB_PASSWORD: "standalone-runtime-test-password",
    KSI_CCTV_WEB_SECRET: "b".repeat(64),
    NEXT_TELEMETRY_DISABLED: "1",
  });

  let cwd = frontendRoot;
  let command = [
    path.join(frontendRoot, "next-with-auth-guard.mjs"),
    "start",
    "-H127.0.0.1",
    `-p${port}`,
  ];
  if (alternateBuildDir) {
    const buildRoot = path.resolve(frontendRoot, alternateBuildDir);
    const standaloneRoot = path.join(buildRoot, "standalone");
    const nestedBuildRoot = path.join(standaloneRoot, path.basename(buildRoot));
    await ensureLink(
      path.join(nestedBuildRoot, "static"),
      path.relative(nestedBuildRoot, path.join(buildRoot, "static")),
    );
    await ensureLink(
      path.join(standaloneRoot, "public"),
      path.relative(standaloneRoot, path.join(frontendRoot, "public")),
    );
    cwd = standaloneRoot;
    command = [path.join(standaloneRoot, "server.js")];
    Object.assign(env, {
      HOSTNAME: "127.0.0.1",
      KSI_CCTV_WEB_BIND_HOST: "127.0.0.1",
      NODE_ENV: "production",
      PORT: String(port),
    });
  }

  const child = spawn(
    process.execPath,
    command,
    {
      cwd,
      detached: process.platform !== "win32",
      env,
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let processOutput = "";
  const capture = (chunk) => {
    processOutput = `${processOutput}${chunk}`.slice(-16_384);
  };
  child.stdout.on("data", capture);
  child.stderr.on("data", capture);

  try {
    const login = await waitForResponse(child, () => processOutput, port, "/login");
    assert.equal(login.status, 200, processOutput);
    assert.match(login.body, /KSI-CCTV/);

    const assetPath = login.body.match(/\/_next\/static\/[^"']+\.(?:css|js)/)?.[0];
    assert(assetPath, "login page did not reference a static asset");
    const asset = await request(port, assetPath);
    assert.equal(asset.status, 200, processOutput);

    const protectedPage = await request(port, "/");
    assert([307, 308].includes(protectedPage.status));
    assert.match(String(protectedPage.headers.location), /\/login$/);
  } finally {
    await stopProcess(child);
  }
});
